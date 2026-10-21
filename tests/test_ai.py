import asyncio
import io
import json

import httpx
import pytest

from paperpilot.chat import chat_context, chat_question, completed_history
from paperpilot.config import Config
from paperpilot.providers import Providers
from paperpilot.providers import ProviderError
from reportlab.pdfgen import canvas
from paperpilot.semantic import compose, validate_groups
from test_reader import environment, upload, wait_ready, wait_jobs, configure


def test_unlimited_usage_allows_all_ai_features_above_old_cap(environment):
    client, app, requests, *_ = environment
    doc = upload(client)
    state = wait_ready(client, doc)
    configure(client)
    user_id = client.get('/api/session').json()['user']['id']
    app.state.jobs.charge(user_id, 210_000)
    assert client.get('/api/usage').json()['limit'] == 0
    operations = [('translate', {'engine':'ai','paragraph_ids':[state['paragraphs'][0]['id']]}),
                  ('ai-reflow', None), ('chat', {'question':'你好，请概括这篇研究。'})]
    for action, payload in operations:
        assert client.post(f'/api/documents/{doc}/{action}', json=payload).status_code == 202
        result = wait_jobs(client, doc)
        assert result['jobs'][0]['status'] == 'done', result['jobs'][0]
    assert len(requests) >= 3
    assert client.get('/api/usage').json()['characters'] > 210_000
    assert result['messages'][-1]['status'] == 'done'


@pytest.mark.parametrize('action,payload', [('ai-reflow', None), ('chat', {'question':'研究方法是什么？'})])
def test_explicit_quota_still_blocks_ai_features(environment, action, payload):
    client, app, requests, *_ = environment
    doc = upload(client); wait_ready(client, doc); configure(client)
    app.state.config.daily_text_limit = 1
    assert client.post(f'/api/documents/{doc}/{action}', json=payload).status_code == 202
    result = wait_jobs(client, doc)
    assert result['jobs'][0]['status'] == 'failed'
    assert '本站限额' in result['jobs'][0]['error']
    assert requests == []


def test_default_env_quota_is_unlimited(monkeypatch):
    monkeypatch.delenv('DAILY_TEXT_LIMIT', raising=False)
    assert Config.from_env().daily_text_limit == 0
    monkeypatch.setenv('DAILY_TEXT_LIMIT', '12345')
    assert Config.from_env().daily_text_limit == 12345


def test_compact_reflow_boundaries_preserve_every_line():
    units = [{'kind':'body'}] * 5
    assert validate_groups('{"starts":[0,2,3]}', units) == [[0,1],[2],[3,4]]
    for starts in [[1], [0,0], [0,3,2], [0,5], [False], [0,'2'], []]:
        with pytest.raises(ValueError):
            validate_groups(json.dumps({'starts':starts}), units)
    assert validate_groups('{"starts":[0]}', [{'kind':'figure'}, {'kind':'body'}]) == [[0],[1]]


def test_reflow_keeps_protected_block_labels_and_assets_exact():
    unit={'kind':'equation','text':'Formula label ', 'page':1,'location':{'page':1,'bbox':[10,20,30,40]},'content':{'asset':'synthetic.png'}}
    result=compose([[0]],[unit])[0]
    assert result['text']==unit['text'] and result['locations']==[unit['location']]
    assert result['content']==unit['content']


def test_reflow_retries_one_invalid_response_without_changing_source(environment):
    client, app, _, *_ = environment
    doc = upload(client); before = wait_ready(client, doc); configure(client)
    source = ' '.join(p['text'] for p in before['paragraphs'])
    calls = []
    original = app.state.jobs.providers.ai

    async def response(settings, messages, charge, **kwargs):
        calls.append(messages[0]['content'])
        if len(calls) == 1:
            return '{"starts":[2]}'
        return '{"starts":[0]}'

    app.state.jobs.providers.ai = response
    try:
        assert client.post(f'/api/documents/{doc}/ai-reflow').status_code == 202
        result = wait_jobs(client, doc)
    finally:
        app.state.jobs.providers.ai = original
    assert result['jobs'][0]['status'] == 'done'
    assert 'previous response failed' in calls[1]
    assert ' '.join(p['text'] for p in result['paragraphs']) == source


def test_chinese_method_question_retrieves_method_section():
    rows=[]
    def add(kind, text):
        rows.append({'id':str(len(rows)), 'ordinal':len(rows), 'page':len(rows)//5+1, 'kind':kind, 'text':text})
    add('title','Synthetic scientific article')
    add('authors','Author Names, Institute')
    for i in range(10):
        add('body','General background material without specific technical detail. '*12)
    add('heading','2. METHODOLOGY')
    method_index=len(rows)
    add('body','Our proposed algorithm uses paired observations to estimate displacement. '*15)
    for i in range(15):
        add('body','Further discussion without the requested technical information. '*12)
    evidence, excerpts=chat_context(rows,'这项研究的方法是什么？',budget=3000)
    assert rows[method_index]['id'] in {p['id'] for p in evidence}
    assert 'Author Names' not in '\n'.join(excerpts)
    prompt=chat_question(excerpts,'这项研究的方法是什么？')
    assert prompt.rindex('用户问题：') > prompt.index('Our proposed algorithm')


def test_chat_history_omits_failed_and_orphaned_questions():
    rows=[{'role':role,'status':status,'text':text} for role,status,text in [
        ('user','done','Old successful question'), ('assistant','done','Answer'),
        ('user','done','Failed question'), ('assistant','error','Local quota error'),
        ('user','done','Orphaned question')]]
    assert completed_history(rows) == [{'role':'user','content':'Old successful question'}, {'role':'assistant','content':'Answer'}]


def test_chat_does_not_reuse_citations_from_an_old_layout():
    rows=[{'role':'user','status':'done','text':'Explain this paper'},
          {'role':'assistant','status':'done','text':'An old answer [P92]','evidence':'[{"id":"old-paragraph"}]'}]
    assert completed_history(rows,{'new-paragraph'}) == []
    assert len(completed_history(rows,{'old-paragraph'})) == 2


def test_glm47_reflow_uses_json_mode_and_keeps_selected_model(tmp_path):
    captured=[]
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"starts":[0]}'}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider=Providers(client,Config(data_dir=tmp_path))
            settings={'ai':{'provider':'zhipu','model':'glm-4.7-flash','base_url':'https://open.bigmodel.cn/api/paas/v4','api_key':'synthetic-test-key'}}
            await provider.ai(settings,[{'role':'user','content':'Return JSON.'}],lambda n:None,json_output=True)
    asyncio.run(run())
    assert captured[0]['model'] == 'glm-4.7-flash'
    assert captured[0]['response_format'] == {'type':'json_object'}
    assert captured[0]['thinking'] == {'type':'disabled'}


@pytest.mark.parametrize('status',[429,500])
def test_ai_rate_limit_waits_and_retries_without_switching_model(tmp_path, monkeypatch, status):
    waits=[];calls=[];charged=[]
    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr('paperpilot.providers.asyncio.sleep',sleep)
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls)<3:
            return httpx.Response(status,headers={'Retry-After':'3'},json={'error':{'code':'1302' if status==429 else '500'}})
        return httpx.Response(200,json={'choices':[{'message':{'content':'Recovered response'}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider=Providers(client,Config(data_dir=tmp_path))
            settings={'ai':{'provider':'zhipu','model':'glm-4.7-flash','base_url':'https://open.bigmodel.cn/api/paas/v4','api_key':'synthetic-test-key'}}
            assert await provider.ai(settings,[{'role':'user','content':'hello'}],charged.append) == 'Recovered response'
    asyncio.run(run())
    assert waits == [3,3] and charged == [5,5,5]
    assert all(c['model']=='glm-4.7-flash' for c in calls)


def test_failed_reflow_resumes_validated_batches_without_repeating_calls(environment):
    client, app, *_ = environment
    output=io.BytesIO();pdf=canvas.Canvas(output,invariant=True)
    for page in range(2):
        pdf.setFont('Times-Bold',19);pdf.drawString(50,770,f'Synthetic page {page+1}')
        pdf.setFont('Times-Roman',11)
        pdf.drawString(50,720,f'This first paragraph on page {page+1} describes a reproducible method.')
        pdf.drawString(50,680,'The second paragraph explains the controlled software experiment.')
        pdf.showPage()
    pdf.save()
    doc=upload(client,output.getvalue());before=wait_ready(client,doc);configure(client)
    calls=[];original=app.state.jobs.providers.ai
    async def response(settings,messages,charge,**kwargs):
        calls.append(messages[-1]['content'])
        if len(calls)==2:
            raise ProviderError('Synthetic provider busy (429).')
        return '{"starts":[0]}'
    app.state.jobs.providers.ai=response
    try:
        client.post(f'/api/documents/{doc}/ai-reflow');failed=wait_jobs(client,doc)
        assert failed['jobs'][0]['status']=='failed' and '批次已保存' in failed['jobs'][0]['error']
        assert failed['paragraphs']==before['paragraphs']
        assert app.state.db.one('SELECT COUNT(*) AS n FROM reflow_cache WHERE document_id=?',(doc,))['n']==1
        client.post(f'/api/documents/{doc}/ai-reflow');after=wait_jobs(client,doc)
    finally:
        app.state.jobs.providers.ai=original
    assert after['jobs'][0]['status']=='done'
    assert len(calls)==3 and calls[1]==calls[2]
    assert app.state.db.one('SELECT COUNT(*) AS n FROM reflow_cache WHERE document_id=?',(doc,))['n']==0
    assert after['has_previous_layout']


@pytest.mark.parametrize('code,expected,calls_expected',[('1113','智谱账户欠费',1),('1305','智谱当前模型服务繁忙',4)])
def test_zhipu_reports_overload_separately_from_balance(tmp_path,monkeypatch,code,expected,calls_expected):
    seen=[]
    async def sleep(_seconds):
        pass
    monkeypatch.setattr('paperpilot.providers.asyncio.sleep',sleep)
    def handler(request):
        seen.append(request)
        return httpx.Response(429,json={'error':{'code':code,'message':'Do not echo this raw body or synthetic-key.'}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p=Providers(client,Config(data_dir=tmp_path))
            with pytest.raises(ProviderError,match=expected) as error:
                await p.request('https://open.bigmodel.cn/api/paas/v4/chat/completions',charge=lambda n:None,characters=1,payload={},max_attempts=4)
            assert code in str(error.value) and 'synthetic-key' not in str(error.value)
    asyncio.run(run())
    assert len(seen)==calls_expected
