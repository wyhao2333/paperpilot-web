import asyncio
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from paperpilot.config import Config
from paperpilot.main import create_app
from paperpilot.providers import Providers, ProviderError
from paperpilot.reflow import parse_pdf
from paperpilot.security import DEFAULT_SETTINGS
from paperpilot.semantic import validate_groups
from test_reader import environment, upload, wait_ready, wait_jobs, configure, TEST_KEY


def test_local_admin_without_registration_and_public_boundary(tmp_path):
    app=create_app(Config(data_dir=tmp_path/'local'))
    with TestClient(app,base_url='http://localhost:8765',client=('127.0.0.1',1234),headers={'X-PaperPilot':'1'}) as client:
        assert client.get('/api/session',headers={'Host':'reader.example.org'}).json()['user'] is None
        assert app.state.db.one('SELECT COUNT(*) AS n FROM users')['n']==0
        result=client.get('/api/session').json()
        assert result['local_admin'] and result['user']['role']=='admin'
        assert app.state.db.one('SELECT COUNT(*) AS n FROM users')['n']==1
        assert client.get('/api/session').json()['user']['id']==result['user']['id']
        assert client.post('/api/setup',json={}).status_code in {404,405}
    public=create_app(Config(data_dir=tmp_path/'public',origin='https://reader.example.org'))
    with TestClient(public,base_url='http://localhost:8765',client=('127.0.0.1',1234)) as client:
        assert client.get('/api/session').json()['user'] is None


def test_vector_figures_math_fragments_and_table_order(tmp_path):
    path=tmp_path/'source.pdf';c=canvas.Canvas(str(path),pagesize=(612,792),invariant=1)
    c.setFont('Times-Bold',19);c.drawString(45,748,'Synthetic scholarly layout')
    c.setFont('Times-Roman',11)
    for i,line in enumerate(['This first paragraph starts in the left column.', 'The source lines belong to a complete sentence.', 'Mathematical symbols keep their original positions.', 'The last line ends this first paragraph.']):
        c.drawString(45,710-i*14,line)
    for i,line in enumerate(['The right column starts after the left column.', 'Its lines must never interleave with the left text.', 'Source coordinates stay available to the reader.', 'This final sentence ends the second paragraph.']):
        c.drawString(330,710-i*14,line)
    c.drawString(45,620,'The input variable is ');x=45+c.stringWidth('The input variable is ','Times-Roman',11)
    c.drawString(x,620,'x');c.setFont('Times-Roman',7);c.drawString(x+5,625,'2');c.setFont('Times-Roman',11);c.drawString(x+11,620,' in this demonstration.')
    c.drawString(45,583,'x + y = 1');c.drawString(265,583,'(1)')
    c.drawString(45,565,'Q QG Q')
    c.rect(55,390,200,130);c.line(55,390,240,502);c.circle(120,460,14)
    c.setFont('Times-Roman',9);c.drawString(60,373,'Fig. 1. A vector-only synthetic illustration.')
    c.drawString(330,520,'Table 1')
    for y in [502,480,430]:c.line(330,y,565,y)
    c.drawString(335,488,'Variable     Value');c.drawString(335,462,'x                 1')
    c.showPage()
    c.setFont('Times-Bold',12);c.drawString(45,748,'References')
    c.setFont('Times-Roman',9)
    c.drawString(45,718,'[1] A. Author, A scientific article about control methods,')
    c.drawString(65,706,'Journal of Applied Control Systems,')
    c.drawString(65,694,'17 (8) (2019) 1957-1970.')
    c.drawString(45,682,'[2] B. Author, A second scientific article about observers,')
    c.drawString(65,670,'Systems and Control Letters 134 (2019) 104541.')
    c.save()
    assets=tmp_path/'assets';result=parse_pdf(path,assets_dir=assets);ps=result['paragraphs']
    text=' '.join(p['text'] for p in ps)
    assert text.index('The last line ends')<text.index('The right column starts')
    assert len([p for p in ps if p['kind']=='figure'])==1
    assert len([p for p in ps if p['kind']=='table'])==1
    figure=next(i for i,p in enumerate(ps) if p['kind']=='figure')
    caption=next(i for i,p in enumerate(ps) if p['text'].startswith('Fig. 1.'))
    assert figure<caption
    assert any(p['kind']=='equation' and '(1)' in p['text'] for p in ps)
    assert not any(p['kind']=='body' and p['text']=='Q QG Q' for p in ps)
    references=[p for p in ps if p['page']==2 and p['kind']!='heading']
    assert len(references)==2 and all(p['kind']=='body' for p in references)
    assert '1957-1970' in references[0]['text'] and references[1]['text'].startswith('[2]')
    inline=[part for p in ps for part in p['content'].get('parts',[]) if part['type']=='math']
    assert inline and 'PPMATH' in text
    for item in inline+[p['content'] for p in ps if p['content'].get('asset')]:
        assert (assets/item['asset']).read_bytes().startswith(b'\x89PNG')


def test_reparse_and_restore_preserves_notes_and_old_translations(environment):
    client,app,*_=environment
    doc=upload(client);before=wait_ready(client,doc);configure(client)
    client.put(f'/api/documents/{doc}/notes',json={'text':'Keep my original notes.'})
    client.post(f'/api/documents/{doc}/translate',json={'engine':'ai'})
    before=wait_jobs(client,doc)
    assert client.post(f'/api/documents/{doc}/reparse').status_code==202
    after=wait_jobs(client,doc)
    assert after['has_previous_layout'] and after['notes']['text']=='Keep my original notes.'
    assert after['paragraphs'][0]['id']!=before['paragraphs'][0]['id']
    assert client.post(f'/api/documents/{doc}/restore-layout').status_code==200
    restored=client.get(f'/api/documents/{doc}').json()
    assert restored['paragraphs'][0]['id']==before['paragraphs'][0]['id']
    assert restored['paragraphs'][0]['translation']['text']==before['paragraphs'][0]['translation']['text']
    assert restored['notes']['text']=='Keep my original notes.'


def test_ai_reflow_rejects_missing_source_and_preserves_document(environment):
    client,app,requests,mode,*_=environment
    doc=upload(client);before=wait_ready(client,doc);configure(client)
    mode['bad_reflow']=True
    assert client.post(f'/api/documents/{doc}/ai-reflow').status_code==202
    after=wait_jobs(client,doc)
    assert after['jobs'][0]['status']=='failed' and '完整性' in after['jobs'][0]['error']
    assert after['paragraphs']==before['paragraphs']
    mode['bad_reflow']=False
    assert client.post(f'/api/documents/{doc}/ai-reflow').status_code==202
    after=wait_jobs(client,doc)
    assert after['jobs'][0]['status']=='done' and after['has_previous_layout']
    assert len(after['paragraphs'])>=len(before['paragraphs'])
    assert validate_groups('{"groups":[[0],[1]]}',[{'kind':'body'},{'kind':'body'}])==[[0],[1]]
    with pytest.raises(ValueError):
        validate_groups('{"groups":[[0,1]]}',[{'kind':'figure'},{'kind':'body'}])


def test_provider_keys_never_carry_to_other_hosts(environment):
    client,app,*_=environment;configure(client)
    saved=client.get('/api/settings').json()
    assert TEST_KEY not in json.dumps(saved)
    switch={'ai':{'provider':'zhipu','base_url':'https://open.bigmodel.cn/api/paas/v4','model':'glm-4-flash','api_key':None}}
    assert client.put('/api/settings',json=switch).status_code==200
    public=client.get('/api/settings').json()
    assert not public['ai']['has_key'] and public['ai_profiles']['test-fixture']['has_key']
    assert TEST_KEY not in json.dumps(public)
    switch['ai'].update(provider='test-fixture',base_url='http://localhost:9999/v1',model='synthetic-model')
    assert client.put('/api/settings',json=switch).json()['ai']['has_key']
    switch['ai']['base_url']='http://localhost:9998/v1'
    assert not client.put('/api/settings',json=switch).json()['ai']['has_key']


def test_mt_adapters_and_formula_integrity(tmp_path):
    seen=[];source='The variable PPMATH000001 remains unchanged.'
    def handler(req):
        seen.append(req);host=req.url.host
        if 'googleapis' in host:return httpx.Response(200,json=[[[source,None]]])
        if 'bing.com' in host:
            if req.method=='GET':return httpx.Response(200,text='IG:"fixture-ig";params_AbusePreventionHelper = [123,"fixture-token",600000];<div data-iid="translator.1">')
            assert parse_qs(req.content.decode())['token']==['fixture-token']
            return httpx.Response(200,json=[{'translations':[{'text':source}]}])
        if 'mymemory' in host:return httpx.Response(200,json={'responseStatus':200,'responseData':{'translatedText':source}})
        if 'youdao' in host:
            data=parse_qs(req.content.decode());q=data['q'][0];inp=q if len(q)<=20 else q[:10]+str(len(q))+q[-10:]
            assert data['sign'][0]==hashlib.sha256(('fixture-id'+inp+data['salt'][0]+data['curtime'][0]+'fixture-secret').encode()).hexdigest()
            return httpx.Response(200,json={'errorCode':'0','translation':[source]})
        if 'microsofttranslator' in host:
            assert req.headers['Ocp-Apim-Subscription-Key']=='fixture-key'
            return httpx.Response(200,json=[{'translations':[{'text':source}]}])
        return httpx.Response(200,json={'choices':[{'message':{'content':'A response missing the formula.'}}]})
    async def run():
        settings=json.loads(json.dumps(DEFAULT_SETTINGS));settings['youdao']={'app_id':'fixture-id','secret':'fixture-secret'};settings['microsoft']['api_key']='fixture-key'
        settings['ai'].update(provider='zhipu',base_url='https://open.bigmodel.cn/api/paas/v4',model='glm-4-flash',api_key='fixture-ai-key')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),trust_env=False) as client:
            provider=Providers(client,Config(data_dir=tmp_path,allow_local_api=True))
            for engine in ['google','bing','mymemory','youdao','microsoft']:
                assert 'PPMATH000001' in await provider.translate(settings,engine,source,'',lambda n:None)
            repaired = await provider.translate(settings,'ai',source,'',lambda n:None)
            assert repaired.count('PPMATH000001') == 1
            assert repaired.index('PPMATH000001') > 0
    asyncio.run(run())
    assert len(seen)==10  # normal attempt, structured repair, then two text spans
