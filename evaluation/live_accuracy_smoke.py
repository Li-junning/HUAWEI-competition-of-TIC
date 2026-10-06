"""Small opt-in public-fact check through the real search and model adapters.

This is a development smoke check, not an estimate of production accuracy.
It never reads or writes the user's task database.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings
from app.judge import judge_claim
from app.providers import get_evidence_judge, get_search_provider
from app.retrieve import SearchBackedRetriever
from app.schemas import Claim
from app.scoring import support_score


CASES = [
    ("pku_founding_true", "北京大学创立于1898年。", "credible", "https://www.pku.edu.cn/about.html"),
    ("pku_founding_false", "北京大学创立于2010年。", "incorrect", "https://www.pku.edu.cn/about.html"),
    ("water_boiling_true", "在标准大气压下，水的沸点约为100摄氏度。", "credible",
     "https://www.iop.cas.cn/kxcb/kpwz/shzwlzl/201009/t20100919_2965944.html"),
    ("water_boiling_false", "在标准大气压下，水的沸点是90摄氏度。", "incorrect",
     "https://www.iop.cas.cn/kxcb/kpwz/shzwlzl/201009/t20100919_2965944.html"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly use configured Tavily/MiMo keys (paid calls).")
    parser.add_argument("--out", type=Path, default=ROOT / "evaluation/results/live-accuracy-smoke.json")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to authorize real provider calls.")
    get_settings()
    retriever = SearchBackedRetriever(get_search_provider("tavily"))
    judge = get_evidence_judge("mimo")
    report = {
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "mode": "live_public_fact_smoke",
        "scope": "4 curated public facts; not independently labeled AI answers; not production accuracy",
        "search": "tavily", "judge": "mimo", "rows": [],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for case_id, statement, expected, reference in CASES:
        claim = Claim(claim_id=case_id, task_id="accuracy_smoke", source_text=statement,
                      normalized_claim=statement, char_start=0, char_end=len(statement))
        started = time.monotonic()
        row = {"case_id": case_id, "statement": statement, "expected": expected, "reference": reference}
        print(f"{case_id}: searching", flush=True)
        try:
            deadline = started + 120
            clusters = retriever.retrieve(claim, deadline=deadline)
            print(f"{case_id}: judging {sum(len(c.items) for c in clusters)} excerpts", flush=True)
            result = judge_claim(claim, clusters, judge, deadline=deadline)
            row.update(predicted=result.label.value if result.label else None,
                       reason=result.reason, score=support_score(result),
                       queries=result.queries,
                       evidence=[c.model_dump(mode="json") for c in result.evidence_clusters])
        except Exception as exc:
            row.update(predicted=None, technical_error=getattr(exc, "code", type(exc).__name__))
        row["matched"] = row["predicted"] == expected
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["rows"].append(row)
        report["matched"] = sum(r["matched"] for r in report["rows"])
        report["total"] = len(report["rows"])
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{case_id}: {row['predicted']} (expected {expected})", flush=True)
    return 0 if report["matched"] == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
