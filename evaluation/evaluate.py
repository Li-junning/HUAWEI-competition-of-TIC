"""Offline claim-label evaluation using the application's real extract/judge logic.

All bundled evidence is synthetic and explicitly marked as a fixture. This is
an executable semantic smoke suite, not a measurement of live search accuracy.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.extract import extract_claims  # noqa: E402
from app.judge import judge_claim  # noqa: E402
from app.schemas import ClaimLabel, EvidenceCluster, EvidenceItem, EvidenceRelation  # noqa: E402
from app.config import Settings  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.providers.base import SearchProviderError  # noqa: E402
from app.storage import Storage  # noqa: E402

LABELS = ["credible", "disputed", "incorrect", "evidence_insufficient", "not_applicable"]
CLASSIFICATION_LABELS = ["credible", "disputed", "incorrect", "evidence_insufficient"]
TECHNICAL_STATES = {"technical_failure", "unchecked"}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON {path}: {exc}") from exc


def validate_run_info(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("run-info 必须是 JSON 对象")
    if not isinstance(value.get("system"), str) or not value["system"].strip():
        raise ValueError("run-info.system 不能为空")
    if not isinstance(value.get("network_enabled"), bool):
        raise ValueError("run-info.network_enabled 必须为布尔值")
    sources = value.get("search_sources")
    if not isinstance(sources, list) or not all(isinstance(s, str) and s.strip() for s in sources):
        raise ValueError("run-info.search_sources 必须是字符串数组")
    return value


def _validate_annotation(row: dict[str, Any], label: Any) -> None:
    """Require traceable human gold labels before counting them as evaluation data."""
    source = row.get("source", "synthetic")
    if source not in {"synthetic", "human", "curated_public_fact_seed"}:
        raise ValueError(f"{row.get('case_id')}: 未知 source: {source}")
    annotation = row.get("annotation")
    if source == "synthetic":
        return
    if label not in LABELS:
        raise ValueError(f"{row.get('case_id')}: {source} 样本必须使用受支持的事实/观点 gold 标签")
    if not isinstance(annotation, dict):
        raise ValueError(f"{row.get('case_id')}: {source} 样本必须提供 annotation")
    for field in ("checked_at", "label_basis"):
        if not isinstance(annotation.get(field), str) or not annotation[field].strip():
            raise ValueError(f"{row.get('case_id')}: annotation.{field} 不能为空")
    if source == "human":
        for field in ("annotator", "reviewer"):
            if not isinstance(annotation.get(field), str) or not annotation[field].strip():
                raise ValueError(f"{row.get('case_id')}: annotation.{field} 不能为空")
    elif annotation.get("review_status") != "example_not_independently_reviewed":
        raise ValueError(f"{row.get('case_id')}: 公开事实演练必须明确标为未独立复核")
    sources = annotation.get("sources")
    if not isinstance(sources, list):
        raise ValueError(f"{row.get('case_id')}: annotation.sources 必须是数组")
    for i, ref in enumerate(sources):
        if not isinstance(ref, dict) or not isinstance(ref.get("url"), str) or not ref["url"].startswith(("https://", "http://")):
            raise ValueError(f"{row.get('case_id')}: annotation.sources[{i}] 必须含 HTTP(S) URL")
        if ref.get("relation") not in {"supports", "refutes", "partially_supports", "unknown"}:
            raise ValueError(f"{row.get('case_id')}: annotation.sources[{i}].relation 无效")
        excerpt = ref.get("excerpt") or ref.get("excerpt_paraphrase")
        if not isinstance(excerpt, str) or not excerpt.strip():
            raise ValueError(f"{row.get('case_id')}: annotation.sources[{i}] 必须含 excerpt 或 excerpt_paraphrase")
    if source == "curated_public_fact_seed":
        if not sources:
            raise ValueError(f"{row.get('case_id')}: 公开事实演练样本必须有来源 URL 和摘录")
        return
    origin = row.get("origin")
    if not isinstance(origin, dict):
        raise ValueError(f"{row.get('case_id')}: human 样本必须记录 origin")
    for field in ("provider", "captured_at", "reference"):
        if not isinstance(origin.get(field), str) or not origin[field].strip():
            raise ValueError(f"{row.get('case_id')}: origin.{field} 不能为空")
    if label in {"credible", "incorrect", "disputed"} and not sources:
        raise ValueError(f"{row.get('case_id')}: {label} 人工标签必须有至少一个可追溯证据来源")
    if label in {"credible", "incorrect"}:
        needed = "supports" if label == "credible" else "refutes"
        if not any(ref.get("relation") == needed for ref in sources):
            raise ValueError(f"{row.get('case_id')}: {label} 标签必须含 relation={needed} 的来源")
    if label == "disputed":
        relations = {ref.get("relation") for ref in sources}
        if not {"supports", "refutes"}.issubset(relations):
            raise ValueError(f"{row.get('case_id')}: disputed 标签需包含 supports 与 refutes 两类来源")
    if label == "evidence_insufficient":
        searches = annotation.get("search_log")
        if not isinstance(searches, list) or not searches or not all(isinstance(q, str) and q.strip() for q in searches):
            raise ValueError(f"{row.get('case_id')}: evidence_insufficient 人工标签必须记录 search_log 查询词")


def validate_cases(cases: Any, *, allow_missing_annotations: bool = False) -> list[dict[str, Any]]:
    if not isinstance(cases, list) or not cases:
        raise ValueError("样本集必须是非空 JSON 数组")
    seen: set[str] = set()
    for row in cases:
        if not isinstance(row, dict):
            raise ValueError("每条样本必须是 JSON 对象")
        cid, text = row.get("case_id"), row.get("input_text")
        if not isinstance(cid, str) or not cid.strip():
            raise ValueError("case_id 不能为空")
        if cid in seen:
            raise ValueError(f"重复 case_id: {cid}")
        seen.add(cid)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{cid}: input_text 不能为空")
        target_claim = row.get("target_claim")
        if target_claim is not None and (not isinstance(target_claim, str) or not target_claim.strip()):
            raise ValueError(f"{cid}: target_claim 必须是非空字符串")
        expected = row.get("expected", {})
        if not isinstance(expected, dict):
            raise ValueError(f"{cid}: expected 必须是对象")
        label = expected.get("label")
        if not isinstance(label, (str, type(None))) or (label is not None and label not in LABELS and label not in TECHNICAL_STATES):
            raise ValueError(f"{cid}: 未知 expected.label: {label}")
        if not (allow_missing_annotations and row.get("source") == "human" and not row.get("annotation")):
            _validate_annotation(row, label)
        checks = row.get("functional_checks", {})
        allowed_checks = {"claim_count", "truncated", "evidence_insufficient_not_error", "technical_failure_unlabeled", "normalized_claim_contains"}
        if not isinstance(checks, dict) or set(checks) - allowed_checks:
            raise ValueError(f"{cid}: functional_checks 含未知检查项")
        if "claim_count" in checks and (not isinstance(checks["claim_count"], int) or checks["claim_count"] < 0):
            raise ValueError(f"{cid}: claim_count 检查值必须是非负整数")
        if "truncated" in checks and not isinstance(checks["truncated"], bool):
            raise ValueError(f"{cid}: truncated 检查值必须是布尔值")
        if "normalized_claim_contains" in checks and (not isinstance(checks["normalized_claim_contains"], list) or not all(isinstance(x, str) for x in checks["normalized_claim_contains"])):
            raise ValueError(f"{cid}: normalized_claim_contains 必须是字符串数组")
    return cases


def merge_gold(cases: list[dict[str, Any]], gold: Any) -> list[dict[str, Any]]:
    """Attach a separately held gold file to blinded input cases by exact IDs."""
    if not isinstance(gold, list) or not gold:
        raise ValueError("gold 文件必须是非空 JSON 数组")
    by_id = {row["case_id"]: row for row in cases}
    gold_by_id: dict[str, dict[str, Any]] = {}
    for row in gold:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str) or not row["case_id"].strip():
            raise ValueError("每条 gold 必须含非空 case_id")
        if row["case_id"] in gold_by_id:
            raise ValueError(f"重复 gold case_id: {row['case_id']}")
        gold_by_id[row["case_id"]] = row
    if set(gold_by_id) != set(by_id):
        raise ValueError("gold 与盲测输入的 case_id 必须完全一致")
    merged = []
    for cid, case in by_id.items():
        expected = gold_by_id[cid].get("expected")
        label = expected.get("label") if isinstance(expected, dict) else None
        if label not in LABELS:
            raise ValueError(f"{cid}: gold.expected.label 必须是支持的事实/观点标签")
        combined = {**case, "expected": {"label": label}, "annotation": gold_by_id[cid].get("annotation")}
        merged.append(combined)
    return validate_cases(merged)


def validate_predictions(gold_rows: list[dict[str, Any]], predictions: Any) -> dict[str, Any]:
    # The app's JSON export has a `claims` array. Map it by an explicit case_id
    # when present, otherwise use a unique target_claim/input_text exact match.
    if isinstance(predictions, dict) and isinstance(predictions.get("claims"), list):
        cases_by_id = {row["case_id"]: row for row in gold_rows}
        mapped = []
        used: set[str] = set()
        for claim in predictions["claims"]:
            if not isinstance(claim, dict):
                continue
            cid = claim.get("case_id")
            if not isinstance(cid, str):
                texts = {value for value in (claim.get("normalized_claim"), claim.get("source_text"))
                         if isinstance(value, str)}
                matches = [row["case_id"] for row in gold_rows
                           if texts & {row.get("target_claim", row["input_text"]), row["input_text"]}]
                if len(matches) > 1:
                    raise ValueError("系统导出声明匹配到多个样本；请提供显式 case_id 预测映射")
                if not matches:
                    continue
                cid = matches[0]
            if cid not in cases_by_id:
                raise ValueError(f"系统导出含未知 case_id: {cid}")
            if cid in used:
                raise ValueError(f"多个系统导出声明匹配到同一 case_id: {cid}")
            mapped.append({"case_id": cid, "label": claim.get("label")})
            used.add(cid)
        predictions = mapped
    if not isinstance(predictions, list) or not predictions:
        raise ValueError("预测集必须是非空 JSON 数组，或包含可唯一匹配 case_id/target_claim 的系统 JSON 导出")
    by_id = {row["case_id"]: row for row in gold_rows}
    result: dict[str, Any] = {}
    for row in predictions:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str) or not row["case_id"].strip():
            raise ValueError("每条预测必须含非空 case_id")
        cid = row["case_id"]
        if cid in result:
            raise ValueError(f"重复预测 case_id: {cid}")
        if cid not in by_id:
            raise ValueError(f"预测包含未知 case_id: {cid}")
        if "label" not in row:
            raise ValueError(f"{cid}: 预测缺少 label 字段")
        result[cid] = row["label"]
    missing = set(by_id) - set(result)
    if missing:
        raise ValueError("预测缺少 case_id: " + ", ".join(sorted(missing)))
    return result


def load_predictions(path: Path) -> Any:
    """Load one prediction file or combine several app JSON exports from a directory."""
    if not path.is_dir():
        return _load(path)
    files = sorted(path.glob("*.json"))
    if not files:
        raise ValueError(f"预测目录中没有 JSON 文件: {path}")
    exports: list[Any] = [_load(file) for file in files]
    if all(isinstance(item, dict) and isinstance(item.get("claims"), list) for item in exports):
        return {"claims": [claim for item in exports for claim in item["claims"]]}
    if all(isinstance(item, list) for item in exports):
        return [row for item in exports for row in item]
    raise ValueError("预测目录中的 JSON 必须全部是系统报告（含 claims）或显式 case_id 预测数组")


def _fixture_clusters(rows: list[dict[str, Any]]) -> list[EvidenceCluster]:
    clusters = []
    for ci, group in enumerate(rows):
        items = []
        for ei, raw in enumerate(group.get("items", [])):
            items.append(EvidenceItem(
                evidence_id=raw.get("evidence_id", f"fixture-{ci}-{ei}"),
                url=raw.get("url", f"https://fixture{ci}.example/evidence"),
                excerpt=raw.get("excerpt", "合成证据夹具正文"),
                relation=EvidenceRelation(raw["relation"]),
                authority=raw.get("authority", .9), relevance=raw.get("relevance", .95),
                time_fit=raw.get("time_fit", .9), quality_reason=raw.get("quality_reason"),
            ))
        clusters.append(EvidenceCluster(cluster_id=group.get("cluster_id", f"fixture-cluster-{ci}"), items=items,
                                        independence_reason="synthetic evaluation fixture"))
    return clusters


def _run_pipeline_failure(text: str) -> tuple[str | None, str, bool]:
    class FailingRetriever:
        provider = "synthetic_failure"
        def retrieve(self, claim, *, deadline=None):
            raise SearchProviderError("SEARCH_UNAVAILABLE")

    storage = Storage(":memory:")
    try:
        pipeline = Pipeline(storage, settings=Settings(database_path=Path(":memory:")),
                            retriever=FailingRetriever(), evidence_judge=False, segmenter=False)
        task = pipeline.create(text, 15)
        summary = pipeline.run_sync(task.task_id)
        claims, _ = storage.list_claims(task.task_id, 0, 1000)
        return (claims[0].label.value if claims and claims[0].label else None,
                claims[0].state.value if claims else "missing", bool(summary.failed_providers))
    finally:
        storage.close()


def run_fixtures(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for i, row in enumerate(cases):
        start = time.perf_counter()
        claims, truncated = extract_claims(row["input_text"], f"eval-{i}", limit=row.get("claim_limit", 15))
        predicted = None
        failure = row.get("technical_failure", False)
        actual_state = "done"
        pipeline_failed = False
        if failure:
            predicted, actual_state, pipeline_failed = _run_pipeline_failure(row["input_text"])
        elif claims:
            claim = claims[0]
            if claim.label == ClaimLabel.NOT_APPLICABLE:
                predicted = judge_claim(claim, []).label.value
            else:
                predicted = judge_claim(claim, _fixture_clusters(row.get("evidence", []))).label.value
        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
        checks = {}
        for name, wanted in row.get("functional_checks", {}).items():
            if name == "claim_count":
                checks[name] = len(claims) == wanted
            elif name == "truncated":
                checks[name] = truncated is wanted
            elif name == "evidence_insufficient_not_error":
                checks[name] = not (predicted == "incorrect" and row.get("expected", {}).get("label") == "evidence_insufficient")
            elif name == "technical_failure_unlabeled":
                checks[name] = failure and predicted is None and actual_state == "failed" and pipeline_failed
            elif name == "normalized_claim_contains":
                normalized = "\n".join(c.normalized_claim for c in claims)
                checks[name] = isinstance(wanted, list) and all(isinstance(part, str) and part in normalized for part in wanted)
            else:
                checks[name] = False
        expected_label = row.get("expected", {}).get("label")
        if expected_label == "not_applicable":
            checks["opinion_label_respected"] = predicted == "not_applicable"
        selected_claim = claims[0] if claims else None
        output.append({"case_id": row["case_id"], "expected": expected_label,
                       "predicted": predicted,
                       "technical_failure": failure, "pipeline_state": actual_state if failure else None,
                       "evaluated_claim_index": 0 if selected_claim else None,
                       "normalized_claim": selected_claim.normalized_claim if selected_claim else None,
                       "invalid_output": False, "elapsed_ms": elapsed_ms,
                       "claim_count": len(claims), "truncated": truncated,
                       "functional_checks": checks})
    return output


def _canonical_output(label: Any) -> str:
    return label if isinstance(label, str) and label in LABELS else "unknown"


def calculate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Classification denominator is rows with one of four factual gold labels."""
    matrix = {gold: {pred: 0 for pred in [*CLASSIFICATION_LABELS, "unknown"]} for gold in CLASSIFICATION_LABELS}
    eligible = [r for r in rows if r.get("expected") in CLASSIFICATION_LABELS]
    for row in eligible:
        matrix[row["expected"]][_canonical_output(row.get("predicted")) if _canonical_output(row.get("predicted")) in CLASSIFICATION_LABELS else "unknown"] += 1
    correct = sum(matrix[label][label] for label in CLASSIFICATION_LABELS)
    abstentions = sum(matrix[label]["evidence_insufficient"] + matrix[label]["unknown"] for label in CLASSIFICATION_LABELS)
    invalid_outputs = sum(bool(r.get("invalid_output")) or (r.get("predicted") is not None and _canonical_output(r.get("predicted")) == "unknown") for r in eligible)
    evidence_insufficient_false_error = sum(
        1 for r in eligible if r["expected"] == "evidence_insufficient" and r.get("predicted") == "incorrect"
    )
    per_class = {}
    for label in CLASSIFICATION_LABELS:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        predicted_n = sum(matrix[g][label] for g in CLASSIFICATION_LABELS)
        per_class[label] = {"support": support, "precision": tp / predicted_n if predicted_n else None,
                            "recall": tp / support if support else None}
    human_eligible = [r for r in rows if r.get("source") == "human" and r.get("expected") in CLASSIFICATION_LABELS]
    human_correct = sum(_canonical_output(r.get("predicted")) == r["expected"] for r in human_eligible)
    functional = [r for r in rows if r.get("functional_checks")]
    check_results: dict[str, dict[str, int]] = {}
    for row in functional:
        for name, value in row["functional_checks"].items():
            stats = check_results.setdefault(name, {"passed": 0, "failed": 0})
            stats["passed" if value else "failed"] += 1
    return {"classification": {"denominator": len(eligible), "correct": correct,
             "accuracy": correct / len(eligible) if eligible else None,
             "confusion_matrix_rows_gold_columns_prediction": matrix,
             "abstentions": abstentions, "abstention_rate": abstentions / len(eligible) if eligible else None,
             "invalid_output_count": invalid_outputs,
             "evidence_insufficient_predicted_incorrect": evidence_insufficient_false_error,
             "per_class": per_class},
            "functional_constraints": {"sample_denominator": len(functional), "checks": check_results},
            "data_quality": {
                "human_labelled_sample_count": sum(r.get("source") == "human" for r in rows),
                "human_classification_denominator": len(human_eligible),
                "human_correct": human_correct,
                "human_accuracy": human_correct / len(human_eligible) if human_eligible else None,
                "public_fact_seed_count": sum(r.get("source") == "curated_public_fact_seed" for r in rows),
                "synthetic_sample_count": sum(r.get("source", "synthetic") == "synthetic" for r in rows),
                "real_world_accuracy_claimed": False,
            },
            "timing": {"sample_count": sum(isinstance(r.get("elapsed_ms"), (int, float)) for r in rows),
                       "total_ms": round(sum(r["elapsed_ms"] for r in rows if isinstance(r.get("elapsed_ms"), (int, float))), 3)
                                   if any(isinstance(r.get("elapsed_ms"), (int, float)) for r in rows) else None,
                       "mean_ms": round(sum(r["elapsed_ms"] for r in rows if isinstance(r.get("elapsed_ms"), (int, float))) /
                                         sum(isinstance(r.get("elapsed_ms"), (int, float)) for r in rows), 3)
                                  if any(isinstance(r.get("elapsed_ms"), (int, float)) for r in rows) else None}}


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    cls = metrics["classification"]
    lines = ["# 评估报告", "", f"- 样本来源：{report['source']}", f"- 运行模式：{report['mode']}",
             f"- 说明：{report['caveat']}", f"- 分类分母：{cls['denominator']} 条含四类可核验 gold 标签的样本",
             f"- 人工标注样本：{metrics['data_quality']['human_labelled_sample_count']} 条；公开事实演练样本：{metrics['data_quality']['public_fact_seed_count']} 条；合成样本：{metrics['data_quality']['synthetic_sample_count']} 条",
             f"- 评估集标签一致率：{cls['correct']}/{cls['denominator']} ({_pct(cls['accuracy'])})",
             f"- 真实 AI 回答盲测准确率：{metrics['data_quality']['human_correct']}/{metrics['data_quality']['human_classification_denominator']} ({_pct(metrics['data_quality']['human_accuracy'])})",
             "- 人工盲测准确率只适用于本次人工标注样本，不能外推为全部用户查询的总体准确率。",
             f"- 弃判率：{cls['abstentions']}/{cls['denominator']} ({_pct(cls['abstention_rate'])})；预测为 evidence_insufficient 或 unknown 计为弃判",
             f"- 证据不足误判为错误：{cls['evidence_insufficient_predicted_incorrect']} 条",
             f"- 功能约束状态：{metrics['functional_constraints'].get('status', 'measured')}", "",
             "## 混淆矩阵（行是真值，列是预测；unknown 包括弃判和无效输出）", "",
             "| gold \\ prediction | " + " | ".join([*CLASSIFICATION_LABELS, "unknown"]) + " |",
             "|---|" + "---|" * 5]
    for gold, row in cls["confusion_matrix_rows_gold_columns_prediction"].items():
        lines.append("| " + gold + " | " + " | ".join(str(row[col]) for col in [*CLASSIFICATION_LABELS, "unknown"]) + " |")
    run_info = report.get("run_info")
    if run_info:
        lines.extend(["", "## 系统运行记录", "", f"- 待评系统：{run_info['system']}",
                      f"- 联网搜索：{'是' if run_info['network_enabled'] else '否'}",
                      f"- 搜索来源：{'、'.join(run_info['search_sources']) or '无'}"])
    lines.extend(["", "## 功能约束", "", f"检查样本分母：{metrics['functional_constraints']['sample_denominator']}"])
    for name, count in metrics["functional_constraints"]["checks"].items():
        lines.append(f"- {name}: 通过 {count['passed']}，失败 {count['failed']}")
    total_ms, mean_ms = metrics["timing"]["total_ms"], metrics["timing"]["mean_ms"]
    timing_text = "未测量" if total_ms is None else f"总计 {total_ms} ms；平均 {mean_ms} ms"
    lines.extend(["", "## 耗时", "", timing_text + "。", ""])
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--predictions", type=Path, help="系统导出的 JSON 报告，或含 case_id/label 的预测数组")
    parser.add_argument("--gold", type=Path, help="可选的盲测 gold 文件；与 --cases 分开保存，预测锁定后再解封")
    parser.add_argument("--run-info", type=Path, help="记录待评系统版本、联网状态和搜索来源的 JSON")
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).with_name("results"))
    args = parser.parse_args(argv)
    try:
        if args.gold and not args.predictions:
            raise ValueError("--gold 仅能与 --predictions 同时使用；先运行系统并保存预测")
        run_info = validate_run_info(_load(args.run_info)) if args.run_info else None
        raw_cases = _load(args.cases)
        cases = validate_cases(raw_cases, allow_missing_annotations=bool(args.gold))
        if args.gold:
            cases = merge_gold(cases, _load(args.gold))
        if args.predictions:
            predictions = validate_predictions(cases, load_predictions(args.predictions))
            rows = []
            for i, case in enumerate(cases):
                extracted, _ = extract_claims(case["input_text"], f"blind-{i}", limit=case.get("claim_limit", 15))
                selected = extracted[0] if extracted else None
                prediction = predictions[case["case_id"]]
                rows.append({"case_id": case["case_id"], "source": case.get("source", "synthetic"),
                             "expected": case.get("expected", {}).get("label"),
                             "predicted": prediction,
                             "invalid_output": prediction is not None and prediction not in LABELS,
                             "evaluated_claim_index": 0 if selected else None,
                             "normalized_claim": selected.normalized_claim if selected else None,
                             "elapsed_ms": None, "functional_checks": {}})
            mode = "人工预测对照" if all(c.get("source") == "human" for c in cases) else "预测结果对照"
            source = str(args.predictions)
        else:
            rows = run_fixtures(cases)
            mode, source = "离线合成证据夹具", str(args.cases)
        metrics = calculate_metrics(rows)
        metrics["functional_constraints"]["status"] = "not_measured" if args.predictions else "measured_on_synthetic_fixture"
        if not args.predictions:
            for row, case in zip(rows, cases):
                row["source"] = case.get("source", "synthetic")
        has_human_gold = metrics["data_quality"]["human_labelled_sample_count"] > 0
        caveat = (
            "指标表示本次人工标注样本上的预测与 gold 一致程度；不能外推为所有用户查询的总体准确率。"
            "预测来源与是否联网由运行者记录，脚本不独立验证。"
            if args.predictions and has_human_gold else
            "当前是外部预测文件与样本标签的对照；若预测不是待评系统的输出，不代表系统成绩。"
            if args.predictions else
            "夹具使用合成证据，仅验证离线语义路径和指标流程；不能代表真实联网搜索准确率。"
        )
        report = {"schema_version": 1, "mode": mode, "source": source,
                  "caveat": caveat,
                  "run_info": run_info, "metrics": metrics, "results": rows}
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (args.out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
        print(f"已写入 {args.out_dir / 'report.json'} 和 {args.out_dir / 'report.md'}")
        failed_functional = any(v["failed"] for v in report["metrics"]["functional_constraints"]["checks"].values())
        failed_classification = any(r.get("expected") in CLASSIFICATION_LABELS and _canonical_output(r.get("predicted")) != r.get("expected") for r in rows)
        failed_opinion = any(r.get("expected") == "not_applicable" and r.get("predicted") != "not_applicable" for r in rows)
        return 1 if failed_functional or failed_opinion or (not args.predictions and failed_classification) else 0
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
