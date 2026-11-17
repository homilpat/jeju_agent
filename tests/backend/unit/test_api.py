import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from jeju_lifepath.api import store as store_module
from jeju_lifepath.api.app import create_app
from tests.backend.fixtures.user_text import assert_user_text

PASSWORD = "jeju-walk-2026"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "SCRYPT_N", 2**10)  # 테스트 속도용, 실제 값은 2^17
    processed = tmp_path / "processed"
    processed.mkdir()
    features = [
        {
            "type": "Feature",
            "id": f"row-{i}",
            "geometry": {"type": "Point", "coordinates": [126.5 + i / 100, 33.5]},
            "properties": {},
        }
        for i in range(5)
    ]
    (processed / "spots.geojson").write_text(
        json.dumps({"dataset_id": "spots", "features": features}), encoding="utf-8"
    )
    return TestClient(create_app(tmp_path / "app.sqlite3", tmp_path / "no-dist", processed))


def signup(client, username="tester", password=PASSWORD):
    return client.post("/api/auth/signup", json={"username": username, "password": password})


def planner(n_places=2, **extra):
    places = [
        {"source": "public", "ref": f"spots/row-{i}", "name": f"장소{i}"} for i in range(n_places)
    ]
    return {
        "title": "제주 동쪽",
        "places": places,
        "route_choices": ["SAFETY"] * max(0, n_places - 1),
        **extra,
    }


def test_signup_login_logout_flow(client):
    assert signup(client).status_code == 201
    assert client.get("/api/auth/me").json() == {"username": "tester"}
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"username": "tester", "password": PASSWORD}
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me").status_code == 200


def test_password_is_not_stored_in_plain_text(client, tmp_path):
    signup(client)
    db = sqlite3.connect(tmp_path / "app.sqlite3")
    dump = "\n".join(db.iterdump())
    assert PASSWORD not in dump


@pytest.mark.parametrize(
    ("username", "password"),
    [("ab", PASSWORD), ("한글아이디", PASSWORD), ("tester", "short"), ("tester", "x" * 65)],
)
def test_signup_rejects_bad_credentials(client, username, password):
    res = signup(client, username, password)
    assert res.status_code == 400
    assert_user_text(res.json()["message"])


def test_duplicate_username_and_wrong_password(client):
    signup(client)
    dup = signup(client)
    assert dup.status_code == 409
    bad = client.post("/api/auth/login", json={"username": "tester", "password": "wrong-pass"})
    assert bad.status_code == 401
    for res in (dup, bad):
        assert_user_text(res.json()["message"])


def test_planner_crud(client):
    signup(client)
    pid = client.post("/api/planners", json=planner()).json()["id"]
    assert client.get("/api/planners").json()[0]["place_count"] == 2
    assert client.get(f"/api/planners/{pid}").json()["title"] == "제주 동쪽"
    assert client.put(f"/api/planners/{pid}", json=planner(3, title="바뀐 이름")).status_code == 200
    assert client.get(f"/api/planners/{pid}").json()["title"] == "바뀐 이름"
    assert client.delete(f"/api/planners/{pid}").status_code == 204
    assert client.get(f"/api/planners/{pid}").status_code == 404


def test_planners_are_private_to_their_owner(client):
    signup(client, "alice")
    pid = client.post("/api/planners", json=planner()).json()["id"]
    client.post("/api/auth/logout")
    signup(client, "bob")
    assert client.get("/api/planners").json() == []
    assert client.get(f"/api/planners/{pid}").status_code == 404
    assert client.put(f"/api/planners/{pid}", json=planner()).status_code == 404
    assert client.delete(f"/api/planners/{pid}").status_code == 404


@pytest.mark.parametrize("source", ["kakao", "public"])
def test_client_coordinates_are_rejected(client, source):
    """카카오 좌표를 공공데이터 장소로 위장해 저장하는 우회도 막는다."""
    signup(client)
    body = planner(1)
    body["places"] = [
        {"source": source, "ref": "spots/row-0", "name": "어딘가", "lon": 126.49, "lat": 33.5}
    ]
    res = client.post("/api/planners", json=body)
    assert res.status_code == 422
    assert_user_text(res.json()["message"])


def test_public_coordinates_come_from_server_data(client):
    signup(client)
    pid = client.post("/api/planners", json=planner(2)).json()["id"]
    places = client.get(f"/api/planners/{pid}").json()["places"]
    assert (places[1]["lon"], places[1]["lat"]) == (126.51, 33.5)


def test_unknown_public_place_is_rejected(client):
    signup(client)
    body = planner(1)
    body["places"][0]["ref"] = "spots/row-999"
    res = client.post("/api/planners", json=body)
    assert res.status_code == 400
    assert_user_text(res.json()["message"])


def test_kakao_place_is_stored_without_coordinates(client):
    signup(client)
    body = planner(0)
    body["places"] = [{"source": "kakao", "ref": "10808261", "name": "제주국제공항"}]
    pid = client.post("/api/planners", json=body).json()["id"]
    stored = client.get(f"/api/planners/{pid}").json()["places"][0]
    assert "lon" not in stored
    assert "lat" not in stored


def test_session_cookie_is_secure_behind_https_proxy(client):
    res = client.post(
        "/api/auth/signup",
        json={"username": "tester", "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert "secure" in res.headers["set-cookie"].lower()
    assert "secure" not in signup(client, "other").headers["set-cookie"].lower()


def test_route_choices_must_match_segments(client):
    signup(client)
    res = client.post("/api/planners", json=planner(3, route_choices=["SAFETY"]))
    assert res.status_code == 422


def test_planner_requires_login(client):
    res = client.get("/api/planners")
    assert res.status_code == 401
    assert_user_text(res.json()["message"])


def test_delete_account_removes_planners(client, tmp_path):
    signup(client)
    client.post("/api/planners", json=planner())
    assert client.delete("/api/account").status_code == 204
    db = sqlite3.connect(tmp_path / "app.sqlite3")
    assert db.execute("SELECT COUNT(*) FROM planners").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        (
            "post",
            "/api/planners",
            {"content": b"\xb0\xa1 not json", "headers": {"Content-Type": "application/json"}},
        ),
        ("get", "/api/no-such-thing", {}),
        ("get", "/api/planners/not-a-number", {}),
    ],
)
def test_framework_errors_are_shown_in_korean(client, method, path, kwargs):
    signup(client)
    res = getattr(client, method)(path, **kwargs)
    assert res.status_code >= 400
    message = res.json()["message"]
    assert_user_text(message)
    assert any("가" <= ch <= "힣" for ch in message)


# routes -------------------------------------------------------------------
def route_client(tmp_path, monkeypatch, factory):
    monkeypatch.setattr(store_module, "SCRYPT_N", 2**10)
    processed = tmp_path / "processed"
    processed.mkdir(exist_ok=True)
    return TestClient(
        create_app(tmp_path / "app.sqlite3", tmp_path / "no-dist", processed, factory)
    )


QUERY = {"origin": {"lat": 33.5, "lon": 126.5}, "destination": {"lat": 33.5, "lon": 126.502}}


def test_routes_need_login_and_jeju_coordinates(tmp_path, monkeypatch):
    c = route_client(tmp_path, monkeypatch, lambda: None)
    assert c.post("/api/routes/compare", json=QUERY).status_code == 401
    signup(c)
    seoul = {**QUERY, "origin": {"lat": 37.56, "lon": 126.97}}
    r = c.post("/api/routes/compare", json=seoul)
    assert r.status_code == 422
    assert_user_text(r.json()["message"])


def test_routes_without_road_data_says_so(tmp_path, monkeypatch):
    c = route_client(tmp_path, monkeypatch, lambda: None)
    signup(c)
    r = c.post("/api/routes/compare", json=QUERY)
    assert r.status_code == 503
    assert_user_text(r.json()["message"])


def test_routes_return_three_lines(tmp_path, monkeypatch):
    from jeju_lifepath.geo.osm import WalkNetwork, build_segments  # noqa: PLC0415
    from jeju_lifepath.routing.walk import WalkRouter  # noqa: PLC0415
    from tests.backend.unit.test_walk import ELEMENTS  # noqa: PLC0415

    calls = []

    def factory():
        calls.append(1)
        return WalkRouter(WalkNetwork(build_segments(ELEMENTS)), None)

    c = route_client(tmp_path, monkeypatch, factory)
    signup(c)
    body = c.post("/api/routes/compare", json=QUERY).json()
    c.post("/api/routes/compare", json=QUERY)
    assert len(calls) == 1  # 길 자료는 한 번만 불러온다
    assert [r["kind"] for r in body["routes"]] == ["SHORTEST", "BALANCED", "SAFETY"]
    assert all(r["found"] and len(r["line"]) >= 2 for r in body["routes"])
    for r in body["routes"]:
        for t in r["text"]:
            assert_user_text(t)
