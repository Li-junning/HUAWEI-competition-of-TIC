"""Keep ordinary test runs offline and away from the user's database."""

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("VERIFIER_PROVIDER_MODE", "mock")
    monkeypatch.setenv("VERIFIER_JUDGE_MODE", "off")
    monkeypatch.setenv("VERIFIER_SEGMENT_MODE", "rules")
    monkeypatch.setenv("VERIFIER_DATABASE_PATH", str(tmp_path / "test.sqlite3"))
