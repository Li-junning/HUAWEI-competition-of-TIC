"""Regression coverage for cross-sentence claim subjects."""

import time

import pytest

from app.extract import extract_claims, utf16_len
from app.pipeline import Pipeline
from app.schemas import ClaimState
from app.storage import Storage
from test_segmentation import segmenter_for


@pytest.mark.parametrize("mode", ["rules", "semantic_model"])
def test_later_sentence_resolves_school_and_discipline(mode):
    first = "根据软科世界一流学科排名，北方工业大学上榜的学科是电力电子工程。"
    second = "该学科在2020年软科世界一流学科排名中位列全球前400名。"
    text = first + second
    spans = None if mode == "rules" else segmenter_for([first, second]).split(
        text, deadline=time.monotonic() + 10)
    claims, _ = extract_claims(text, "t", spans=spans)
    assert [claim.source_text for claim in claims] == [first, second]
    assert claims[1].normalized_claim == (
        "北方工业大学的电力电子工程学科在2020年软科世界一流学科排名中位列全球前400名。"
    )
    assert claims[1].char_start == utf16_len(first)
    assert claims[1].char_end == utf16_len(text)


@pytest.mark.parametrize("first", [
    "北方工业大学上榜的学科是电力电子工程和计算机科学。",
    "甲大学上榜的学科是计算机科学，乙大学上榜的学科是电力电子工程。",
])
def test_ambiguous_discipline_reference_is_not_guessed(first):
    second = "该学科在2020年位列全球前400名。"
    claims, _ = extract_claims(first + second, "t")
    assert claims[-1].normalized_claim == second
    assert claims[-1].state == ClaimState.UNCHECKED


def test_reference_does_not_cross_paragraph():
    first = "北方工业大学上榜的学科是电力电子工程。"
    second = "该学科在2020年位列全球前400名。"
    claims, _ = extract_claims(first + "\n\n" + second, "t")
    assert claims[-1].normalized_claim == second
    assert claims[-1].state == ClaimState.UNCHECKED



def test_unresolved_reference_never_reaches_retrieval_or_retry():
    class RecordingRetriever:
        provider = "fixture"

        def __init__(self):
            self.seen = []

        def retrieve(self, claim):
            self.seen.append(claim.normalized_claim)
            return []

    first = "甲大学上榜的学科是计算机科学，乙大学上榜的学科是电力电子工程。"
    second = "该学科在2020年位列全球前400名。"
    storage = Storage(":memory:")
    retriever = RecordingRetriever()
    try:
        pipeline = Pipeline(storage, retriever=retriever)
        task = pipeline.create(first + second, 15)
        summary = pipeline.run_sync(task.task_id)
        claims, _ = storage.list_claims(task.task_id)
        assert summary.claims_unchecked == 1
        assert claims[-1].state == ClaimState.UNCHECKED
        assert len(retriever.seen) == 1
        assert storage.begin_claim_retry(claims[-1].claim_id, 2)[1] == "RETRY_NOT_ALLOWED"
    finally:
        storage.close()

def test_semantic_split_in_one_sentence_inherits_unique_subject():
    first = "北方工业大学上榜的学科是电力电子工程，"
    second = "该学科位列全球前400名。"
    text = first + second
    spans = segmenter_for([first, second]).split(text, deadline=time.monotonic() + 10)
    claims, _ = extract_claims(text, "t", spans=spans)
    assert claims[1].normalized_claim == "北方工业大学的电力电子工程学科位列全球前400名。"


def test_resolved_reference_reaches_retrieval_with_explicit_subject():
    class RecordingRetriever:
        provider = "fixture"

        def __init__(self):
            self.seen = []

        def retrieve(self, claim):
            self.seen.append(claim.normalized_claim)
            return []

    text = (
        "北方工业大学上榜的学科是电力电子工程。"
        "该学科在2020年软科世界一流学科排名中位列全球前400名。"
    )
    storage = Storage(":memory:")
    retriever = RecordingRetriever()
    try:
        pipeline = Pipeline(storage, retriever=retriever)
        task = pipeline.create(text, 15)
        pipeline.run_sync(task.task_id)
        assert len(retriever.seen) == 2
        assert retriever.seen[1].startswith("北方工业大学的电力电子工程学科在2020年")
    finally:
        storage.close()