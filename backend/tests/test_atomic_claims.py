"""Product regressions: independent facts, shared scope, and exact highlights."""

import time

import pytest

from app.extract import count_claim_candidates, extract_claims, utf16_len
from app.pipeline import Pipeline
from app.providers.base import JudgmentProviderError
from app.providers.segmentation import SemanticSegment, validated_spans
from app.storage import Storage
from app.text_processing import normalize_claim
from test_segmentation import segmenter_for


SCREENSHOT = '三国时期，诸葛亮与凯撒大帝在华盛顿签署《马关条约》，割让澳大利亚给日本，并宣布日语成为联合国唯一官方语言。'


@pytest.mark.parametrize('model_mode', ['rules', 'coarse_model', 'semantic_model'])
def test_screenshot_is_three_independent_claims_with_shared_context(model_mode):
    parts = ['三国时期，诸葛亮与凯撒大帝在华盛顿签署《马关条约》，',
             '割让澳大利亚给日本，', '并宣布日语成为联合国唯一官方语言。']
    prefix = '三国时期，诸葛亮与凯撒大帝在华盛顿'
    spans = None
    if model_mode == 'coarse_model':
        spans = segmenter_for([SCREENSHOT]).split(SCREENSHOT, deadline=time.monotonic() + 10)
    if model_mode == 'semantic_model':
        payload = [dict(source=source, context='' if i == 0 else prefix) for i, source in enumerate(parts)]
        spans = segmenter_for(payload).split(SCREENSHOT, deadline=time.monotonic() + 10)
    claims, truncated = extract_claims(SCREENSHOT, 't', spans=spans)
    assert not truncated
    assert [claim.source_text for claim in claims] == parts
    assert [claim.normalized_claim for claim in claims] == [
        '三国时期,诸葛亮与凯撒大帝在华盛顿签署《马关条约》',
        '三国时期,诸葛亮与凯撒大帝在华盛顿割让澳大利亚给日本',
        '三国时期,诸葛亮与凯撒大帝在华盛顿宣布日语成为联合国唯一官方语言。',
    ]
    assert all(sum(action in claim.normalized_claim for action in ['签署', '割让', '宣布']) == 1 for claim in claims)
    assert count_claim_candidates(SCREENSHOT) == 3


@pytest.mark.parametrize('text, expected', [
    ('甲公司位于北京，乙公司位于上海。', ['甲公司位于北京', '乙公司位于上海。']),
    ('张三出生于北京并毕业于甲大学。', ['张三出生于北京', '张三毕业于甲大学。']),
    ('2024年，甲公司发布A产品，并收购乙公司。', ['2024年,甲公司发布A产品', '2024年,甲公司收购乙公司。']),
    ('甲公司发布A产品，乙公司收购丙公司，并宣布总部迁往上海。',
     ['甲公司发布A产品', '乙公司收购丙公司', '乙公司宣布总部迁往上海。']),
    ('甲公司签署《合作，并发展协议》，并宣布合作启动。',
     ['甲公司签署《合作,并发展协议》', '甲公司宣布合作启动。']),
])
def test_independent_predicates_and_subject_changes(text, expected):
    from app.text_processing import normalize_claim
    claims, _ = extract_claims(text, 't')
    assert [claim.normalized_claim for claim in claims] == [normalize_claim(s) for s in expected]


@pytest.mark.parametrize('text, expected', [
    ('2024年甲公司发布A产品，乙公司收购丙公司。',
     ['2024年甲公司发布A产品', '2024年乙公司收购丙公司。']),
    ('甲公司在北京发布A产品，并在上海收购乙公司，并宣布合作启动。',
     ['甲公司在北京发布A产品', '甲公司在上海收购乙公司', '甲公司在上海宣布合作启动。']),
    ('2024年，甲公司在北京发布A产品，乙公司收购丙公司，并宣布合作启动。',
     ['2024年,甲公司在北京发布A产品', '2024年,乙公司收购丙公司', '2024年,乙公司宣布合作启动。']),
    ('2024年，甲公司发布A产品，2025年乙公司收购丙公司，并宣布合作启动。',
     ['2024年,甲公司发布A产品', '2025年乙公司收购丙公司', '2025年乙公司宣布合作启动。']),
    ('甲公司签署《未来发展协议》，并宣布合作启动。',
     ['甲公司签署《未来发展协议》', '甲公司宣布合作启动。']),
    ('并州大学发布招生简章，并宣布开放报名。',
     ['并州大学发布招生简章', '并州大学宣布开放报名。']),
])
@pytest.mark.parametrize('mode', ['rules', 'coarse_model'])
def test_scope_inheritance_does_not_copy_a_previous_fact(text, expected, mode):
    spans = None if mode == 'rules' else validated_spans(text, [text])
    claims, _ = extract_claims(text, 't', spans=spans)
    assert [c.normalized_claim for c in claims] == expected
    raw = text.encode('utf-16-le')
    assert ''.join(c.source_text for c in claims) == text
    for claim in claims:
        assert raw[claim.char_start * 2:claim.char_end * 2].decode('utf-16-le') == claim.source_text


def test_model_can_inherit_the_latest_explicit_subject():
    parts = ['甲公司发布A产品，', '乙公司收购丙公司，', '并宣布合作启动。']
    text = ''.join(parts)
    spans = validated_spans(text, [parts[0], parts[1],
        SemanticSegment(source=parts[2], context='乙公司')])
    assert spans[-1].normalized == '乙公司宣布合作启动。'
    with pytest.raises(JudgmentProviderError):
        validated_spans(text, [parts[0], parts[1],
            SemanticSegment(source=parts[2], context='甲公司')])


@pytest.mark.parametrize('text', [
    '甲公司在2024年发布A产品，并在上海收购乙公司。',
    '甲公司发布A产品，2025年收购乙公司。',
    '甲公司（尚未获批）发布A产品，并收购乙公司。',
])
def test_ambiguous_time_location_or_parenthetical_scope_stays_joint(text):
    claims, _ = extract_claims(text, 't')
    assert len(claims) == 1 and claims[0].source_text == text


@pytest.mark.parametrize('text', [
    '在标准大气压下，纯水的沸点不是90摄氏度，而是100摄氏度。',
    '如果甲公司收购乙公司，并宣布合并，审批才会开始。',
    '甲公司没有签署协议，也没有宣布合并。',
    '报告称甲公司发布A产品，并收购乙公司。',
    '甲公司可能发布A产品，并收购乙公司。',
    '甲公司宣布，如果通过审批，并获得许可，则开通线路。',
    '甲公司拥有北京、上海和深圳三个分部。',
    '甲公司发布A产品，配色有红色，蓝色和绿色。',
    '甲公司发布A产品，因此获得奖励。',
    '甲公司发布A产品，让乙公司获得奖励。',
    '甲公司发布A产品，其支持中文输入。',
    '甲公司签署协议（条款包括发布公告，并开通服务），并宣布合作启动。',
])
def test_dependent_statements_do_not_lose_their_scope(text):
    claims, _ = extract_claims(text, 't')
    # Bracketed clauses may split outside the bracket, never inside it.
    if '（' in text:
        assert len(claims) == 2
        assert '（条款包括发布公告，并开通服务）' in claims[0].source_text
    else:
        assert len(claims) == 1
        assert claims[0].source_text == text


def test_utf16_and_budget_use_atomic_claims():
    text = '😀甲公司发布A产品，并收购乙公司，并宣布总部迁往上海。'
    claims, truncated = extract_claims(text, 't', 2)
    assert truncated and count_claim_candidates(text) == 3
    raw = text.encode('utf-16-le')
    for claim in claims:
        assert raw[claim.char_start * 2:claim.char_end * 2].decode('utf-16-le') == claim.source_text
    assert claims[1].char_start == utf16_len(claims[0].source_text)


def test_consecutive_metrics_inherit_only_the_shared_entity_and_keep_original_utf16_ranges():
    text = '😀甲公司营收达到100亿元，净利润增长20%，并发布年度报告。'
    claims, truncated = extract_claims(text, 't')
    assert not truncated
    assert [claim.normalized_claim for claim in claims] == [
        '😀甲公司营收达到100亿元', '😀甲公司净利润增长20%', '😀甲公司发布年度报告。',
    ]
    raw = text.encode('utf-16-le')
    for claim in claims:
        assert raw[claim.char_start * 2:claim.char_end * 2].decode('utf-16-le') == claim.source_text


@pytest.mark.parametrize('context', ['乙公司', '甲公司收购', '2025年，甲公司', '甲', ''])
def test_untraceable_or_missing_shared_subject_is_rejected(context):
    text = '甲公司发布A产品，并收购乙公司。'
    with pytest.raises(JudgmentProviderError):
        validated_spans(text, ['甲公司发布A产品，', SemanticSegment(source='并收购乙公司。', context=context)])


def test_context_cannot_be_copied_from_another_sentence():
    with pytest.raises(JudgmentProviderError):
        validated_spans('甲公司位于北京。乙公司收购丙公司。', [
            '甲公司位于北京。', SemanticSegment(source='乙公司收购丙公司。', context='甲公司'),
        ])


def test_overlong_model_context_is_trimmed_without_repeating_the_previous_fact():
    parts = ['三国时期，诸葛亮与凯撒大帝在华盛顿签署《马关条约》，',
             '割让澳大利亚给日本，', '并宣布日语成为联合国唯一官方语言。']
    spans = validated_spans(SCREENSHOT, [
        SemanticSegment(source=part, context=parts[0] if i else '') for i, part in enumerate(parts)
    ])
    assert len(spans) == 3
    assert all('签署' not in span.normalized for span in spans[1:])
    assert all('三国时期' in span.normalized and '诸葛亮与凯撒大帝在华盛顿' in span.normalized for span in spans)


def test_model_cannot_detach_a_conditional_or_negation():
    for parts in [['如果甲公司收购乙公司，', '并宣布合并，审批才会开始。'],
                  ['甲公司没有签署协议，', '也没有宣布合并。']]:
        with pytest.raises(JudgmentProviderError):
            validated_spans(''.join(parts), parts)


def test_fallback_and_limit_keep_shared_subjects_for_retrieval():
    class RecordingRetriever:
        provider = 'fixture'
        def __init__(self):
            self.claims = []
        def retrieve(self, claim):
            self.claims.append(claim.normalized_claim)
            return []

    storage = Storage(':memory:')
    retriever = RecordingRetriever()
    try:
        pipeline = Pipeline(storage, segmenter=segmenter_for(['改写的原文。']), retriever=retriever)
        task = pipeline.create(SCREENSHOT, 2)
        summary = pipeline.run_sync(task.task_id)
        assert summary.segmentation_method == 'rules_fallback'
        assert summary.claims_extracted == 3 and summary.claims_unchecked == 1
        assert summary.truncated
        assert len(retriever.claims) == 2
        assert all('诸葛亮与凯撒大帝' in claim and '三国时期' in claim for claim in retriever.claims)
        assert '签署' not in retriever.claims[1]
    finally:
        storage.close()
