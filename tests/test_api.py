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


def test_parent_downloads_isolated_and_cleanup_works(tmp_path: Path, monkeypatch):
    clean_track = CatalogTrack(
        track_id="10",
        title="Firework",
        artist="Katy Perry",
        album="Teenage Dream",
        year="2010",
        duration_sec=228,
        explicit_verdict=ExplicitVerdict.CLEAN,
    )
    explicit_track = CatalogTrack(
        track_id="11",
        title="Popular Monster",
        artist="Falling In Reverse",
        album="Popular Monster",
        year="2024",
        duration_sec=220,
        explicit_verdict=ExplicitVerdict.EXPLICIT,
        is_explicit=True,
    )
    monkeypatch.setattr(
        "app.services.request_pipeline.find_clean_alternative",
        AsyncMock(return_value=None),
    )

    with _make_client(tmp_path) as client:
        # Kid submits a clean song and an explicit song
        kid_clean = client.post(
            "/api/requests",
            json={
                "query": "Firework by Katy Perry",
                "requester_name": "Alex",
                "source": "ipad",
                "catalog_track": clean_track.model_dump(),
            },
        ).json()
        kid_exp = client.post(
            "/api/requests",
            json={
                "query": "Popular Monster",
                "requester_name": "Alex",
                "source": "ipad",
                "catalog_track": explicit_track.model_dump(),
            },
        ).json()
        assert kid_exp["status"] == RequestStatus.BLOCKED.value

        # Mark kid clean track completed so we can test cleanup & local cover
        album_dir = tmp_path / "music" / "Katy Perry" / "Teenage Dream (2010)"
        album_dir.mkdir(parents=True, exist_ok=True)
        (album_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
        client.app.state.db.update_request(
            kid_clean["id"],
            status=RequestStatus.COMPLETED,
            file_path=str(album_dir / "01 - Firework.m4a"),
        )

        # Cleanup & delete MUST require Parent authentication (denied for kids)
        assert client.post("/api/requests/cleanup", json={"scope": "completed"}).status_code == 401
        assert client.delete(f"/api/requests/{kid_clean['id']}").status_code == 401

        # Parent logs in and downloads an explicit song with override
        assert client.get("/api/parent/downloads").status_code == 401
        assert client.post("/api/parent/login", json={"pin": "4321"}).status_code == 200
        parent_dl = client.post(
            "/api/requests",
            json={
                "query": "Popular Monster by Falling In Reverse",
                "requester_name": "Parent",
                "source": "parent",
                "catalog_track": explicit_track.model_dump(),
                "parent_override": True,
            },
        )
        assert parent_dl.status_code == 201
        assert parent_dl.json()["source"] == "parent"
        assert parent_dl.json()["status"] == RequestStatus.QUEUED.value

        # Main kid Song Requests list MUST NOT include parent downloads
        kid_list = client.get("/api/requests").json()
        assert all(r["source"] != "parent" for r in kid_list)
        assert len(kid_list) == 2

        # Parent Downloads endpoint returns ONLY parent downloads
        parent_list = client.get("/api/parent/downloads").json()
        assert len(parent_list) == 1
        assert parent_list[0]["id"] == parent_dl.json()["id"]

        # Artwork proxy rejects SSRF hosts, while local request artwork & library covers work
        assert client.get("/api/artwork?url=http://127.0.0.1:8080/secret.jpg").status_code == 400
        art_resp = client.get(f"/api/requests/{kid_clean['id']}/artwork")
        assert art_resp.status_code == 200
        assert art_resp.headers["content-type"].startswith("image/")
        covers = client.get("/api/library/covers").json()
        assert len(covers) >= 1

        # Parent cleans up kid completed requests while leaving blocked row and parent downloads untouched
        cleanup_res = client.post("/api/requests/cleanup?queue=kid", json={"scope": "completed"}).json()
        assert cleanup_res["deleted_count"] == 1
        assert len(client.get("/api/requests").json()) == 1
        assert len(client.get("/api/parent/downloads").json()) == 1
        # Filesystem cover.jpg still feeds floating album covers after cleanup!
        covers_after = client.get("/api/library/covers").json()
        assert len(covers_after) >= 1


def test_album_search_details_and_download_flow(tmp_path: Path, monkeypatch):
    from app.models import CatalogAlbum

    album = CatalogAlbum(
        provider="itunes",
        album_id="9001",
        title="Family Hits",
        artist="Various Kids",
        year="2024",
        track_count=2,
        explicit_verdict=ExplicitVerdict.CLEAN,
    )
    t_clean = CatalogTrack(
        provider="itunes",
        track_id="90011",
        title="Sunshine Day",
        artist="Various Kids",
        album="Family Hits",
        track_number=1,
        track_count=2,
        explicit_verdict=ExplicitVerdict.CLEAN,
    )
    t_explicit = CatalogTrack(
        provider="itunes",
        track_id="90012",
        title="Explicit Track",
        artist="Various Kids",
        album="Family Hits",
        track_number=2,
        track_count=2,
        explicit_verdict=ExplicitVerdict.EXPLICIT,
        is_explicit=True,
    )

    monkeypatch.setattr("app.main.search_albums", AsyncMock(return_value=[album]))
    monkeypatch.setattr("app.main.get_album_tracks", AsyncMock(return_value=(album, [t_clean, t_explicit])))
    monkeypatch.setattr(
        "app.services.request_pipeline.get_album_tracks",
        AsyncMock(return_value=(album, [t_clean, t_explicit])),
    )
    monkeypatch.setattr(
        "app.services.request_pipeline.find_clean_alternative",
        AsyncMock(return_value=None),
    )

    with _make_client(tmp_path) as client:
        # Album search & details
        albums = client.get("/api/search/albums?q=family").json()
        assert len(albums) == 1
        assert albums[0]["album"]["title"] == "Family Hits"

        details = client.get("/api/albums/itunes/9001").json()
        assert len(details["tracks"]) == 2
        assert details["tracks"][0]["safety"]["allowed_auto"] is True
        assert details["tracks"][1]["safety"]["blocked"] is True

        # Kid album request skips explicit track and queues only safe track
        kid_alb = client.post(
            "/api/requests/album",
            json={
                "provider": "itunes",
                "album_id": "9001",
                "requester_name": "Alex",
                "source": "ipad",
                "skip_explicit": True,
            },
        )
        assert kid_alb.status_code == 201
        data = kid_alb.json()
        assert data["queued_count"] == 1
        assert data["skipped_explicit_count"] == 1

        # Parent full album download with override queues ALL tracks into Parent Downloads only
        client.post("/api/parent/login", json={"pin": "4321"})
        parent_alb = client.post(
            "/api/requests/album",
            json={
                "provider": "itunes",
                "album_id": "9001",
                "requester_name": "Parent",
                "source": "parent",
                "skip_explicit": False,
                "parent_override": True,
            },
        )
        assert parent_alb.status_code == 201
        assert parent_alb.json()["queued_count"] == 2
        assert len(client.get("/api/parent/downloads").json()) == 2
        # Kid list still has only the 1 kid-safe track
        assert len(client.get("/api/requests").json()) == 1

