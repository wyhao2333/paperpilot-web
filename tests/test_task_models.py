import asyncio
import json
import threading
import time

import httpx

from paperpilot.ai_config import ai_for
from paperpilot.config import Config
from paperpilot.providers import Providers, ProviderError, translation_signature
from test_reader import environment, configure, pdf_bytes, upload, wait_jobs, wait_ready


def test_roles_reuse_credentials_but_route_and_cache_independently(environment):
    client, app, requests, *_ = environment
    doc = upload(client)
    before = wait_ready(client, doc)
    configure(client)
    settings = client.get('/api/settings').json()
    assert settings['ai_tasks']['translation']['model'] == 'synthetic-model'
    body = {
        'ai': {k: v for k, v in settings['ai'].items() if k != 'has_key'},
        'ai_profiles': {'zhipu': {'provider': 'zhipu', 'base_url': 'https://open.bigmodel.cn/api/paas/v4',
                                'model': 'glm-4.7-flash', 'api_key': 'synthetic-translation-secret'}},
        'ai_tasks': {'translation': {'provider': 'zhipu', 'model': 'glm-4-flash'},
                     'chat': {'provider': 'test-fixture', 'model': 'qa-model'}},
        'translation_concurrency': 3,
    }
    response = client.put('/api/settings', json=body)
    assert response.status_code == 200, response.text
    assert 'synthetic-translation-secret' not in response.text
    assert response.json()['ai_profiles']['zhipu']['has_key']
    target = before['paragraphs'][0]['id']
    client.post(f'/api/documents/{doc}/translate', json={'engine': 'ai', 'paragraph_ids': [target]})
    after = wait_jobs(client, doc)
    assert after['paragraphs'][0]['translation']['status'] == 'done'
    assert json.loads(requests[-1].content)['model'] == 'glm-4-flash'
    assert requests[-1].headers['authorization'] == 'Bearer synthetic-translation-secret'

    body['ai_profiles']['zhipu']['api_key'] = None
    body['ai_tasks']['chat']['model'] = 'different-qa-model'
    body['translation_concurrency'] = 2
    assert client.put('/api/settings', json=body).status_code == 200
    assert client.get(f'/api/documents/{doc}').json()['paragraphs'][0]['translation']['status'] == 'done'
    client.post(f'/api/documents/{doc}/chat', json={'question': '研究方法是什么？'})
    assert wait_jobs(client, doc)['messages'][-1]['status'] == 'done'
    assert json.loads(requests[-1].content)['model'] == 'different-qa-model'
    assert requests[-1].headers['authorization'] != 'Bearer synthetic-translation-secret'
    # Reflow shares the explicit question/reflow role, never the translation role.
    client.post(f'/api/documents/{doc}/ai-reflow')
    assert wait_jobs(client, doc)['jobs'][0]['status'] == 'done'
    assert json.loads(requests[-1].content)['model'] == 'different-qa-model'

    body['ai_tasks']['translation']['model'] = 'glm-4-flash-250414'
    assert client.put('/api/settings', json=body).status_code == 200
    assert all(p['translation'] is None for p in client.get(f'/api/documents/{doc}').json()['paragraphs'])
    # Changing an endpoint never carries the old key to that new endpoint.
    body['ai_profiles']['zhipu']['base_url'] = 'http://localhost:9998/v1'
    changed = client.put('/api/settings', json=body).json()
    assert changed['ai_profiles']['zhipu']['has_key'] is False
    assert client.post(f'/api/documents/{doc}/translate', json={'engine': 'ai'}).status_code == 400


def test_legacy_model_is_preserved_until_roles_are_assigned(environment):
    client, app, *_ = environment
    configure(client)
    user_id = client.get('/api/session').json()['user']['id']
    saved = app.state.vault.get(app.state.db, user_id)
    assert saved['ai_tasks'] == {}
    assert ai_for(saved, 'translation') == ai_for(saved, 'chat') == saved['ai']
    key = translation_signature(saved, 'ai', 'Source text')
    saved['ai_tasks'] = {'translation': {'provider': 'test-fixture', 'model': 'synthetic-model'},
                         'chat': {'provider': 'test-fixture', 'model': 'other-model'}}
    assert translation_signature(saved, 'ai', 'Source text') == key
    for value in [0, 17, -1]:
        assert client.put('/api/settings', json={'ai': saved['ai'], 'translation_concurrency': value}).status_code == 422


def test_shared_concurrency_cancel_failure_and_reserved_chat_worker(environment):
    client, app, *_ = environment
    doc1 = upload(client, name='first.pdf')
    state1 = wait_ready(client, doc1)
    doc2 = upload(client, data=pdf_bytes('A second document with independent translation results.'), name='second.pdf')
    state2 = wait_ready(client, doc2)
    configure(client)
    settings = client.get('/api/settings').json()
    assert client.put('/api/settings', json={'ai': settings['ai'], 'translation_concurrency': 2}).status_code == 200
    released, entered = threading.Event(), threading.Event()
    active = peak = 0
    calls = []
    original = app.state.jobs.providers.translate

    async def slow_translate(settings, engine, text, context, charge):
        nonlocal active, peak
        charge(len(text))
        calls.append(text)
        active += 1
        peak = max(peak, active)
        if active == 2:
            entered.set()
        try:
            while not released.is_set():
                await asyncio.sleep(.02)
            if text == state2['paragraphs'][-1]['text']:
                raise ProviderError('Synthetic single paragraph failure')
            return 'Synthetic translated text'
        finally:
            active -= 1

    app.state.jobs.providers.translate = slow_translate
    try:
        job1 = client.post(f'/api/documents/{doc1}/translate', json={'engine': 'ai'}).json()['job_id']
        client.post(f'/api/documents/{doc2}/translate', json={'engine': 'ai'})
        assert entered.wait(5)
        assert peak == 2
        # Both document workers are occupied, yet the answer completes.
        client.post(f'/api/documents/{doc2}/chat', json={'question': '解释研究方法'})
        end = time.monotonic() + 5
        while time.monotonic() < end:
            messages = client.get(f'/api/documents/{doc2}').json()['messages']
            if messages and messages[-1]['status'] == 'done':
                break
            time.sleep(.03)
        assert messages[-1]['status'] == 'done'
        assert client.post(f'/api/jobs/{job1}/cancel').status_code == 200
        released.set()
        result1, result2 = wait_jobs(client, doc1), wait_jobs(client, doc2)
    finally:
        released.set()
        app.state.jobs.providers.translate = original
    assert peak == 2
    assert result1['jobs'][0]['status'] == 'cancelled'
    assert result1['jobs'][0]['completed'] < len(state1['paragraphs'])
    translated = [p['translation'] for p in result2['paragraphs']]
    assert any(t and t['status'] == 'done' for t in translated)
    assert any(t and t['status'] == 'error' for t in translated)
    assert not app.state.db.one("SELECT id FROM translations WHERE status='running'")


def test_zhipu_requests_are_no_longer_globally_serialized(tmp_path):
    peak = active = 0

    async def handler(request):
        nonlocal peak, active
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(.03)
            return httpx.Response(200, json={'choices': [{'message': {'content': 'Synthetic response'}}]})
        finally:
            active -= 1

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = Providers(client, Config(data_dir=tmp_path, allow_local_api=True))
            settings = {'ai': {'provider': 'zhipu', 'model': 'glm-4-flash',
                              'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'api_key': 'synthetic-key'}}
            await asyncio.gather(*(p.translate(settings, 'ai', 'Synthetic source.', '', lambda n: None) for _ in range(4)))
    asyncio.run(run())
    assert peak == 4
