import unittest

from app.citations import (
    CitationInput,
    PaperMetadata,
    assess_claim_support,
    build_paper_check,
    compare_metadata,
    metadata_from_record,
    normalize_doi,
)


class CitationTests(unittest.TestCase):
    def test_doi_and_record_normalization(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1000/XYZ."), "10.1000/xyz")
        record = metadata_from_record(
            {"title": ["A Study"], "author": [{"family": "Doe", "given": "Jane"}], "published": {"date-parts": [[2024]]}, "DOI": "10.1000/xyz"},
            source="crossref",
        )
        self.assertEqual(record.year, 2024)
        self.assertEqual(record.doi, "10.1000/xyz")

    def test_existence_and_claim_support_are_independent(self):
        citation = CitationInput(title="A Study", authors=("Jane Doe",), year=2024, doi="10.1000/xyz")
        candidate = PaperMetadata("A Study", ("Jane Doe",), 2024, None, "10.1000/xyz", "semantic_scholar")
        check = build_paper_check(citation, [candidate])
        self.assertEqual(check.existence_status, "found")
        self.assertEqual(check.claim_support_status, "not_checked")
        assess_claim_support(check, evidence={"e1": "The abstract says X."}, relations={"e1": "supports"}, excerpts={"e1": "The abstract says X."})
        self.assertEqual(check.existence_status, "found")
        self.assertEqual(check.claim_support_status, "supported")

    def test_bad_excerpt_cannot_support_and_mismatch_is_not_fabrication_proof(self):
        citation = CitationInput(title="A Study", authors=("Jane Doe",), year=2024)
        candidate = PaperMetadata("Other Study", ("Someone Else",), 2023, None, None, "crossref")
        check = build_paper_check(citation, [candidate])
        self.assertEqual(check.existence_status, "not_found")
        assess_claim_support(check, evidence={"e1": "real text"}, relations={"e1": "supports"}, excerpts={"e1": "invented text"})
        self.assertEqual(check.claim_support_status, "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
