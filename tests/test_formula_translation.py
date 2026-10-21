import asyncio
import json

import httpx
import pytest

from paperpilot.config import Config
from paperpilot.formulas import formula_sequence, protected_parts
from paperpilot.providers import Providers, ProviderError, TranslationStopped, formula_translation_chunks, split_text

SOURCE = 'Given A PPMATH000067 and B PPMATH000068, the result is PPMATH000069.'
SETTINGS = {'ai': {'provider': 'zhipu', 'base_url': 'https://open.bigmodel.cn/api/paas/v4',
                   'model': 'glm-4-flash', 'api_key': 'synthetic-secret'}}


def test_dense_formula_chunks_keep_sentences_and_figure_references_together():
    sentences = ['See Figs. S16 and S17 for details.',
                 'For A PPMATH000001 the voltage is lower than B PPMATH000002.',
                 'The redox PPMATH000003 reaction proceeds rapidly.']
    chunks = formula_translation_chunks(' '.join(sentences), 95)
    assert ' '.join(chunks) == ' '.join(sentences)
    assert all(any(sentence in chunk for chunk in chunks) for sentence in sentences)
    assert all(len(chunk) <= 95 for chunk in chunks)


@pytest.mark.parametrize('bad', [
    '给定 A 和 B，结果为。',
    '给定 PPMATH000067 PPMATH000067 PPMATH000068 PPMATH000069',
    '给定 PPMATH000067 PPMATH000068 PPMATH000069 PPMATH999999',
    '给定 PPMATH000067 PPMATH000068 PPMATH00006X',
    '给定 PPMATH000067 PPMATH000068 PPMATH000069 和 PPMATH00006X',
    '给定 PPMATH000067 PPMATH000068 PPMATH000069 和 [来源公式]',
])
def test_formula_damage_is_repaired_with_locally_owned_anchors(tmp_path, bad):
    calls = []

    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))

        async def ai(settings, messages, charge, **kwargs):
            charge(1)
            data = json.loads(messages[-1]['content'])
            calls.append(data)
            assert 'PPMATH999999' not in data.get('BACKGROUND', '')
            if not kwargs.get('json_output'):
                return bad
            # Response key order is deliberately unrelated to source order.
            values = {'0': '给定 A', '1': '和 B', '2': '，结果为'}
            return '```json\n' + json.dumps(dict(reversed(list(values.items())))) + '\n```'

        provider.ai = ai
        result = await provider.translate(SETTINGS, 'ai', SOURCE, 'Previous: PPMATH999999', lambda n: None)
        assert result == '给定 A PPMATH000067 和 B PPMATH000068，结果为 PPMATH000069.'
        assert formula_sequence(result) == formula_sequence(SOURCE)
        assert len(calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize('bad_json', [
    'not JSON', '{"translations":[]}', '{"translations":{"0":"丢失其他字段"}}',
    '{"translations":{"0":"甲","1":"乙","2":""}}',
    '{"translations":{"0":"甲","1":"PPMATH999999","2":"乙"}}',
    '{"translations":{"0":"甲","1":"乙","2":"丙","9":"多余"}}',
    '{"0":"整段 [source formula] 复制","1":"乙","2":"丙"}',
    '{"0":"整段 [源公式] 复制","1":"乙","2":"丙"}',
])
def test_bad_structured_response_falls_back_to_translating_only_text(tmp_path, bad_json):
    calls = []

    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))

        async def ai(settings, messages, charge, **kwargs):
            charge(1)
            data = json.loads(messages[-1]['content'])
            calls.append(data)
            if kwargs.get('json_output'):
                return bad_json
            if 'PPMATH' in data['TARGET']:
                return '模型漏掉公式'
            assert data['BACKGROUND'] == ''
            return {'Given A': '给定 A', 'and B': '和 B', ', the result is': '，结果为'}[data['TARGET']]

        provider.ai = ai
        result = await provider.translate(SETTINGS, 'ai', SOURCE, 'Context PPMATH999999', lambda n: None)
        assert formula_sequence(result) == formula_sequence(SOURCE)
        assert '给定 A' in result and '和 B' in result and '结果为' in result
        assert len(calls) == 5

    asyncio.run(run())


def test_spacing_normalization_does_not_need_extra_calls(tmp_path):
    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))
        calls = []

        async def ai(*args, **kwargs):
            calls.append(1)
            return '给定 PP MATH 000067 和 pp math000068，结果 PPMATH000069。'

        provider.ai = ai
        result = await provider.translate(SETTINGS, 'ai', SOURCE, '', lambda n: None)
        assert formula_sequence(result) == formula_sequence(SOURCE)
        assert len(calls) == 1

    asyncio.run(run())


def test_long_spans_are_bounded_and_math_tokens_never_split(tmp_path):
    source = 'x' * 3995 + 'PPMATH000001' + ' Long scientific sentence. ' * 300 + 'PPMATH000002'
    assert formula_sequence(''.join(split_text(source))) == formula_sequence(source)
    parts, fields = protected_parts(source)
    restored = ''.join(fields[int(p['id'])]['text'] if isinstance(p, dict) else p for p in parts)
    assert restored == source

    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))
        batches = []

        async def ai(settings, messages, charge, **kwargs):
            charge(1)
            data = json.loads(messages[-1]['content'])
            if not kwargs.get('json_output'):
                return '缺失公式'
            assert 'SOURCE' not in data and 'BACKGROUND' not in data
            assert sum(len(f['text']) for f in data['FIELDS']) <= 1800
            assert all(len(f['text']) <= 1600 for f in data['FIELDS'])
            batches.append(data['FIELDS'])
            return json.dumps({'translations': {f['id']: '翻译文字' for f in data['FIELDS']}})

        provider.ai = ai
        result = await provider.translate(SETTINGS, 'ai', source, '', lambda n: None)
        assert formula_sequence(result) == formula_sequence(source)
        assert len(batches) > 1

    asyncio.run(run())


def test_cancel_prevents_formula_repair_request(tmp_path):
    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))
        stopped, calls = False, []

        def charge(n):
            if stopped:
                raise TranslationStopped()

        async def ai(settings, messages, charge, **kwargs):
            nonlocal stopped
            charge(1)
            calls.append(1)
            stopped = True
            return '缺失公式'

        provider.ai = ai
        with pytest.raises(TranslationStopped):
            await provider.translate(SETTINGS, 'ai', SOURCE, '', charge)
        assert len(calls) == 1

    asyncio.run(run())


def test_provider_failure_is_not_treated_as_formula_damage(tmp_path):
    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))
        calls = []

        async def ai(*args, **kwargs):
            calls.append(1)
            raise ProviderError('API 凭证无效')

        provider.ai = ai
        with pytest.raises(ProviderError, match='凭证'):
            await provider.translate(SETTINGS, 'ai', SOURCE, '', lambda n: None)
        assert len(calls) == 1

    asyncio.run(run())


def test_machine_translation_can_recover_without_sending_anchors(tmp_path):
    sent = []

    def handler(request):
        text = json.loads(request.content)[0]['Text']
        sent.append(text)
        return httpx.Response(200, json=[{'translations': [{'text': '文字译文'}]}])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = Providers(client, Config(data_dir=tmp_path))
            result = await provider.translate({'microsoft': {'api_key': 'fixture', 'region': 'eastasia'}},
                                              'microsoft', SOURCE, '', lambda n: None)
            assert formula_sequence(result) == formula_sequence(SOURCE)
            assert len(sent) == 4
            assert all('PPMATH' not in text for text in sent[1:])

    asyncio.run(run())


def test_invalid_independent_translation_is_not_saved_as_success(tmp_path):
    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))

        async def ai(*args, **kwargs):
            return 'PPMATH999999'

        provider.ai = ai
        with pytest.raises(ProviderError, match='未返回有效文字译文'):
            await provider.translate(SETTINGS, 'ai', SOURCE, '', lambda n: None)

    asyncio.run(run())


@pytest.mark.parametrize('bad_compact', [
    '漏掉短公式标记',
    '[[F0]] [[F0]]',
    ' '.join(f'[[F{i}]]' for i in range(20)) + ' [[Fbad]]',
])
def test_dense_formula_paragraphs_use_bounded_fields_immediately(tmp_path, bad_compact):
    source = ' '.join(f'Variable PPMATH{i:06d}' for i in range(20))

    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))
        calls = []

        async def ai(settings, messages, charge, **kwargs):
            if not kwargs.get('json_output'):
                calls.append('compact')
                return bad_compact
            assert kwargs.get('json_output')
            data = json.loads(messages[-1]['content'])
            assert set(data) == {'GLOSSARY', 'FIELDS'}
            batch = data['FIELDS']
            calls.append(batch)
            return json.dumps({f['id']: '变量' for f in batch})

        provider.ai = ai
        result = await provider.translate(SETTINGS, 'ai', source, '', lambda n: None)
        assert formula_sequence(result) == formula_sequence(source)
        assert len(calls) == 3

    asyncio.run(run())


def test_natural_chinese_word_order_can_reposition_identified_formulas(tmp_path):
    source = 'For A PPMATH000001 the rate is larger than B PPMATH000002.'
    translated = '与 B PPMATH000002 相比，A PPMATH000001 的速率更大。'

    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))
        calls = []

        async def ai(*args, **kwargs):
            calls.append(1)
            return translated

        provider.ai = ai
        assert await provider.translate(SETTINGS, 'ai', source, '', lambda n: None) == translated
        assert len(calls) == 1

    asyncio.run(run())


def test_dense_formulas_preserve_sentences_with_validated_short_references(tmp_path):
    source = ' '.join(f'The variable PPMATH{i:06d} remains unchanged.' for i in range(30))

    async def run():
        provider = Providers(None, Config(data_dir=tmp_path))
        calls = []

        async def ai(settings, messages, charge, **kwargs):
            assert not kwargs.get('json_output')
            data = json.loads(messages[-1]['content'])
            assert 'PPMATH' not in data['TARGET'] and len(data['TARGET']) <= 800
            calls.append(data['TARGET'])
            return data['TARGET'].replace('The variable', '变量').replace('remains unchanged.', '保持不变。')

        provider.ai = ai
        result = await provider.translate(SETTINGS, 'ai', source, 'Unrelated background', lambda n: None)
        assert formula_sequence(result) == formula_sequence(source)
        assert '[[F' not in result and '保持不变' in result and len(calls) > 1

    asyncio.run(run())
