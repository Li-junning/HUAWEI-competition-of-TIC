import json
import time

import httpx
import pytest

from app.extract import extract_claims, utf16_len
from app.pipeline import Pipeline
from app.providers.base import JudgmentProviderError
from app.providers.segmentation import MiMoSegmenter, validated_spans
from app.storage import Storage


def segmenter_for(segments):
    def respond(request):
        body = json.loads(request.content)
        assert body['response_format'] == {'type': 'json_object'}
        assert 'segments' in body['messages'][0]['content']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'segments': segments}, ensure_ascii=False),
        }}]})
    return MiMoSegmenter('test-key', transport=httpx.MockTransport(respond))


def test_model_splits_comma_and_preserves_utf16_offsets():
    parts = ['😀甲公司位于北京，', '乙公司位于上海。']
    text = ''.join(parts)
    spans = segmenter_for(parts).split(text, deadline=time.monotonic() + 10)
    claims, truncated = extract_claims(text, 'task', spans=spans)
    assert not truncated
    assert [c.source_text for c in claims] == parts
    assert claims[1].char_start == utf16_len(parts[0])
    assert claims[1].char_end == utf16_len(text)


@pytest.mark.parametrize('segments', [
    ['甲公司位于北京，'],
    ['甲公司位于上海，', '乙公司位于上海。'],
    ['乙公司位于上海。', '甲公司位于北京，'],
    ['甲公司位于北京，', '甲公司位于北京，', '乙公司位于上海。'],
    ['', '甲公司位于北京，乙公司位于上海。'],
])
def test_invalid_partitions_are_rejected(segments):
    with pytest.raises(JudgmentProviderError):
        validated_spans('甲公司位于北京，乙公司位于上海。', segments)


def test_repeated_text_and_whitespace():
    spans = validated_spans('甲。\n甲。', ['甲。', '甲。'])
    assert [(s.start, s.end) for s in spans] == [(0, 2), (3, 5)]


def test_heading_cannot_hide_body_text():
    with pytest.raises(JudgmentProviderError):
        validated_spans('# 标题\n正文事实。', ['# 标题\n正文事实。'])
    spans = validated_spans('# 标题\n正文事实。', ['# 标题', '正文事实。'])
    assert [s.source for s in spans] == ['正文事实。']


def test_pipeline_counts_semantic_candidates_before_limit():
    parts = ['甲公司位于北京，', '乙公司位于上海，', '丙公司位于天津。']
    storage = Storage(':memory:')
    pipeline = Pipeline(storage, segmenter=segmenter_for(parts))
    task = pipeline.create(''.join(parts), 2)
    summary = pipeline.run_sync(task.task_id)
    assert summary.claims_extracted == 3
    assert summary.claims_unchecked == 1
    assert summary.truncated
    assert pipeline.get_summary(task.task_id).segmentation_method == 'mimo'


@pytest.mark.parametrize('failure', ['omission', 'timeout'])
def test_pipeline_falls_back_without_losing_text(failure):
    splitter = segmenter_for(['甲。'])
    if failure == 'timeout':
        def timeout(request):
            raise httpx.ReadTimeout('private upstream details')
        splitter = MiMoSegmenter('test-key', transport=httpx.MockTransport(timeout), sleep=lambda _: None)
    storage = Storage(':memory:')
    pipeline = Pipeline(storage, segmenter=splitter)
    task = pipeline.create('甲。乙。', 15)
    summary = pipeline.run_sync(task.task_id)
    assert summary.segmentation_method == 'rules_fallback'
    assert summary.claims_extracted == 2
    assert [c.source_text for c in storage.list_claims(task.task_id)[0]] == ['甲。', '乙。']
    assert summary.failed_providers == []
    assert pipeline.get_summary(task.task_id).segmentation_method == 'rules_fallback'


def test_expired_budget_does_not_send_request():
    def unexpected(request):
        pytest.fail('Expired budget must not call API')
    splitter = MiMoSegmenter('test-key', transport=httpx.MockTransport(unexpected))
    with pytest.raises(JudgmentProviderError):
        splitter.split('甲。', deadline=time.monotonic() - 1)
