import json
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.evaluate import calculate_metrics, load_predictions, main, merge_gold, run_fixtures, validate_cases, validate_predictions, validate_run_info


def test_metrics_report_wrong_and_unknown_predictions_with_clear_denominator():
    rows = [
        {"case_id": "a", "expected": "credible", "predicted": "incorrect"},
        {"case_id": "b", "expected": "evidence_insufficient", "predicted": "incorrect"},
        {"case_id": "c", "expected": "incorrect", "predicted": "nonsense"},
        {"case_id": "d", "expected": "not_applicable", "predicted": "not_applicable"},
    ]
    result = calculate_metrics(rows)["classification"]
    assert result["denominator"] == 3
    assert result["correct"] == 0
    assert result["abstentions"] == 1
    assert result["evidence_insufficient_predicted_incorrect"] == 1
    assert result["confusion_matrix_rows_gold_columns_prediction"]["incorrect"]["unknown"] == 1
    assert result["invalid_output_count"] == 1


def test_fixture_runner_calls_real_extract_and_judge_and_checks_functionality():
    cases = validate_cases(json.loads((Path(__file__).resolve().parents[2] / "evaluation" / "cases.json").read_text(encoding="utf-8-sig")))
    rows = run_fixtures(cases)
    by_id = {row["case_id"]: row for row in rows}
    assert by_id["parallel_refuted"]["predicted"] == "incorrect"
    assert by_id["conflicting_evidence"]["predicted"] == "disputed"
    assert by_id["insufficient_evidence"]["predicted"] == "evidence_insufficient"
    assert by_id["technical_failure"]["predicted"] is None
    assert by_id["technical_failure"]["pipeline_state"] == "failed"
    assert by_id["parallel_refuted"]["functional_checks"]["claim_count"] is True
    assert by_id["complex_conditions_support"]["functional_checks"]["normalized_claim_contains"] is True


def test_prediction_validation_requires_exact_unique_ids_and_accepts_unknown_output():
    gold = [{"case_id": "x", "input_text": "声明", "expected": {"label": "credible"}}]
    assert validate_predictions(gold, [{"case_id": "x", "label": "bad-output"}]) == {"x": "bad-output"}
    for predictions in ([], [{"case_id": "x", "label": "credible"}, {"case_id": "x", "label": "credible"}],
                        [{"case_id": "other", "label": "credible"}], [{"case_id": "x", "label": "credible"}, {"case_id": "extra", "label": "incorrect"}]):
        try:
            validate_predictions(gold, predictions)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid prediction set was accepted")


def test_native_app_export_claims_map_by_target_claim():
    gold = [{"case_id": "x", "input_text": "甲公司的回答", "target_claim": "甲公司于2024年发布产品。",
             "expected": {"label": "credible"}}]
    exported_task = {"task": {"task_id": "t_example"}, "claims": [
        {"source_text": "发布产品", "normalized_claim": "甲公司于2024年发布产品。", "label": "credible"}
    ]}
    assert validate_predictions(gold, exported_task) == {"x": "credible"}


def test_run_info_requires_explicit_network_status_and_sources():
    info = validate_run_info({"system": "release-abc", "network_enabled": True, "search_sources": ["Tavily"]})
    assert info["network_enabled"] is True
    for invalid in ({"system": "x", "search_sources": []},
                    {"system": "x", "network_enabled": False, "search_sources": "none"}):
        try:
            validate_run_info(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid run info was accepted")


def test_prediction_directory_combines_multiple_task_exports_and_rejects_duplicates(tmp_path):
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "task-a.json").write_text(json.dumps({"claims": [
        {"source_text": "甲公司发布产品。", "normalized_claim": "甲公司发布产品。", "label": "credible"}]}), encoding="utf-8")
    (folder / "task-b.json").write_text(json.dumps({"claims": [
        {"source_text": "乙公司发布产品。", "normalized_claim": "乙公司发布产品。", "label": "incorrect"}]}), encoding="utf-8")
    gold = [{"case_id": "a", "input_text": "甲公司发布产品。", "expected": {"label": "credible"}},
            {"case_id": "b", "input_text": "乙公司发布产品。", "expected": {"label": "incorrect"}}]
    assert validate_predictions(gold, load_predictions(folder)) == {"a": "credible", "b": "incorrect"}
    (folder / "task-c.json").write_text(json.dumps({"claims": [
        {"source_text": "甲公司发布产品。", "normalized_claim": "甲公司发布产品。", "label": "credible"}]}), encoding="utf-8")
    try:
        validate_predictions(gold, load_predictions(folder))
    except ValueError as exc:
        assert "同一 case_id" in str(exc)
    else:
        raise AssertionError("duplicate predictions across task exports were accepted")


def test_blind_gold_can_be_kept_separate_until_predictions_are_saved(tmp_path):
    cases = tmp_path / "blind_cases.json"
    gold = tmp_path / "blind_gold.json"
    predictions = tmp_path / "predictions.json"
    cases.write_text(json.dumps([{"case_id": "x", "source": "human", "input_text": "甲公司发布产品。",
                                  "origin": {"provider": "平台", "captured_at": "2026-09-27", "reference": "脱敏样本"}}], ensure_ascii=False), encoding="utf-8")
    gold.write_text(json.dumps([{"case_id": "x", "expected": {"label": "credible"},
                                 "annotation": {"checked_at": "2026-09-27", "annotator": "a", "reviewer": "b",
                                                "label_basis": "官方页面可核对。", "sources": [
                                                    {"url": "https://example.com/source", "relation": "supports", "excerpt": "甲公司发布产品。"}]}}], ensure_ascii=False), encoding="utf-8")
    predictions.write_text(json.dumps([{"case_id": "x", "label": "credible"}]), encoding="utf-8")
    out = tmp_path / "blind-report"
    assert main(["--cases", str(cases), "--predictions", str(predictions), "--gold", str(gold), "--out-dir", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["metrics"]["data_quality"]["human_labelled_sample_count"] == 1
    assert report["metrics"]["classification"]["accuracy"] == 1


def test_human_gold_requires_traceable_sources():
    row = {"case_id": "x", "source": "human", "input_text": "甲公司发布产品。",
           "origin": {"provider": "平台", "captured_at": "2026-09-27", "reference": "case-x"},
           "expected": {"label": "credible"},
           "annotation": {"checked_at": "2026-09-27", "annotator": "a", "reviewer": "b",
                          "label_basis": "checked", "sources": []}}
    try:
        validate_cases([row])
    except ValueError as exc:
        assert "至少一个可追溯证据来源" in str(exc)
    else:
        raise AssertionError("human gold without evidence source was accepted")


def test_empty_samples_duplicate_ids_and_unknown_gold_are_rejected():
    for cases in ([], [{"case_id": "", "input_text": "声明", "expected": {}}],
                  [{"case_id": "x", "input_text": "声明", "expected": {"label": "bad"}}],
                  [{"case_id": "x", "input_text": "声明", "expected": {}}, {"case_id": "x", "input_text": "声明", "expected": {}}]):
        try:
            validate_cases(cases)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid sample set was accepted")


def test_public_fact_seed_is_explicitly_not_independently_reviewed():
    seed = json.loads((Path(__file__).resolve().parents[2] / "evaluation" / "public_fact_seed.json").read_text(encoding="utf-8-sig"))
    validated = validate_cases(seed)
    assert validated[0]["annotation"]["review_status"] == "example_not_independently_reviewed"
    assert "reviewer" not in validated[0]["annotation"]


def test_cli_returns_failure_for_a_fixture_classification_regression(tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"case_id": "regression", "input_text": "测试机构于2024年发布报告。",
                                 "expected": {"label": "credible"}, "evidence": []}], ensure_ascii=False), encoding="utf-8")
    assert main(["--cases", str(cases), "--out-dir", str(tmp_path / "report")]) == 1



def test_prediction_cli_writes_unknowns_without_fake_timing_or_functional_passes(tmp_path):
    cases = tmp_path / "cases.json"
    predictions = tmp_path / "predictions.json"
    labels = ["credible", "incorrect", "evidence_insufficient", "not_applicable"]
    samples = []
    for cid, label, company in zip("abcd", labels, "甲乙丙丁"):
        annotation = {"checked_at": "2026-09-27", "annotator": "a", "reviewer": "b",
                      "label_basis": "人工复核示例。", "sources": []}
        if label in {"credible", "incorrect"}:
            relation = "supports" if label == "credible" else "refutes"
            annotation["sources"] = [{"url": f"https://example.com/{cid}", "relation": relation, "excerpt": "标注夹具摘录"}]
        if label == "evidence_insufficient":
            annotation["search_log"] = ["产品发布 官方"]
        samples.append({"case_id": cid, "input_text": f"{company}公司发布产品。", "source": "human",
                        "origin": {"provider": "测试来源", "captured_at": "2026-09-27", "reference": f"case-{cid}"},
                        "expected": {"label": label}, "annotation": annotation})
    cases.write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
    predictions.write_text(json.dumps([
        {"case_id": "a", "label": "bogus"}, {"case_id": "b", "label": None},
        {"case_id": "c", "label": "incorrect"}, {"case_id": "d", "label": "not_applicable"}
    ]), encoding="utf-8")
    out = tmp_path / "manual-report"
    assert main(["--cases", str(cases), "--predictions", str(predictions), "--out-dir", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["metrics"]["functional_constraints"]["status"] == "not_measured"
    assert report["metrics"]["timing"]["total_ms"] is None
    assert report["metrics"]["timing"]["mean_ms"] is None
    cls = report["metrics"]["classification"]
    assert cls["denominator"] == 3
    assert cls["invalid_output_count"] == 1
    assert cls["evidence_insufficient_predicted_incorrect"] == 1
    matrix = cls["confusion_matrix_rows_gold_columns_prediction"]
    assert matrix["credible"]["unknown"] == 1
    assert matrix["incorrect"]["unknown"] == 1
    assert matrix["evidence_insufficient"]["incorrect"] == 1
    assert "未测量" in (out / "report.md").read_text(encoding="utf-8")
    assert report["metrics"]["data_quality"]["human_labelled_sample_count"] == 4
    assert report["metrics"]["data_quality"]["human_classification_denominator"] == 3
    assert report["metrics"]["data_quality"]["human_accuracy"] == 0
    assert "不能外推" in (out / "report.md").read_text(encoding="utf-8")
    assert "不能外推为所有用户查询" in report["caveat"]
    assert report["results"][0]["evaluated_claim_index"] == 0
    assert report["results"][0]["normalized_claim"]


def test_offline_cli_fails_when_an_opinion_is_predicted_as_fact(tmp_path, monkeypatch):
    import evaluation.evaluate as evaluate
    cases = tmp_path / "opinion.json"
    cases.write_text(json.dumps([{"case_id": "opinion", "input_text": "我认为方案值得推荐。",
                                  "expected": {"label": "not_applicable"}}], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(evaluate, "judge_claim", lambda claim, clusters: SimpleNamespace(label=SimpleNamespace(value="incorrect")))
    assert evaluate.main(["--cases", str(cases), "--out-dir", str(tmp_path / "opinion-report")]) == 1
    report = json.loads((tmp_path / "opinion-report" / "report.json").read_text(encoding="utf-8"))
    assert report["results"][0]["functional_checks"]["opinion_label_respected"] is False
