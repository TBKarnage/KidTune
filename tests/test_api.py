from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import CatalogTrack, ExplicitVerdict, RequestStatus


def _make_client(tmp_path: Path) -> TestClient:
    cfg = Settings(
        data_dir=tmp_path / "data",
        music_dir=tmp_path / "music",
        parent_pin="4321",
        enable_background_worker=False,
        check_lyrics_on_search=False,
    )
    app = create_app(cfg)
    return TestClient(app)


def test_pwa_routes_and_bootstrap(tmp_path: Path):
    with _make_client(tmp_path) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/").status_code == 200
        assert client.get("/manifest.webmanifest").status_code == 200
        assert client.get("/sw.js").status_code == 200
        boot = client.get("/api/bootstrap").json()
        assert boot["parent_authenticated"] is False
        assert "Alex" in boot["kid_profiles"]


def test_kid_request_safety_and_parent_approval_flow(tmp_path: Path, monkeypatch):
    clean_track = CatalogTrack(
        track_id="1",
        title="Happy",
        artist="Pharrell Williams",
        album="G I R L",
        year="2014",
        duration_sec=233,
        explicit_verdict=ExplicitVerdict.CLEAN,
    )
    explicit_track = CatalogTrack(
        track_id="2",
        title="Explicit Song",
        artist="Rapper",
        album="Album",
        year="2024",
        duration_sec=190,
        explicit_verdict=ExplicitVerdict.EXPLICIT,
        is_explicit=True,
    )

    monkeypatch.setattr(
        "app.main.search_catalog",
        AsyncMock(return_value=[clean_track, explicit_track]),
    )
    monkeypatch.setattr(
        "app.services.request_pipeline.match_text_request",
        AsyncMock(return_value=(None, [])),
    )
    monkeypatch.setattr(
        "app.services.request_pipeline.find_clean_alternative",
        AsyncMock(return_value=None),
    )

    with _make_client(tmp_path) as client:
        # Search returns safety assessments
        search_res = client.get("/api/search?q=happy").json()
        assert len(search_res) == 2
        assert search_res[0]["safety"]["allowed_auto"] is True
        assert search_res[1]["safety"]["blocked"] is True

        # Clean kid pick auto-queues
        r_clean = client.post(
            "/api/requests",
            json={
                "query": "Happy by Pharrell Williams",
                "requester_name": "Alex",
                "source": "ipad",
                "catalog_track": clean_track.model_dump(),
            },
        )
        assert r_clean.status_code == 201
        assert r_clean.json()["status"] == RequestStatus.QUEUED.value

        # Explicit kid pick blocks
        r_exp = client.post(
            "/api/requests",
            json={
                "query": "Explicit Song",
                "requester_name": "Alex",
                "source": "ipad",
                "catalog_track": explicit_track.model_dump(),
            },
        )
        assert r_exp.status_code == 201
        assert r_exp.json()["status"] == RequestStatus.BLOCKED.value

        # Unmatched custom request cannot spoof source="parent" and routes to pending_approval
        r_custom = client.post(
            "/api/requests",
            json={
                "query": "Obscure Indie Kid Song",
                "requester_name": "Alex",
                "source": "parent",
            },
        )
        assert r_custom.status_code == 201
        custom_data = r_custom.json()
        assert custom_data["source"] == "ipad"
        assert custom_data["status"] == RequestStatus.PENDING_APPROVAL.value

        # Parent endpoints require PIN login
        assert client.get("/api/parent/settings").status_code == 401
        assert client.post("/api/parent/login", json={"pin": "0000"}).status_code == 401
        login_res = client.post("/api/parent/login", json={"pin": "4321"})
        assert login_res.status_code == 200

        # Parent approves and edits metadata
        approve_res = client.post(
            f"/api/parent/requests/{custom_data['id']}/approve",
            json={"artist": "Local Band", "album": "Kids Live"},
        )
        assert approve_res.status_code == 200
        assert approve_res.json()["status"] == RequestStatus.QUEUED.value
        assert approve_res.json()["artist"] == "Local Band"

        # Parent cannot approve a blocked explicit song without override or clean alt
        bad_approve = client.post(
            f"/api/parent/requests/{r_exp.json()['id']}/approve",
            json={},
        )
        assert bad_approve.status_code == 400

        # Parent changes PIN and verifies old PIN fails
        pin_res = client.post(
            "/api/parent/pin",
            json={"current_pin": "4321", "new_pin": "9876"},
        )
        assert pin_res.status_code == 200
        client.post("/api/parent/logout")
        assert client.post("/api/parent/login", json={"pin": "4321"}).status_code == 401
        assert client.post("/api/parent/login", json={"pin": "9876"}).status_code == 200
