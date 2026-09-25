import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from open_window import open_window
import open_window as rollover


def test_rollover_freezes_history_and_clears_live_state(tmp_path, monkeypatch):
    original = {
        "config": {
            "windowSlug": "2026-summer",
            "windowName": "Summer 2026",
            "deadline": "2026-09-01T22:00:00Z",
            "site": {"baseUrl": "https://example.com/transfers"},
            "provenClubs": ["Arsenal"],
        },
        "deals": [{"id": "historic-transfer"}],
        "clubs": {"Arsenal": {"needs": "old context"}},
    }
    (tmp_path / "data.json").write_text(json.dumps(original))
    (tmp_path / "index.html").write_text("<html></html>")
    renders = []
    monkeypatch.setattr(rollover, "ROOT", tmp_path)
    monkeypatch.setattr(rollover, "render", lambda data, dest, template: renders.append(dest))
    future = str(datetime.now(timezone.utc).year + 2) + "-02-01T23:00:00Z"
    open_window("2028-winter", "Winter 2028", future, "Closes 1 Feb")

    frozen = json.loads((tmp_path / "windows/2026-summer/data.json").read_text())
    live = json.loads((tmp_path / "data.json").read_text())
    assert frozen["deals"] == original["deals"]
    assert frozen["config"]["site"]["baseUrl"].endswith("/windows/2026-summer")
    assert live["deals"] == []
    assert live["clubs"] == {"Arsenal": {}}
    assert live["config"]["archives"] == [
        {"slug": "2026-summer", "name": "Summer 2026"}
    ]
    assert renders == [tmp_path / "windows/2026-summer", tmp_path]
