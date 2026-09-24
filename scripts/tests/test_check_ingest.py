"""Regression checks for a feed that succeeds while extraction is down."""

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "check_ingest.py"


def check(tmp_path, *, failures, batches, dropped, claims=0):
    stats = {
        "ingest": {"feeds": 16, "feeds_failed": 0},
        "extract": {
            "articles": 100,
            "batches": batches,
            "parse_failures": failures,
            "claims": claims,
            "dropped": dropped,
        },
    }
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(stats), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--stats", str(path)],
        capture_output=True, text=True,
    )


def test_invalid_key_blocks_publication(tmp_path):
    result = check(tmp_path, failures=5, batches=5,
                   dropped=["Error code: 401 - authentication_error: API key is invalid."])
    assert result.returncode == 1
    assert "Replace that Actions secret" in result.stdout


def test_all_failed_batches_block_publication(tmp_path):
    result = check(tmp_path, failures=5, batches=5, dropped=["batch timed out"])
    assert result.returncode == 1
    assert "Every extraction batch failed" in result.stdout


def test_quiet_day_with_successful_extraction_is_allowed(tmp_path):
    result = check(tmp_path, failures=0, batches=5, dropped=[])
    assert result.returncode == 0
