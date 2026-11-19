"""FastAPI server: accounts, planners, and the built frontend.

Dev:  uvicorn jeju_lifepath.api.app:app --port 8001   (화면 개발 서버 8000이 /api를 넘겨줌)
Demo: npm run build (src/frontend) 후 uvicorn jeju_lifepath.api.app:app --port 8000

All messages returned to the browser are plain Korean sentences (no field names or codes).
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import shapely.geometry
from fastapi import Cookie, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pyproj import Transformer
from starlette.exceptions import HTTPException as StarletteHTTPException

from jeju_lifepath.agent.kakao import KakaoLocal
from jeju_lifepath.agent.llm import DailyBudget, OpenAICompatibleLLM
from jeju_lifepath.agent.models import Policy, ToolError
from jeju_lifepath.agent.orchestrator import Agent, Conflict, NotFound, locate_trip
from jeju_lifepath.agent.services import AgentContext
from jeju_lifepath.api.store import SESSION_DAYS, TITLE_MAX, DraftOutdated, Store
from jeju_lifepath.geo.geodesy import haversine_m
from jeju_lifepath.presentation.route import render_plan
from jeju_lifepath.schemas import MobilityProfile, RoutePreference

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DB = ROOT / "data" / "private" / "app.sqlite3"
FRONTEND_DIST = ROOT / "src" / "frontend" / "dist"
PROCESSED_DIR = ROOT / "data" / "processed"
OSM_FILE = ROOT / "data" / "private" / "osm" / "jeju_highways.json"
PRIVATE_DIR = ROOT / "data" / "private"
ENV_FILE = ROOT / ".env"
TOILET_FILE = PRIVATE_DIR / "공중화장실정보_제주특별자치도.csv"
HUB_LINK_DIR = PRIVATE_DIR / "hub_link"  # scripts/fetch_hub_links.py
BARRIER_FREE_FILE = PRIVATE_DIR / "barrier_free.json"  # scripts/build_barrier_free.py
CCTV_FILES = [PRIVATE_DIR / "CCTV정보_제주특별자치도.csv", PRIVATE_DIR / "CCTV정보.csv"]
# 제주도 전체를 덮는 사각형(경도·위도). 경로 요청 좌표가 이 밖이면 거절한다
# (kakao.js 검색 범위와 같음)
JEJU_LON = (125.9, 127.0)
JEJU_LAT = (33.0, 34.05)
COOKIE = "jl_session"

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
PASSWORD_MIN = 8  # NIST SP 800-63B 5.1.1.2: 사용자가 정한 비밀번호는 최소 8자
PASSWORD_MAX = 64  # 같은 기준: 최소 64자까지는 허용해야 함
MAX_PLACES = 30  # shortcut: 근거 없는 상한(하루 일정에 충분하다고 봄), 부족하면 늘리기

# 모듈 최상단에 둬야 FastAPI가 (지연 평가된) 주석에서 찾을 수 있다
SessionCookie = Annotated[str | None, Cookie(alias=COOKIE)]

MSG_INVALID_INPUT = "입력한 내용을 다시 확인해 주세요."
MSG_LOGIN_NEEDED = "로그인이 필요해요. 다시 로그인해 주세요."
MSG_NOT_FOUND = "찾는 여행 계획이 없어요."
MSG_LOCATE_FAILED = "장소 위치를 다시 찾지 못했어요. 잠시 후 다시 열어 주세요."
MSG_DRAFT_OUTDATED = "그사이 원래 여행이 바뀌었어요. 새 여행으로 저장하거나 다시 고쳐 달라고 해 주세요."
MSG_GENERIC = "잠시 문제가 생겼어요. 다시 시도해 주세요."
# 프레임워크 기본 오류 문구(영어)는 보여주지 않고 상태별 한국어 문장으로 바꾼다
FALLBACK_BY_STATUS = {400: MSG_INVALID_INPUT, 401: MSG_LOGIN_NEEDED, 404: "찾는 페이지가 없어요."}
HANGUL = re.compile("[가-힣]")


class Credentials(BaseModel):
    username: str = ""
    password: str = ""


class PlannerPlace(BaseModel):
    """좌표는 받지 않는다: 카카오 장소는 좌표 저장 금지(운영정책),
    공공데이터 장소는 서버가 자기 자료에서 좌표를 찾아 붙인다(클라이언트 좌표를 믿지 않음)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["kakao", "public"]
    # kakao: 카카오 장소 ID / public: "<자료 이름>/<지점 번호>"
    ref: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=40)


def load_public_points(processed_dir: Path) -> dict[str, tuple[float, float]]:
    """ref("<dataset_id>/<feature id>") -> (lon, lat), 가공된 공공데이터 GeoJSON에서."""
    points: dict[str, tuple[float, float]] = {}
    for path in sorted(processed_dir.glob("*.geojson")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for f in data.get("features", []):
            if f["geometry"]["type"] != "Point":  # 사고 다발 구역 같은 영역 자료는 장소가 아님
                continue
            lon, lat = f["geometry"]["coordinates"]
            points[f"{data['dataset_id']}/{f['id']}"] = (lon, lat)
    return points


class LatLon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=JEJU_LAT[0], le=JEJU_LAT[1])
    lon: float = Field(ge=JEJU_LON[0], le=JEJU_LON[1])


class RouteQuery(BaseModel):
    """경로 계산용 임시 좌표(저장하지 않음). 카카오 좌표도 계산에만 쓰고 버린다."""

    model_config = ConfigDict(extra="forbid")

    origin: LatLon
    destination: LatLon
    mobility: MobilityProfile = MobilityProfile.STANDARD
    prefer_cctv: bool = False  # 사용자가 "CCTV 근처 길로 안내할까요?"에 네라고 했을 때만


def default_router():
    """실제 제주 OSM 보행망 + 사고다발 구역. 처음 경로 요청 때 한 번 불러온다(약 3초)."""
    from jeju_lifepath.geo.osm import WalkNetwork  # noqa: PLC0415 - 무거운 불러오기를 첫 요청까지 미룸
    from jeju_lifepath.routing.walk import AccidentZones, CctvPoints, WalkRouter  # noqa: PLC0415

    if not OSM_FILE.exists():
        return None
    net = WalkNetwork.from_file(OSM_FILE)
    verified = frozenset()
    if BARRIER_FREE_FILE.exists():  # 무장애 조사 코스가 지난 길은 휠체어 통행 확인된 길로 본다
        from jeju_lifepath.data.barrier_free import verified_segment_ids  # noqa: PLC0415

        places = json.loads(BARRIER_FREE_FILE.read_text(encoding="utf-8"))
        verified = verified_segment_ids(places, net.segments)
    return WalkRouter(
        net,
        AccidentZones.from_file(PROCESSED_DIR / "accident-zones.geojson"),
        CctvPoints.from_csvs(CCTV_FILES),
        verified,
    )


MSG_ROUTES_UNAVAILABLE = "지금은 경로를 계산할 수 없어요. 길 자료가 준비되지 않았어요."
MSG_OVERLAY_FAILED = "범죄주의구간 지도를 불러오지 못했어요. 잠시 후 다시 시도해 주세요."

# 생활안전지도 범죄주의구간(전체) 지도 그림.
# 카카오 지도와 같은 좌표계(EPSG:5181)로 받아 그대로 겹친다
SAFEMAP_CRIME_WMS = "https://www.safemap.go.kr/openapi2/IF_0087_WMS"
# shortcut: 큰 화면 한 장이면 충분하다고 봄(근거 없음), 4K 화면이면 늘리기
MAX_OVERLAY_PX = 2048


def read_env(name: str) -> str:
    """환경변수 먼저, 없으면 .env. 값은 절대 로그·응답에 넣지 않는다."""
    if os.environ.get(name):
        return os.environ[name]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name and not line.lstrip().startswith("#"):
                return v.strip().strip('"').strip("'")
    return ""


def fetch_crime_png(
    bbox_5181: tuple[float, float, float, float], width: int, height: int
) -> bytes | None:
    key = read_env("SAFEMAP_API_KEY")
    if not key:
        return None
    url = (
        SAFEMAP_CRIME_WMS
        + "?"
        + urllib.parse.urlencode(
            {
                "serviceKey": key,
                "request": "GetMap",
                "service": "WMS",
                "version": "1.1.1",
                "srs": "EPSG:5181",
                "bbox": ",".join(f"{v:.2f}" for v in bbox_5181),
                "width": width,
                "height": height,
                "format": "image/png",
                "transparent": "TRUE",
            }
        )
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
    except Exception:
        return None
    return body if body.startswith(b"\x89PNG") else None  # 오류 때는 HTML이 옴


def load_toilets(path: Path) -> list[dict]:
    """공중화장실 표준데이터(cp949). 위경도가 없어 이름·주소·편의시설만 쓴다."""
    if not path.exists():
        return []
    out = []
    for r in csv.DictReader(io.StringIO(path.read_bytes().decode("cp949"))):
        road, lot = r["소재지도로명주소"].strip(), r["소재지지번주소"].strip()
        if not (road or lot):
            continue
        out.append(
            {
                "name": r["화장실명"].strip(),
                "addresses": [a for a in (road, lot) if a],
                "hours": (r["개방시간상세"] or r["개방시간"]).strip(),
                "accessible": any(
                    (r.get(k) or "0").strip() not in ("", "0")
                    for k in ("남성용-장애인용대변기수", "여성용-장애인용대변기수")
                ),
                "diaper": r["기저귀교환대유무"].strip() == "Y",
                "bell": r["비상벨설치여부"].strip() == "Y",
            }
        )
    return out


def _csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("cp949"))))


def load_hub_links(folder: Path) -> tuple[dict[str, list[dict]], list[dict]]:
    """제주데이터허브 LINK 자료(scripts/fetch_hub_links.py가 받은 CSV). 위경도가 없어 주소만 쓴다.
    반환 ({시설 종류: 화장실과 같은 모양의 목록}, 올레 코스 목록). 파일이 없으면 빈 목록."""
    clean = lambda s: (s or "").strip().replace("?", "-")  # noqa: E731 - 원자료의 깨진 긴 줄표(–)
    facilities = {
        "night_pharmacy": [
            {"name": clean(r["약국명"]), "addresses": [clean(r["소재지"])],
             "notes": [f"밤 {clean(r['운영종료시간'])}까지", clean(r["운영요일"])]}
            for r in _csv_rows(folder / "night_pharmacy.csv")
        ],
        "nursing_room": [
            {"name": clean(r["설치장소(기관)"]), "addresses": [clean(r["주소"])],
             "notes": [clean(r["용도"])]}
            for r in _csv_rows(folder / "nursing_room.csv")
        ],
        "emergency": [
            {"name": clean(r["의료기관"]), "addresses": [clean(r["소재지"])],
             "notes": [clean(r["구분"]), f"전화 {clean(r['전화번호'])}"]}
            for r in _csv_rows(folder / "emergency.csv")
        ],
    }
    olle = []
    for r in _csv_rows(folder / "olle.csv"):
        start, _, end = clean(r["시종점정보"]).partition("-")
        olle.append({"course": clean(r["코스별"]), "name": clean(r["코스명"]), "length": clean(r["거리"]),
                     "time": clean(r["소요시간정보"]), "start": start, "end": end or None})
    return {k: [x for x in v if x["name"] and x["addresses"][0]] for k, v in facilities.items()}, olle


# 주소 속 읍·면·동 이름(예: "애월읍", "삼양1동"). 도로명 주소만 있는 동 지역 화장실에는 없다
REGION_WORD = re.compile(r"\S+[읍면동](?=\s|$)")


def toilets_in(toilets: list[dict], city: str, regions: list[str]) -> list[dict]:
    """같은 읍면동이 주소에 적힌 화장실 + (동 지역이면) 읍면동이 안 적힌 같은 시 화장실.
    shortcut: 주소 글자 대조라 표기가 다르면 빠지고, 경계 근처 옆 동 화장실은 못 찾음.
    좌표 자료가 생기면 거리로 바꾸기."""
    wanted = set(regions)
    in_dong = any(r.endswith("동") for r in regions)
    picked = []
    for t in toilets:
        if not any(city in a for a in t["addresses"]):
            continue
        words = {w for a in t["addresses"] for w in REGION_WORD.findall(a)}
        if words & wanted or (in_dong and not words):
            picked.append(t)
    return picked


NEARBY_DATASETS = {"wifi": "hub-wifi.geojson", "police": "safemap-police.geojson"}
BRACKET_NO = re.compile(r"\s*\[\d+\]")
NEARBY_LIMIT = 5  # shortcut: 근거 없는 개수(한 화면에 보기 좋은 정도), 시연 후 조정


def load_nearby(processed_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for kind, name in NEARBY_DATASETS.items():
        path = processed_dir / name
        feats = json.loads(path.read_text(encoding="utf-8"))["features"] if path.exists() else []
        out[kind] = [
            {
                # 원자료 이름의 밑줄("전기차충전소_제주시청")은 화면에서 띄어쓰기로, 정류소 번호
                # ("[405000284]")처럼 대괄호 속 숫자는 내부 번호라 화면에 보이지 않게 뺀다
                "name": BRACKET_NO.sub(
                    "", (f["properties"].get("place") or f["properties"].get("name") or "").replace("_", " ")
                ).strip(),
                "detail": f["properties"].get("category") or f["properties"].get("kind") or "",
                "lon": f["geometry"]["coordinates"][0],
                "lat": f["geometry"]["coordinates"][1],
            }
            for f in feats
        ]
    return out


def load_areas(processed_dir: Path) -> tuple[dict, dict]:
    """읍면동 경계 ({이름: shapely 도형}, {이름: 시}). scripts/fetch_admdong.py가 만든 파일, 없으면 빈 사전."""
    path = processed_dir / "jeju-admdong.geojson"
    if not path.exists():
        return {}, {}
    feats = json.loads(path.read_text(encoding="utf-8"))["features"]
    shapes = {f["properties"]["name"]: shapely.geometry.shape(f["geometry"]) for f in feats}
    # full_name 예: "제주특별자치도 제주시 한림읍"
    cities = {f["properties"]["name"]: f["properties"]["full_name"].split()[1] for f in feats}
    return shapes, cities


def nearest(points: list[dict], lat: float, lon: float, limit: int = NEARBY_LIMIT) -> list[dict]:
    ranked = sorted(
        ({**p, "distance_m": round(haversine_m(lon, lat, p["lon"], p["lat"]))} for p in points),
        key=lambda p: p["distance_m"],
    )
    out, seen = [], set()
    for p in ranked:  # 와이파이는 한 장소에 기기가 여러 대라 같은 이름은 가장 가까운 것 하나만
        if p["name"] not in seen:
            seen.add(p["name"])
            out.append(p)
        if len(out) == limit:
            break
    return out


class AgentStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=40)
    theme: str = Field(min_length=1, max_length=40)
    fixed: bool = False
    completed: bool = False
    # 무장애 여행지의 접근성 요약(공공 무장애 조사 자료, 카카오 자료 아님)
    access: str | None = Field(default=None, max_length=120)


class AgentTripMeta(BaseModel):
    """Agent가 만든 일정의 조건(사용자가 말한 값과 장소 ID만. 카카오 이름·좌표는 없음)."""

    model_config = ConfigDict(extra="forbid")

    anchor_text: str | None = Field(default=None, max_length=40)
    anchor_ref: str | None = Field(default=None, max_length=40)  # 기준 지명의 카카오 장소 번호
    start_point: LatLon | None = None  # 지금 위치에서 시작한 일정의 출발점(약 10m 단위)
    destination_text: str | None = Field(default=None, max_length=40)
    round_trip: bool = True
    return_by: str | None = Field(default=None, max_length=40)
    stay_minutes: int | None = Field(default=None, ge=0, le=600)
    themes: list[str] = Field(default_factory=list, max_length=10)
    stops: list[AgentStop] = Field(default_factory=list, max_length=30)
    excluded_refs: list[str] = Field(default_factory=list, max_length=100)


class Planner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=60)
    start_date: date | None = None
    end_date: date | None = None
    places: list[PlannerPlace] = Field(default_factory=list, max_length=MAX_PLACES)
    # 장소 사이 구간마다 고른 경로 종류 (장소 n개면 n-1개)
    route_choices: list[RoutePreference] = Field(default_factory=list)
    # "공개된 방범 CCTV가 근처에 있는 길로 안내할까요?" 답. None = 아직 안 물어봄
    prefer_cctv: bool | None = None
    # 이동 조건(유모차·휠체어). 화면·Agent·경로 계산에 같은 값을 넘긴다(AG-206)
    mobility: MobilityProfile | None = None
    agent: AgentTripMeta | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Planner:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end before start")
        if len(self.route_choices) != max(0, len(self.places) - 1):
            raise ValueError("one route choice per segment")
        return self


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse({"message": MSG_INVALID_INPUT}, status_code=422)

    @app.exception_handler(StarletteHTTPException)  # FastAPI HTTPException도 이걸 상속
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        ours = isinstance(exc.detail, str) and HANGUL.search(exc.detail)
        message = exc.detail if ours else FALLBACK_BY_STATUS.get(exc.status_code, MSG_GENERIC)
        return JSONResponse({"message": message}, status_code=exc.status_code)


MSG_UNKNOWN_PLACE = "공공데이터에서 찾을 수 없는 장소가 있어요. 다시 골라 주세요."


def planner_body(planner: Planner, public_points: dict[str, tuple[float, float]]) -> dict:
    body = planner.model_dump(mode="json")
    for place in body["places"]:
        if place["source"] == "public":
            if place["ref"] not in public_points:
                raise HTTPException(400, MSG_UNKNOWN_PLACE)
            place["lon"], place["lat"] = public_points[place["ref"]]
    return body


ID_PATTERN = r"^[A-Za-z0-9-]{8,64}$"


class MapView(BaseModel):
    """화면 지도에 지금 보이는 범위. AI가 같은 지도를 보고 답하게 하는 맥락(저장 안 함)."""

    model_config = ConfigDict(extra="forbid")

    center: LatLon
    south: float = Field(ge=-90, le=90)
    west: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def _ordered(self) -> MapView:
        if not (self.south < self.north and self.west < self.east):
            raise ValueError("bounds")
        return self


class ScreenContext(BaseModel):
    """사용자가 지금 보고 있는 화면. AI가 같은 화면을 보고 답하게 한다(저장 안 함)."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["planners", "editor", "travel", "chat"]
    travel_stop: int | None = Field(default=None, ge=0, le=MAX_PLACES)


class ConversationTitle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)  # 서버가 앞 40자로 자름


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 화면이 만든 메시지 ID(UUID). 같은 ID로 다시 보내면 다시 실행하지 않는다(AG-405)
    message_id: str = Field(pattern=ID_PATTERN)
    conversation_id: str | None = Field(default=None, pattern=ID_PATTERN)
    text: str = Field(min_length=1, max_length=500)
    location: LatLon | None = None  # 사용자가 위치 사용을 허락했을 때만. 저장하지 않음
    trip_id: int | None = None
    # trip_id가 없을 때 이 대화에 연결해 둔 여행도 쓰지 않는다(화면에서 "그만 보기")
    detach_trip: bool = False
    map_view: MapView | None = None
    screen: ScreenContext | None = None


class AgentResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: LatLon | None = None
    map_view: MapView | None = None


MSG_AGENT_CONFLICT = "같은 메시지 번호로 다른 내용이 왔어요. 새로 보내 주세요."
MSG_AGENT_NOT_FOUND = "찾는 대화가 없어요."


def default_llm(policy: Policy):
    """LLM_API_KEY가 있으면 실 LLM, 없으면 None(규칙 해석 제한 모드). AGENT_LLM=off로 끌 수 있다."""
    key = read_env("LLM_API_KEY")
    if not key or read_env("AGENT_LLM") == "off":
        return None
    cfg = policy.llm
    limit = read_env("LLM_DAILY_BUDGET_USD")  # 제공자 쪽 한도를 걸 수 없을 때 하루 요금 상한(달러)
    budget = (
        DailyBudget(PRIVATE_DIR / "llm_usage.json", float(limit), cfg["price_in_per_m"], cfg["price_out_per_m"])
        if limit else None
    )
    return OpenAICompatibleLLM(
        read_env("LLM_BASE_URL") or cfg["base_url"],
        read_env("LLM_MODEL") or cfg["model"],
        key,
        cfg["timeout_s"],
        cfg.get("reasoning_effort"),
        budget=budget,
    )


def is_https(request: Request) -> bool:
    # 휴대폰 시연용 https 터널은 X-Forwarded-Proto로 알려준다
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def create_app(  # noqa: PLR0915 - 엔드포인트를 한곳에 모아 둠
    db_path: Path = DEFAULT_DB,
    static_dir: Path = FRONTEND_DIST,
    processed_dir: Path = PROCESSED_DIR,
    router_factory: Callable[[], object | None] = default_router,
    *,
    crime_fetch: Callable[..., bytes | None] = fetch_crime_png,
    toilet_file: Path = TOILET_FILE,
    hub_link_dir: Path = HUB_LINK_DIR,
    barrier_free_file: Path = BARRIER_FREE_FILE,
    kakao: object | None = None,
    llm: object | None = "env",
    policy: Policy | None = None,
    agent_hooks: dict | None = None,
) -> FastAPI:
    store = Store(db_path)
    policy = policy or Policy.load()
    store.purge_older_than(policy.trace_retention_days)
    public_points = load_public_points(processed_dir)
    toilets = load_toilets(toilet_file)
    addr_facilities, olle = load_hub_links(hub_link_dir)
    barrier_free = (
        json.loads(barrier_free_file.read_text(encoding="utf-8")) if barrier_free_file.exists() else []
    )
    nearby = load_nearby(processed_dir)
    areas, area_city = load_areas(processed_dir)
    app = FastAPI(title="JEJU LifePath", docs_url=None, redoc_url=None, openapi_url=None)

    _install_error_handlers(app)

    def current_user(token: str | None) -> int:
        user_id = store.session_user(token) if token else None
        if user_id is None:
            raise HTTPException(401, MSG_LOGIN_NEEDED)
        return user_id

    def start_session(request: Request, response: Response, user_id: int) -> None:
        response.set_cookie(
            COOKIE,
            store.create_session(user_id),
            max_age=SESSION_DAYS * 86400,
            httponly=True,
            samesite="lax",
            secure=is_https(request),
        )

    # accounts -------------------------------------------------------------
    @app.post("/api/auth/signup", status_code=201)
    def signup(body: Credentials, request: Request, response: Response) -> dict:
        if not USERNAME_RE.fullmatch(body.username):
            raise HTTPException(400, "아이디는 영문, 숫자, 밑줄로 3~20자로 만들어 주세요.")
        if not PASSWORD_MIN <= len(body.password) <= PASSWORD_MAX:
            raise HTTPException(400, f"비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자로 만들어 주세요.")
        user_id = store.create_user(body.username, body.password)
        if user_id is None:
            raise HTTPException(409, "이미 쓰고 있는 아이디예요. 다른 아이디를 골라 주세요.")
        start_session(request, response, user_id)
        return {"username": body.username}

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request, response: Response) -> dict:
        user_id = store.verify_user(body.username, body.password)
        if user_id is None:
            raise HTTPException(401, "아이디 또는 비밀번호가 맞지 않아요.")
        start_session(request, response, user_id)
        return {"username": body.username}

    @app.post("/api/auth/logout", status_code=204)
    def logout(response: Response, token: SessionCookie = None) -> None:
        if token:
            store.delete_session(token)
        response.delete_cookie(COOKIE)

    @app.get("/api/auth/me")
    def me(token: SessionCookie = None) -> dict:
        return {"username": store.username(current_user(token))}

    @app.delete("/api/account", status_code=204)
    def delete_account(response: Response, token: SessionCookie = None) -> None:
        store.delete_user(current_user(token))
        response.delete_cookie(COOKIE)

    # planners -------------------------------------------------------------
    @app.get("/api/planners")
    def list_planners(token: SessionCookie = None) -> list[dict]:
        return [
            {
                "id": pid,
                "title": body["title"],
                "start_date": body.get("start_date"),
                "end_date": body.get("end_date"),
                "place_count": len(body.get("places", [])),
                "updated_at": updated,
            }
            for pid, body, updated in store.list_planners(current_user(token))
        ]

    @app.post("/api/planners", status_code=201)
    def create_planner(planner: Planner, token: SessionCookie = None) -> dict:
        user_id = current_user(token)
        return {"id": store.save_planner(user_id, planner_body(planner, public_points))}

    @app.get("/api/planners/{planner_id}")
    def get_planner(planner_id: int, token: SessionCookie = None) -> dict:
        found = store.get_trip(current_user(token), planner_id)
        if found is None:
            raise HTTPException(404, MSG_NOT_FOUND)
        body, version, updated = found
        # version: AI가 고쳤는지 화면이 알아보는 번호(저장할 때 보내지 않음)
        return {"id": planner_id, **body, "updated_at": updated, "version": version}

    @app.put("/api/planners/{planner_id}")
    def update_planner(planner_id: int, planner: Planner, token: SessionCookie = None) -> dict:
        user_id = current_user(token)
        if store.save_planner(user_id, planner_body(planner, public_points), planner_id) is None:
            raise HTTPException(404, MSG_NOT_FOUND)
        return {"id": planner_id}

    @app.get("/api/planners/{planner_id}/located")
    def located_places(planner_id: int, token: SessionCookie = None) -> dict:
        """AI가 만든 장소를 만들 때와 같은 기준으로 다시 찾은 위치(응답에만, 저장 안 함)."""
        found = store.get_trip(current_user(token), planner_id)
        if found is None:
            raise HTTPException(404, MSG_NOT_FOUND)
        try:
            places = locate_trip(agent.ctx.kakao, found[0])
        except ToolError:
            raise HTTPException(503, MSG_LOCATE_FAILED) from None
        return {
            "places": {
                ref: {k: p[k] for k in ("name", "category", "address", "lat", "lon", "url")}
                for ref, p in places.items()
            }
        }

    @app.post("/api/planners/{planner_id}/save")
    def save_draft(planner_id: int, overwrite: bool = False, token: SessionCookie = None) -> dict:
        """AI가 만든 초안을 내 여행에 넣는다. overwrite: 고친 원래 여행에 덮어쓰기."""
        try:
            saved = store.save_draft(current_user(token), planner_id, overwrite)
        except DraftOutdated:
            raise HTTPException(409, MSG_DRAFT_OUTDATED) from None
        if saved is None:
            raise HTTPException(404, MSG_NOT_FOUND)
        return {"id": saved}

    @app.delete("/api/planners/{planner_id}", status_code=204)
    def delete_planner(planner_id: int, token: SessionCookie = None) -> None:
        if not store.delete_planner(current_user(token), planner_id):
            raise HTTPException(404, MSG_NOT_FOUND)

    # routes ---------------------------------------------------------------
    router_box: dict = {}
    router_lock = threading.Lock()

    def get_router():
        with router_lock:
            if "router" not in router_box:
                router_box["router"] = router_factory()
            return router_box["router"]

    @app.post("/api/routes/compare")
    def compare(query: RouteQuery, token: SessionCookie = None) -> dict:
        current_user(token)
        router = get_router()
        if router is None:
            raise HTTPException(503, MSG_ROUTES_UNAVAILABLE)
        plan = router.plan(
            (query.origin.lon, query.origin.lat),
            (query.destination.lon, query.destination.lat),
            query.mobility,
            prefer_cctv=query.prefer_cctv,
        )
        return render_plan(plan)

    # map overlay ---------------------------------------------------------
    to_5181 = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)

    @app.get("/api/overlays/crime")
    def crime_overlay(  # noqa: PLR0917 - 지도 화면 사각형과 픽셀 크기
        west: float,
        south: float,
        east: float,
        north: float,
        width: Annotated[int, Query(ge=1, le=MAX_OVERLAY_PX)],
        height: Annotated[int, Query(ge=1, le=MAX_OVERLAY_PX)],
        token: SessionCookie = None,
    ) -> Response:
        """사용자가 "범죄주의구간 표시"에 네라고 했을 때만 화면이 부른다.
        키를 숨기려고 서버가 대신 받는다."""
        current_user(token)
        jeju = (
            west < JEJU_LON[1]
            and east > JEJU_LON[0]
            and south < JEJU_LAT[1]
            and north > JEJU_LAT[0]
        )
        if not (west < east and south < north and jeju):
            raise HTTPException(400, MSG_INVALID_INPUT)
        x1, y1 = to_5181.transform(west, south)
        x2, y2 = to_5181.transform(east, north)
        png = crime_fetch((x1, y1, x2, y2), width, height)
        if png is None:
            raise HTTPException(502, MSG_OVERLAY_FAILED)
        return Response(
            png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
        )

    # nearby facilities ----------------------------------------------------
    @app.get("/api/nearby/toilets")
    def nearby_toilets(
        city: Annotated[str, Query(pattern="^(제주시|서귀포시)$")],
        region: Annotated[list[str], Query(min_length=1, max_length=4)],
        token: SessionCookie = None,
    ) -> list[dict]:
        """좌표 없는 자료라 후보(이름·주소)만 준다.
        화면이 주소로 그때그때 위치를 찾고 저장하지 않는다."""
        current_user(token)
        return toilets_in(toilets, city, region)

    @app.get("/api/nearby/{kind}")
    def nearby_points(
        kind: Literal["wifi", "police"],
        lat: Annotated[float, Query(ge=JEJU_LAT[0], le=JEJU_LAT[1])],
        lon: Annotated[float, Query(ge=JEJU_LON[0], le=JEJU_LON[1])],
        token: SessionCookie = None,
    ) -> list[dict]:
        current_user(token)
        return nearest(nearby[kind], lat, lon)

    # agent ---------------------------------------------------------------
    agent = Agent(
        store,
        AgentContext(
            kakao=kakao or KakaoLocal(read_env("KAKAO_REST_API_KEY")),
            get_router=get_router,
            policy=policy,
            nearby=nearby,
            areas=areas,
            area_city=area_city,
            toilets=toilets,
            toilets_in=toilets_in,
            addr_facilities=addr_facilities,
            olle=olle,
            barrier_free=barrier_free,
            nearest=nearest,
            trip_validator=lambda body: planner_body(Planner.model_validate(body), public_points),
        ),
        llm=default_llm(policy) if llm == "env" else llm,
        **(agent_hooks or {}),
    )
    app.state.agent = agent

    def agent_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Conflict:
            raise HTTPException(409, MSG_AGENT_CONFLICT) from None
        except NotFound:
            raise HTTPException(404, MSG_AGENT_NOT_FOUND) from None

    @app.post("/api/agent/messages")
    def agent_message(body: AgentMessage, token: SessionCookie = None) -> dict:
        user_id = current_user(token)  # 사용자 ID는 세션에서만 정한다(AG-202)
        return agent_call(
            agent.handle_message,
            user_id,
            body.message_id,
            body.text,
            conversation_id=body.conversation_id,
            location=body.location.model_dump() if body.location else None,
            trip_id=body.trip_id,
            detach_trip=body.detach_trip,
            map_view=body.map_view.model_dump() if body.map_view else None,
            screen=body.screen.model_dump() if body.screen else None,
        )

    @app.post("/api/agent/runs/{run_id}/resume")
    def agent_resume(run_id: str, body: AgentResume, token: SessionCookie = None) -> dict:
        user_id = current_user(token)
        loc = body.location.model_dump() if body.location else None
        view = body.map_view.model_dump() if body.map_view else None
        return agent_call(agent.resume, user_id, run_id, loc, view)

    @app.post("/api/agent/runs/{run_id}/cancel", status_code=202)
    def agent_cancel(run_id: str, token: SessionCookie = None) -> dict:
        if not agent.cancel(current_user(token), run_id):
            raise HTTPException(404, MSG_AGENT_NOT_FOUND)
        return {"message": "취소를 요청했어요."}

    @app.get("/api/agent/runs/{run_id}")
    def agent_run(run_id: str, detail: bool = False, token: SessionCookie = None) -> dict:
        return agent_call(agent.run_status, current_user(token), run_id, detail)

    @app.get("/api/agent/conversations")
    def agent_conversations(token: SessionCookie = None) -> dict:
        """지난 대화 목록(보유기간 안의 것만, 최근 것부터)."""
        return {"conversations": store.list_conversations(current_user(token))}

    @app.patch("/api/agent/conversations/{conversation_id}")
    def agent_conversation_title(
        conversation_id: str, body: ConversationTitle, token: SessionCookie = None
    ) -> dict:
        """대화 제목 바꾸기. title이 비었거나 null이면 붙인 제목을 지우고 기본 제목(첫 질문)으로."""
        title = (body.title or "").strip()[:TITLE_MAX] or None
        if not store.set_conversation_title(current_user(token), conversation_id, title):
            raise HTTPException(404, MSG_AGENT_NOT_FOUND)
        return {"title": title, "custom": bool(title)}

    @app.delete("/api/agent/conversations/{conversation_id}", status_code=204)
    def agent_conversation_delete(conversation_id: str, token: SessionCookie = None) -> None:
        if not store.delete_conversation(current_user(token), conversation_id):
            raise HTTPException(404, MSG_AGENT_NOT_FOUND)

    @app.get("/api/agent/conversations/{conversation_id}")
    def agent_conversation(conversation_id: str, token: SessionCookie = None) -> dict:
        user_id = current_user(token)
        found = store.get_conversation(user_id, conversation_id)
        if found is None:
            raise HTTPException(404, MSG_AGENT_NOT_FOUND)
        state = found[0]
        trip = store.get_trip(user_id, state["trip_id"]) if state.get("trip_id") else None
        return {
            "conversation_id": conversation_id,
            "history": state.get("history", []),
            "trip_id": state["trip_id"] if trip else None,
            "trip_version": trip[1] if trip else None,
        }

    if static_dir.is_dir():  # 빌드한 화면이 있으면 같은 주소에서 함께 제공
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()
