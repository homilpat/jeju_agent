"""CCTV 근처 길 선택, 범죄주의구간 지도 그림, 근처 시설 찾기."""

import json

import pytest
from fastapi.testclient import TestClient

from jeju_lifepath.api import store as store_module
from jeju_lifepath.api.app import create_app, load_toilets, nearest, toilets_in
from jeju_lifepath.geo.osm import WalkNetwork, build_segments
from jeju_lifepath.presentation.route import render_plan
from jeju_lifepath.routing.walk import CctvPoints, WalkRouter
from jeju_lifepath.schemas.common import RoutePreference
from tests.backend.fixtures.user_text import assert_user_text
from tests.backend.unit.test_api import planner, signup
from tests.backend.unit.test_walk import ELEMENTS, LAT, LON, NODES

PNG = b"\x89PNG\r\n\x1a\nfake"

TOILET_HEADER = (
    "화장실명,소재지도로명주소,소재지지번주소,개방시간,개방시간상세,"
    "남성용-장애인용대변기수,여성용-장애인용대변기수,기저귀교환대유무,비상벨설치여부"
)
TOILET_ROWS = [
    "애월 화장실,,제주특별자치도 제주시 애월읍 고내리 1,상시,,1,0,Y,N",
    "동네 화장실,제주특별자치도 제주시 임항로 111,,정시,09:00~18:00,0,0,N,Y",
    "서귀포 화장실,제주특별자치도 서귀포시 중앙로 1,,상시,,0,0,N,N",
]


@pytest.fixture
def toilet_file(tmp_path):
    path = tmp_path / "toilets.csv"
    path.write_bytes(("\n".join([TOILET_HEADER, *TOILET_ROWS]) + "\n").encode("cp949"))
    return path


@pytest.fixture
def client(tmp_path, monkeypatch, toilet_file):
    monkeypatch.setattr(store_module, "SCRYPT_N", 2**10)
    processed = tmp_path / "processed"
    processed.mkdir()
    wifi = [
        {
            "type": "Feature",
            "id": f"row-{i}",
            "geometry": {"type": "Point", "coordinates": [126.5 + i / 1000, 33.5]},
            "properties": {"place": "같은 공원" if i < 3 else f"공원_{i}", "category": "공원"},
        }
        for i in range(10)
    ]
    (processed / "hub-wifi.geojson").write_text(
        json.dumps({"dataset_id": "hub-wifi", "features": wifi}), encoding="utf-8"
    )
    fetched = []

    def fake_fetch(bbox, width, height):
        fetched.append((bbox, width, height))
        return None if width == 13 else PNG

    app = create_app(
        tmp_path / "app.sqlite3",
        tmp_path / "no-dist",
        processed,
        lambda: None,
        crime_fetch=fake_fetch,
        toilet_file=toilet_file,
    )
    c = TestClient(app)
    c.fetched = fetched
    return c


# --- CCTV ---------------------------------------------------------------


def test_cctv_only_counts_when_user_asks():
    # 위로 돌아가는 길(1-4-5-6-3)에서 약 89m, 아래 직선길에서 약 122m 떨어진 CCTV
    cctv = CctvPoints([(LON + 0.001, LAT + 0.0011)])
    router = WalkRouter(WalkNetwork(build_segments(ELEMENTS)), None, cctv)
    off = router.plan(NODES[1], NODES[3])
    assert off.routes[RoutePreference.SAFETY].result.indicator_breakdown.get("cctv_index") is None

    on = router.plan(NODES[1], NODES[3], prefer_cctv=True)
    short = on.routes[RoutePreference.SHORTEST].result
    safe = on.routes[RoutePreference.SAFETY].result
    assert safe.indicator_breakdown["cctv_index"] > short.indicator_breakdown["cctv_index"]
    for t in [
        *render_plan(on)["comparison"],
        *(t for r in render_plan(on)["routes"] for t in r["text"]),
    ]:
        assert_user_text(t)


def test_cctv_radius_is_100m():
    from shapely.geometry import LineString  # noqa: PLC0415

    cctv = CctvPoints([(LON, LAT)])
    # 위도 0.0008도 ≈ 89m, 0.001도 ≈ 111m
    assert cctv.near(LineString([(LON - 0.001, LAT + 0.0008), (LON + 0.001, LAT + 0.0008)]))
    assert not cctv.near(LineString([(LON - 0.001, LAT + 0.001), (LON + 0.001, LAT + 0.001)]))


def test_cctv_loader_keeps_only_pedestrian_purposes(tmp_path):
    hub = tmp_path / "hub.csv"
    hub.write_bytes(
        "설치목적,위도,경도\n생활방범,33.5,126.5\n교통단속,33.6,126.6\n어린이보호,33.7,126.7\n".encode(
            "cp949"
        )
    )
    std = tmp_path / "std.csv"
    std.write_bytes("설치목적구분,WGS84위도,WGS84경도\n생활방범,33.5,126.5\n".encode("cp949"))
    assert len(CctvPoints.from_csvs([hub, std, tmp_path / "없음.csv"]).points_m) == 2


def wifi_planner(**extra):
    body = planner(**extra)
    for i, place in enumerate(body["places"]):
        place["ref"] = f"hub-wifi/row-{i}"
    return body


def test_planner_remembers_cctv_answer(client):
    signup(client)
    pid = client.post("/api/planners", json=wifi_planner(prefer_cctv=True)).json()["id"]
    assert client.get(f"/api/planners/{pid}").json()["prefer_cctv"] is True
    pid = client.post("/api/planners", json=wifi_planner()).json()["id"]
    assert client.get(f"/api/planners/{pid}").json()["prefer_cctv"] is None


# --- 범죄주의구간 지도 그림 -------------------------------------------------

VIEW = {"west": 126.48, "south": 33.47, "east": 126.56, "north": 33.53, "width": 300, "height": 200}


def test_crime_overlay_needs_login_and_returns_png(client):
    assert client.get("/api/overlays/crime", params=VIEW).status_code == 401
    signup(client)
    r = client.get("/api/overlays/crime", params=VIEW)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == PNG
    (x1, y1, x2, y2), w, h = client.fetched[-1]
    assert x1 < x2
    assert y1 < y2
    assert (w, h) == (300, 200)


@pytest.mark.parametrize(
    "bad",
    [
        {"west": 126.97, "south": 37.5, "east": 127.0, "north": 37.6},  # 서울
        {"west": 126.56, "east": 126.48},  # 뒤집힌 사각형
        {"width": 5000},
    ],
)
def test_crime_overlay_rejects_bad_view(client, bad):
    signup(client)
    r = client.get("/api/overlays/crime", params={**VIEW, **bad})
    assert r.status_code in (400, 422)
    assert_user_text(r.json()["message"])


def test_crime_overlay_failure_is_korean(client):
    signup(client)
    r = client.get("/api/overlays/crime", params={**VIEW, "width": 13})
    assert r.status_code == 502
    assert_user_text(r.json()["message"])


# --- 근처 시설 ------------------------------------------------------------


def test_toilets_by_region(toilet_file):
    toilets = load_toilets(toilet_file)
    assert [t["name"] for t in toilets_in(toilets, "제주시", ["애월읍"])] == ["애월 화장실"]
    # 동 지역이면 읍면동이 안 적힌 같은 시 화장실도 후보
    assert [t["name"] for t in toilets_in(toilets, "제주시", ["일도이동"])] == ["동네 화장실"]
    assert toilets_in(toilets, "서귀포시", ["성산읍"]) == []
    aewol = toilets[0]
    assert aewol["accessible"]
    assert aewol["diaper"]
    assert not aewol["bell"]


def test_nearby_toilets_api(client):
    assert (
        client.get("/api/nearby/toilets", params={"city": "제주시", "region": "애월읍"}).status_code
        == 401
    )
    signup(client)
    r = client.get("/api/nearby/toilets", params={"city": "제주시", "region": "애월읍"})
    assert r.status_code == 200
    body = r.json()
    assert body[0]["name"] == "애월 화장실"
    assert "lat" not in body[0]  # 위치는 화면이 그때그때 찾는다
    assert (
        client.get("/api/nearby/toilets", params={"city": "서울", "region": "x"}).status_code == 422
    )


def test_nearby_wifi_sorted_and_deduplicated(client):
    signup(client)
    body = client.get("/api/nearby/wifi", params={"lat": 33.5, "lon": 126.5}).json()
    assert len(body) == 5
    assert [p["distance_m"] for p in body] == sorted(p["distance_m"] for p in body)
    assert [p["name"] for p in body].count("같은 공원") == 1
    for p in body:
        assert_user_text(p["name"])
    assert client.get("/api/nearby/wifi", params={"lat": 37.5, "lon": 127.0}).status_code == 422
    assert client.get("/api/nearby/police", params={"lat": 33.5, "lon": 126.5}).json() == []


def test_nearest_drops_same_place_duplicates():
    pts = [{"name": "공원", "lat": 33.5, "lon": 126.5}] * 3 + [
        {"name": "역", "lat": 33.51, "lon": 126.5}
    ]
    assert [p["name"] for p in nearest(pts, 33.5, 126.5)] == ["공원", "역"]
