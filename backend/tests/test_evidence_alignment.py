"""Adversarial grounded quotations must not turn mismatches into verdicts."""

import json

import httpx
import pytest

from app.judge import judge_claim
from app.providers.mimo import MiMoEvidenceJudge, _claim_parts
from app.schemas import Claim, ClaimLabel, EvidenceCluster, EvidenceItem, EvidenceRelation
from app.scoring import support_score


MATCH = dict(entity="match", predicate="match", scope="match", value="match")
CONFLICT = {**MATCH, "value": "conflict"}


def run_judge(statement, body, *, quote=None, relation="supports", alignment=None, checks=None, decisions=None):
    claim = Claim(claim_id="c", task_id="t", source_text=statement, normalized_claim=statement,
                  char_start=0, char_end=len(statement))
    item = EvidenceItem(evidence_id="e", url="https://fixture.edu/source", excerpt=body,
                        authority=.9, quality_reason="网页正文")
    decision = dict(evidence_id="e", relation=relation, excerpt=quote or body, reason="核对证据。",
                    alignment=alignment if alignment is not None else (CONFLICT if relation == "refutes" else MATCH))
    parts = _claim_parts(statement)
    if checks is None and len(parts) > 1:
        checks = [dict(part_id=index + 1, relation=relation, excerpt=quote or body,
                       alignment=decision["alignment"]) for index, part in enumerate(parts)]
    if checks is not None:
        decision["checks"] = checks
    def respond(request):
        data = json.loads(request.content)
        assert len(data["messages"]) == 2
        assert "alignment" in data["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"decisions": decisions if decisions is not None else [decision]}, ensure_ascii=False)}}]})
    result = judge_claim(claim, [EvidenceCluster(cluster_id="ec", items=[item])],
                         MiMoEvidenceJudge("test", transport=httpx.MockTransport(respond)))
    return result, item


@pytest.mark.parametrize("statement,body,quote,reason", [
    ("甲公司成立于2010年。", "甲公司成立于1998年。", None, "日期"),
    ("甲公司成立于2010年5月。", "甲公司成立于2010年。", None, "精度"),
    ("甲公司位于上海。", "甲公司位于北京。", None, "属性答案"),
    ("甲大学位于上海。", "甲大学附属医院位于上海。", None, "同一主体"),
    ("甲公司位于上海。", "新甲公司位于上海。", None, "同一主体"),
    ("甲公司位于上海。", "甲公司成立于2010年。乙公司位于上海。", "乙公司位于上海。", "同一主体"),
    ("张三毕业于甲大学。", "张三就读于甲大学。", None, "同一主体"),
    ("甲公司成立于2010年。", "甲公司在2010年发布了产品。", None, "同一主体"),
    ("甲公司成立于2010年。", "甲公司成立于1998年，2010年发布了产品。", None, "日期"),
    ("声音能在真空中传播。", "声音不能在真空中传播。", "能在真空中传播", "否定"),
    ("甲公司发布产品。", "据称甲公司发布产品。", "甲公司发布产品", "转述"),
    ("甲公司发布产品。", "如果获得批准，甲公司发布产品。", "甲公司发布产品", "条件"),
    ("水的沸点是100摄氏度。", "在标准大气压下，水的沸点是100摄氏度。", "水的沸点是100摄氏度", "条件"),
    ("在标准大气压下，水的沸点是100摄氏度。", "水的沸点是100摄氏度。", None, "适用条件"),
    ("2024年甲公司研发投入为100亿元。", "2023年甲公司研发投入为100亿元。", None, "时间范围"),
    ("甲公司研发投入为100亿元。", "甲公司研发投入为100万元。", None, "数值"),
    ("甲公司研发投入为100亿元。", "乙公司研发投入为100亿元。", None, "同一主体"),
    ("甲公司研发投入为100亿元。", "新甲公司研发投入为100亿元。", None, "同一主体"),
    ("甲公司研发投入为100亿元。", "甲公司研发投入为10亿元，乙公司研发投入为100亿元。", None, "数值"),
    ("2024年甲公司研发投入为100亿元。", "2023年甲公司研发投入为100亿元，2024年乙公司营收为90亿元。", None, "时间范围"),
    ("水的沸点是100摄氏度。", "酒精的沸点是100摄氏度。", None, "同一主体"),
    ("甲公司研发投入为100亿元。", "甲公司營收为100亿元。", None, "统计指标"),
    ("甲公司研发投入为100亿元。", "甲公司研发投入为10亿元，营收为100亿元。", "营收为100亿元", "其他指标"),
    ("甲公司因为技术创新而获奖。", "甲公司进行了技术创新并获奖。", None, "因果关系"),
    ("所有设备支持中文。", "部分设备支持中文。", None, "全称"),
])
def test_real_quotes_with_false_model_alignment_are_vetoed(statement, body, quote, reason):
    result, item = run_judge(statement, body, quote=quote)
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT
    assert item.relation in {EvidenceRelation.UNKNOWN, EvidenceRelation.PARTIALLY_SUPPORTS}
    assert support_score(result) is None
    assert reason in item.quality_reason


@pytest.mark.parametrize("statement,body", [
    ("甲公司成立于2010年。", "甲公司创立于2010年5月8日。"),
    ("甲公司位于上海。", "甲公司位于上海市。"),
    ("甲公司研发投入为1亿元。", "甲公司研发投入为10000万元。"),
    ("甲公司研发投入为0.5亿元。", "甲公司研发支出为5000万元。"),
    ("2024年甲公司研发投入为1亿元。", "2024年，甲公司研发投入为10000万元。"),
    ("甲公司研发投入为1000万元。", "甲公司研发投入为1,000万元。"),
    ("水的沸点是100摄氏度。", "水的沸点是100℃。"),
    ("线路长1公里。", "线路长1000米。"),
    ("物体速度是36km/h。", "物体速度是10米/秒。"),
    ("设备质量是1kg。", "设备质量是1000克。"),
    ("在标准大气压下，水的沸点是100摄氏度。", "在标准大气压下，水的沸点是100摄氏度。"),
])
def test_equivalent_units_and_finer_dates_do_not_lose_valid_support(statement, body):
    result, item = run_judge(statement, body)
    assert item.relation == EvidenceRelation.SUPPORTS
    assert result.label == ClaimLabel.CREDIBLE


@pytest.mark.parametrize("statement,body,quote", [
    ("甲公司成立于2010年。", "甲公司成立于2010年5月。", None),
    ("甲公司位于上海。", "甲公司位于上海市黄浦区。", None),
    ("甲公司研发投入为1亿元。", "甲公司研发投入为10000万元。", None),
    ("甲公司研发投入为100亿元。", "甲公司研发投入为90亿美元。", None),
    ("甲公司研发投入为100亿元。", "甲公司研发投入不是90亿美元。", None),
    ("2024年甲公司研发投入为100亿元。", "2023年甲公司研发投入为90亿元。", None),
    ("甲公司研发投入为100亿元。", "甲公司研发投入为10亿元，营收为80亿元。", "营收为80亿元"),
    ("2024年甲公司研发投入为100亿元。", "2024年甲公司研发投入为100亿元，2023年甲公司研发投入为90亿元。", None),
])
def test_nonexclusive_or_incomparable_answers_cannot_refute(statement, body, quote):
    result, item = run_judge(statement, body, quote=quote, relation="refutes")
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT
    assert item.relation == EvidenceRelation.UNKNOWN


@pytest.mark.parametrize("dimension,status", [
    ("entity", "mismatch"), ("entity", "unknown"),
    ("predicate", "mismatch"), ("predicate", "unknown"),
    ("scope", "mismatch"), ("scope", "unknown"), ("value", "unknown"),
])
@pytest.mark.parametrize("relation", ["supports", "refutes"])
def test_any_unconfirmed_alignment_dimension_blocks_a_direct_verdict(dimension, status, relation):
    alignment = {**(MATCH if relation == "supports" else CONFLICT), dimension: status}
    result, item = run_judge("声音能在真空中传播。", "声音不能在真空中传播。",
                             relation=relation, alignment=alignment)
    assert item.relation == EvidenceRelation.UNKNOWN
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


@pytest.mark.parametrize("statement,body", [
    ("甲公司成立于2010年。", "甲公司成立于1998年。"),
    ("甲公司位于上海。", "甲公司位于北京，而非上海。"),
    ("甲公司研发投入为100亿元。", "甲公司研发投入为90亿元。"),
    ("声音能在真空中传播。", "声音不能在真空中传播。"),
    ("设备质量不是1kg。", "设备质量是1000克。"),
    ("设备质量是1kg。", "设备质量不是1000克。"),
])
def test_same_scope_direct_conflicts_remain_refutations(statement, body):
    result, item = run_judge(statement, body, relation="refutes")
    assert item.relation == EvidenceRelation.REFUTES
    assert result.label == ClaimLabel.INCORRECT


def test_part_refutation_overrules_an_incorrect_whole_support_label():
    statement = "甲公司成立于2010年，它位于上海。"
    first, second = "甲公司成立于2010年。", "甲公司位于北京，而非上海。"
    checks = [dict(part_id=1, relation="supports", excerpt=first, alignment=MATCH),
              dict(part_id=2, relation="refutes", excerpt=second, alignment=CONFLICT)]
    result, item = run_judge(statement, first + second, checks=checks)
    assert item.relation == EvidenceRelation.REFUTES
    assert result.label == ClaimLabel.INCORRECT


def test_partial_coverage_does_not_become_full_support():
    first = "甲公司成立于2010年。"
    result, item = run_judge("甲公司成立于2010年，它位于上海。", first,
                            checks=[dict(part_id=1, relation="supports", excerpt=first, alignment=MATCH)])
    assert item.relation == EvidenceRelation.PARTIALLY_SUPPORTS
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_causal_part_does_not_change_the_scope_of_an_independent_property():
    statement = "甲公司因为技术创新而获奖，它成立于2010年。"
    assert len(_claim_parts(statement)) == 2
    first, second = "甲公司因为技术创新而获奖。", "甲公司成立于1998年。"
    result, item = run_judge(statement, first + second, relation="refutes", checks=[
        dict(part_id=1, relation="supports", excerpt=first, alignment=MATCH),
        dict(part_id=2, relation="refutes", excerpt=second, alignment=CONFLICT),
    ])
    assert result.label == ClaimLabel.INCORRECT


@pytest.mark.parametrize("ids", [(1, 1), (1, 3)])
def test_duplicate_or_out_of_range_part_ids_are_rejected(ids):
    body = "甲公司成立于2010年。甲公司位于上海。"
    result, item = run_judge("甲公司成立于2010年，它位于上海。", body, checks=[
        dict(part_id=i, relation="supports", excerpt=body, alignment=MATCH) for i in ids])
    assert item.relation == EvidenceRelation.UNKNOWN
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_duplicate_evidence_decisions_do_not_accept_the_first_answer():
    body = "甲公司位于上海。"
    decisions = [dict(evidence_id="e", relation=relation, excerpt=body, reason="核对。", alignment=MATCH)
                 for relation in ("supports", "refutes")]
    result, item = run_judge(body, body, decisions=decisions)
    assert item.relation == EvidenceRelation.UNKNOWN
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_omitted_decisions_clear_previous_relationship_scores():
    from app.providers.mimo import _DecisionPayload
    item = EvidenceItem(evidence_id="e", excerpt="甲公司位于上海。", relation="supports", relevance=.9, time_fit=.5)
    MiMoEvidenceJudge._apply_validated_decisions(_DecisionPayload(decisions=[]), {"e": item.excerpt},
        [EvidenceCluster(cluster_id="ec", items=[item])], parts=[item.excerpt])
    assert item.relation == EvidenceRelation.UNKNOWN
    assert item.relevance == item.time_fit == 0


def test_unknown_evidence_id_and_invented_part_quote_cannot_score():
    body = "甲公司位于上海。"
    decisions = [dict(evidence_id="invented", relation="supports", excerpt=body, reason="核对。", alignment=MATCH),
                 dict(evidence_id="e", relation="supports", excerpt=body, reason="核对。", checks=[
                     dict(part_id=1, relation="supports", excerpt="甲公司位于北京。", alignment=MATCH)])]
    result, item = run_judge(body, body, decisions=decisions)
    assert item.relation == EvidenceRelation.UNKNOWN
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_repeated_narrow_quote_cannot_select_only_the_supporting_context():
    body = "甲公司位于上海。乙公司位于上海。"
    result, item = run_judge("甲公司位于上海。", body, quote="位于上海")
    assert item.relation == EvidenceRelation.UNKNOWN
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_legacy_nonliteral_single_decision_cannot_skip_alignment():
    body = "张三就读于甲大学。"
    decisions = [dict(evidence_id="e", relation="supports", excerpt=body, reason="核对。")]
    result, item = run_judge("张三毕业于甲大学。", body, decisions=decisions)
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT
    assert item.relation == EvidenceRelation.UNKNOWN


def test_a_repeated_literal_fact_is_not_an_exclusive_refutation():
    statement = "Paris is in France."
    result, item = run_judge(statement, statement, relation="refutes")
    assert item.relation == EvidenceRelation.UNKNOWN
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_condition_clause_does_not_lose_a_supported_negative_fact():
    statement = "在标准大气压下，水的沸点不是90摄氏度。"
    result, item = run_judge(statement, statement)
    assert item.relation == EvidenceRelation.SUPPORTS
    assert result.label == ClaimLabel.CREDIBLE


def test_condition_clause_and_value_conflict_are_combined_in_their_joint_scope():
    statement = "在标准大气压下，水的沸点是90摄氏度。"
    body = "在标准大气压下，水的沸点是100摄氏度。"
    result, item = run_judge(statement, body, relation="refutes", checks=[
        dict(part_id=1, relation="supports", excerpt=body, alignment=MATCH),
        dict(part_id=2, relation="refutes", excerpt=body, alignment=CONFLICT),
    ])
    assert item.relation == EvidenceRelation.REFUTES
    assert result.label == ClaimLabel.INCORRECT


def test_legacy_explicit_denial_cannot_drop_the_claim_condition():
    statement = "如果甲公司迁址，甲公司位于上海。"
    body = "甲公司位于北京，而非上海。"
    decisions = [dict(evidence_id="e", relation="refutes", excerpt=body, reason="核对。")]
    result, item = run_judge(statement, body, decisions=decisions)
    assert item.relation == EvidenceRelation.UNKNOWN
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT
