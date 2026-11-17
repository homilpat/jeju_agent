"""FastAPI server: accounts, planners, and the built frontend.

Dev:  uvicorn jeju_lifepath.api.app:app --port 8001   (화면 개발 서버 8000이 /api를 넘겨줌)
Demo: npm run build (src/frontend) 후 uvicorn jeju_lifepath.api.app:app --port 8000

All messages returned to the browser are plain Korean sentences (no field names or codes).
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from jeju_lifepath.api.store import SESSION_DAYS, Store
from jeju_lifepath.presentation.route import render_plan
from jeju_lifepath.schemas import MobilityProfile, RoutePreference

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DB = ROOT / "data" / "private" / "app.sqlite3"
FRONTEND_DIST = ROOT / "src" / "frontend" / "dist"
PROCESSED_DIR = ROOT / "data" / "processed"
OSM_FILE = ROOT / "data" / "private" / "osm" / "jeju_highways.json"
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


def default_router():
    """실제 제주 OSM 보행망 + 사고다발 구역. 처음 경로 요청 때 한 번 불러온다(약 3초)."""
    from jeju_lifepath.geo.osm import WalkNetwork  # noqa: PLC0415 - 무거운 불러오기를 첫 요청까지 미룸
    from jeju_lifepath.routing.walk import AccidentZones, WalkRouter  # noqa: PLC0415

    if not OSM_FILE.exists():
        return None
    return WalkRouter(
        WalkNetwork.from_file(OSM_FILE),
        AccidentZones.from_file(PROCESSED_DIR / "accident-zones.geojson"),
    )


MSG_ROUTES_UNAVAILABLE = "지금은 경로를 계산할 수 없어요. 길 자료가 준비되지 않았어요."


class Planner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=60)
    start_date: date | None = None
    end_date: date | None = None
    places: list[PlannerPlace] = Field(default_factory=list, max_length=MAX_PLACES)
    # 장소 사이 구간마다 고른 경로 종류 (장소 n개면 n-1개)
    route_choices: list[RoutePreference] = Field(default_factory=list)

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


def is_https(request: Request) -> bool:
    # 휴대폰 시연용 https 터널은 X-Forwarded-Proto로 알려준다
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def create_app(  # noqa: PLR0915 - 엔드포인트를 한곳에 모아 둠
    db_path: Path = DEFAULT_DB,
    static_dir: Path = FRONTEND_DIST,
    processed_dir: Path = PROCESSED_DIR,
    router_factory: Callable[[], object | None] = default_router,
) -> FastAPI:
    store = Store(db_path)
    public_points = load_public_points(processed_dir)
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
        found = store.get_planner(current_user(token), planner_id)
        if found is None:
            raise HTTPException(404, MSG_NOT_FOUND)
        body, updated = found
        return {"id": planner_id, **body, "updated_at": updated}

    @app.put("/api/planners/{planner_id}")
    def update_planner(planner_id: int, planner: Planner, token: SessionCookie = None) -> dict:
        user_id = current_user(token)
        if store.save_planner(user_id, planner_body(planner, public_points), planner_id) is None:
            raise HTTPException(404, MSG_NOT_FOUND)
        return {"id": planner_id}

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
        )
        return render_plan(plan)

    if static_dir.is_dir():  # 빌드한 화면이 있으면 같은 주소에서 함께 제공
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()
