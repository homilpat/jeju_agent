"""제주 무장애여행정보(제주데이터허브·공공데이터포털)를 장소별 한 덩어리로 묶는다.

입력(모두 data/private, 커밋 안 함):
- barrier_free_hub/858_무장애여행정보.csv: 무장애 여행지 통합 목록(장소명·주소·위도·경도)
- barrier_free_hub/*_무장애여행정보_<장소>.csv, barrier_free/*.csv: 장소 안 지점별 위경도·접근성(계단·경사·화장실)
- barrier_free_hub/*_무장애여행위치정보.csv: 2021년 마을길 코스 지점(순서·위경도·구분·설명)
- barrier_free_hub/*_무장애여행주변시설.csv: 근처 식당·카페 등과 휠체어 진입·장애인 화장실 여부
- barrier_free_hub_files/*_무장애여행일반정보.hwp: 개요·주소·장애인 주차장·장애인 화장실·추천 대상·
  길 설명(경사 각도·단차)·추천 코스(2021년 틀). *_무장애여행정보_<장소>.hwp: 이동약자 안내·관람시간·
  관람료·장애인 주차구역·승강기(2020년 틀)
- barrier_free_zip/<허브 zip 이름>/NNN-1.csv: 무장애 코스를 걸으며 기록한 GPS 경로(위도·경도·고도),
  NNN-2.csv: 그 코스의 지점 이름·설명(허브 zip에서 CSV만 꺼낸 것, 사진은 쓰지 않음)
출력: 장소 목록(scripts/build_barrier_free.py가 data/private/barrier_free.json으로 저장).
"""

from __future__ import annotations

import csv
import io
import math
import re
from pathlib import Path

# 한글 문서 "추천 대상" 표의 유형(문서 순서). "●"가 붙으면 추천
AUDIENCE = {
    "휠체어 이용인(수동)": "휠체어(수동)",
    "휠체어 이용인(전동)": "휠체어(전동)",
    "보행 가능한 지체장애인": "보행 가능한 지체장애인",
    "시각장애인": "시각장애인",
    "청각장애인": "청각장애인",
    "지적장애인": "지적장애인",
    "시니어": "어르신",
    "영유아": "영유아(유모차)",
}
# 길 설명 문장 중 접근성에 관한 것(경사·단차·계단·노면)
ACCESS_WORDS = re.compile(r"각도|단차|계단|경사|오르막|내리막|턱|노면|휠체어|횡각")
# 위험 지점 낱말. "경사로"(휠체어용 시설)와 "턱 없음·계단 없음"처럼 없다는 말은 위험이 아니다
BARRIER_WORDS = re.compile(r"(계단|(?<!전)경사(?!로|진)|길 상태 안 좋|턱)(?!\s*(이\s*)?없)")


def place_ref(place: dict) -> str:
    """일정·플래너에 공공자료 장소로 넣을 때의 번호("barrier_free/<여행지 이름>"). 서버가 이 번호로 조사 좌표를 붙인다."""
    return f"barrier_free/{place['name']}"[:64]


def name_key(name: str) -> str:
    return re.sub(r"[\s()·~\-_,.'‘’]", "", name)


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():  # 받지 않은 자료는 빈 목록(다른 출처로 계속 묶는다)
        return []
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return list(csv.DictReader(io.StringIO(raw.decode(enc))))
        except UnicodeDecodeError:
            continue
    return []


def _yes_no(value: str | None) -> str | None:
    """표 칸 값 -> "있음"/"없음". 그 밖(다른 칸을 읽은 경우)은 모름(None)."""
    v = (value or "").replace(" ", "")
    if v in ("있음", "유", "O", "○", "●") or v.startswith("있음"):
        return "있음"
    if v in ("없음", "무", "X", "×", "-없음") or v.startswith("없음"):
        return "없음"
    return None


def _after(lines: list[str], label: str, skip: int = 0) -> str | None:
    """label 줄 다음(표 머리 skip줄 건너뜀)의 값."""
    for i, line in enumerate(lines):
        if line == label and i + 1 + skip < len(lines):
            value = lines[i + 1 + skip]
            return None if value in ("-", "") else value
    return None


TRACK_MAX = 300  # 지도에 그릴 GPS 경로 점 수 상한(고르게 솎음). shortcut: 화면 무게만 본 잠정값


def _label(lines: list[str], *labels: str) -> str | None:
    """띄어쓰기를 무시하고 label 줄 다음 값."""
    want = {x.replace(" ", "") for x in labels}
    for i, line in enumerate(lines):
        if line.replace(" ", "") in want and i + 1 < len(lines):
            return lines[i + 1]
    return None


NO_WORDS = re.compile(r"없|미설치|불가|X\b")


def _has_toilet(lines: list[str]) -> str | None:
    """2020년 틀: 장애인 화장실 줄이 있으면 그 줄에 없다는 말이 없을 때만 "있음", 있으면 "없음"."""
    hits = [x for x in lines if "장애인화장실" in x.replace(" ", "")]
    if not hits:
        return None
    return "없음" if all(NO_WORDS.search(x) for x in hits) else "있음"


def parse_doc_2020(text: str) -> dict:
    """2020년 틀 한글 문서(무장애여행정보_<장소>): 이동약자 안내·관람시간·관람료·주차·승강기."""
    lines = [x.strip() for x in text.splitlines() if x.strip() and not x.strip().startswith("그림")]
    note = next((x.split(":", 1)[1].strip() for x in lines if x.startswith("이동약자를 위한 안내")), "")
    summary = next((x for x in lines[1:] if len(x) >= 60), "")  # noqa: PLR2004 - 첫 긴 문단이 소개 글
    parking = next((x for x in lines if "장애인 주차" in x), None)
    elevator = _label(lines, "승강기")
    return {
        "summary": summary,
        "address": _label(lines, "주소", "주 소"),
        "disabled_parking": "있음" if parking and re.search(r"[1-9]", parking) else None,
        "disabled_toilet": _has_toilet(lines),
        "audience": [],
        "companion": [],
        "closed": None,
        "fee": _label(lines, "관람료", "관 람 료"),
        "hours": _label(lines, "관람시간"),
        "aid_rental": None,
        "course": None,
        "notes": [n for n in [note, f"승강기 {elevator}" if elevator else "", parking or ""] if n][:4],
    }


def parse_doc(text: str) -> dict:
    """무장애여행일반정보 한글 문서 글자 -> 항목. 표가 글자로 풀려 있어 줄 순서로 읽는다.
    2021년 틀("개요" 칸)이 아니면 2020년 틀(parse_doc_2020)로 읽는다.
    shortcut: 한 가지 문서 틀(2021년 미래성장과)에 맞춘 줄 순서 규칙이다. 틀이 다르면 빈 값이 나온다."""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if "개요" not in lines:
        return parse_doc_2020(text)
    summary_at = lines.index("개요")
    summary = lines[summary_at + 1] if 0 <= summary_at < len(lines) - 1 else ""
    audience, companion = [], []
    for i, line in enumerate(lines):
        if line in AUDIENCE and i + 1 < len(lines) and lines[i + 1] == "●":
            audience.append(AUDIENCE[line])
            if i + 2 < len(lines) and "동반" in lines[i + 2]:
                companion.append(AUDIENCE[line])
    # "장애인 주차장 유무 / 장애인 주차 대수 / 위치" 머리 세 줄 뒤가 값. 표 칸 순서가 다른 문서는
    # 엉뚱한 칸("구분", "5.3km")을 읽으므로 있음·없음으로 읽히는 값만 받는다
    parking = _yes_no(_after(lines, "장애인 주차장 유무", skip=2))
    toilet = _yes_no(_after(lines, "장애인 화장실", skip=2))
    notes = []
    for line in lines:
        # 짧은 표 칸과 코스 순서 줄("1. 주차장 → 2. …")은 빼고 설명 문장만
        if len(line) >= 40 and ACCESS_WORDS.search(line) and "→" not in line:  # noqa: PLR2004
            notes += [s.strip() for s in re.split(r"(?<=[다요])\.\s*", line) if ACCESS_WORDS.search(s)]
    return {
        "summary": summary,
        "address": _after(lines, "주소"),
        "disabled_parking": parking,
        "disabled_toilet": toilet,
        "audience": audience,
        "companion": companion,
        "closed": _after(lines, "휴무일"),
        "fee": _after(lines, "관람료"),
        "aid_rental": _after(lines, "보조기기 대여"),
        "course": _after(lines, "추천코스"),
        "notes": list(dict.fromkeys(n.rstrip(".") for n in notes))[:6],
    }


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spots(rows: list[dict]) -> list[dict]:
    """코스 지점 설명(NNN-2.csv): 같은 이름이 이어지면 설명이 있는 첫 줄만."""
    out: list[dict] = []
    for x in _points([{**r, "장소명칭": r.get("장소명"), "무장애관광정보": r.get("장소설명")} for r in rows]):
        if out and out[-1]["label"] == x["label"]:
            if not out[-1]["info"] and x["info"]:
                out[-1] = x
            continue
        out.append(x)
    return out


def _points(rows: list[dict]) -> list[dict]:
    """장소 안 지점(두 가지 열 이름 틀): 위경도, 이름, 접근성 설명."""
    out = []
    for r in rows:
        lat = _float(r.get("위도") or r.get("lat"))
        lon = _float(r.get("경도") or r.get("lon"))
        if lat is None or lon is None or not (33.0 < lat < 34.1 and 125.9 < lon < 127.0):  # noqa: PLR2004
            continue
        label = (r.get("장소명칭") or r.get("title") or r.get("Title") or r.get("구분") or "").strip()
        info = (r.get("무장애관광정보") or r.get("info") or r.get("설명") or r.get("Description") or "").strip()
        out.append({"lat": lat, "lon": lon, "label": label, "info": info})
    return out


def _track(rows: list[dict]) -> tuple[list[list[float]], float | None]:
    """GPS 경로 -> (지도용 [위도, 경도] 목록(최대 TRACK_MAX), 고도 차이 m). 고도가 없으면 None."""
    pts, alts = [], []
    for r in rows:
        lat, lon = _float(r.get("위도")), _float(r.get("경도"))
        if lat is None or lon is None:
            continue
        pts.append([round(lat, 6), round(lon, 6)])
        alt = _float(r.get("고도"))
        if alt is not None:
            alts.append(alt)
    step = max(1, -(-len(pts) // TRACK_MAX))
    thin = pts[::step] + ([pts[-1]] if pts and (len(pts) - 1) % step else [])
    return thin, (round(max(alts) - min(alts)) if len(alts) > 1 else None)


def build(hub: Path, portal: Path, hwp_dir: Path, hwp_text, zip_dir: Path | None = None) -> list[dict]:
    """출처들을 장소 이름으로 묶는다. hwp_text: 한글 문서 -> 글자(jeju_lifepath.data.hwp.hwp_text)."""
    places: dict[str, dict] = {}

    def place(name: str) -> dict:
        key = name_key(name)
        return places.setdefault(key, {"name": name.strip(), "lat": None, "lon": None, "address": None,
                                        "points": [], "nearby": [], "doc": None, "track": [],
                                        "climb_m": None})

    for r in read_csv(next(iter(sorted(hub.glob("858_*.csv"))), Path("없음"))) if hub.exists() else []:
        p = place(r["장소명"])
        p.update(lat=_float(r["위도"]), lon=_float(r["경도"]), address=r["주소"].strip())
    point_files = [*hub.glob("*_무장애여행정보_*.csv"), *(portal.glob("*.csv") if portal.exists() else [])]
    for f in point_files:
        if f.name.startswith("858_"):
            continue
        name = re.sub(r"^(\d+_)?제주특별자치도_무장애여행정보_(\d+[.\-])?|_\d{8}$", "", f.stem)
        p = place(name)
        if not p["points"]:  # 허브·포털에 같은 장소가 있으면 먼저 읽은 것만
            p["points"] = _points(read_csv(f))
    for f in hub.glob("*_무장애여행위치정보.csv"):
        p = place(re.sub(r"^\d+_제주특별자치도_|_무장애여행위치정보$", "", f.stem))
        p["points"] = _points(read_csv(f))
    for f in hub.glob("*_무장애여행주변시설.csv"):
        p = place(re.sub(r"^\d+_제주특별자치도_|_무장애여행주변시설$", "", f.stem))
        p["nearby"] = [
            {"kind": r.get("구분", "").strip(), "name": r.get("명칭", "").strip(),
             "wheelchair": (r.get("휠체어 진입") or "").strip(), "toilet": (r.get("장애인 화장실") or "").strip()}
            for r in read_csv(f) if r.get("명칭")
        ]
    if zip_dir is not None and zip_dir.exists():
        for folder in sorted(x for x in zip_dir.iterdir() if x.is_dir()):
            p = place(re.sub(r"^\d+_제주특별자치도_무장애여행정보_", "", folder.name))
            for f in sorted(folder.glob("*-1.csv")):
                p["track"], p["climb_m"] = _track(read_csv(f))
            for f in sorted(folder.glob("*-2.csv")):
                spots = [{**x, "label": x["label"], "info": x["info"]} for x in _spots(read_csv(f))]
                if spots:
                    p["points"] = spots  # 코스 지점 설명이 있으면 그것을 지도 지점으로
    if hwp_dir.exists():
        for f in hwp_dir.glob("*.hwp"):
            stem = re.sub(r"^\d+_제주특별자치도_", "", f.stem)
            if not (stem.endswith("_무장애여행일반정보") or stem.startswith("무장애여행정보_")):
                continue
            p = place(re.sub(r"_무장애여행일반정보$|^무장애여행정보_", "", stem))
            try:
                p["doc"] = parse_doc(hwp_text(f))
            except Exception:  # noqa: BLE001 - 깨진 문서 하나 때문에 전체를 멈추지 않는다
                p["doc"] = None
            if p["doc"] and not p["address"]:
                p["address"] = p["doc"]["address"]
    out = []
    for p in places.values():
        if p["lat"] is None and p["points"]:  # 위치가 없으면 첫 지점(코스 시작점)
            p["lat"], p["lon"] = p["points"][0]["lat"], p["points"][0]["lon"]
        if p["lat"] is None:
            continue
        p["barrier_points"] = sum(1 for x in p["points"] if BARRIER_WORDS.search(x["info"] + x["label"]))
        p["toilet_points"] = sum(
            1 for x in p["points"]
            if "장애인화장실" in (x["info"] + x["label"]).replace(" ", "") and not NO_WORDS.search(x["info"])
        )
        out.append(p)
    return sorted(out, key=lambda p: p["name"])


def verified_segment_ids(places: list[dict], segments, within_m: float = 10, share: float = 0.95) -> frozenset[str]:
    """무장애 조사자가 실제로 걸은 GPS 코스 위의 보행 구간(길이의 share 이상이 코스 within_m 안).
    계단·경사·턱 주의 지점 within_m 안에 조금이라도 닿는 구간은 통째로 뺀다(구간 단위로만 표시하므로
    일부만 빼면 그 장애물까지 확인된 길이 된다).
    share는 근거 없는 값: 구간 끝이 코스 밖으로 조금 나가는 GPS 오차만 봐준다.
    shortcut: 위경도를 제주 위도 기준 평면 미터로 근사(오차 수 m), 넓은 지역에 쓰면 투영 좌표로 바꿀 것"""
    import shapely  # noqa: PLC0415 - 경로 엔진을 쓸 때만
    from shapely.geometry import LineString, Point  # noqa: PLC0415

    k = math.cos(math.radians(33.4))

    def xy(lon: float, lat: float) -> tuple[float, float]:
        return lon * 111_320 * k, lat * 110_574

    zones = [LineString([xy(lon, lat) for lat, lon in p["track"]]).buffer(within_m)
             for p in places if len(p.get("track") or []) >= 2]  # noqa: PLR2004
    if not zones:
        return frozenset()
    bad = [Point(xy(x["lon"], x["lat"])).buffer(within_m) for p in places for x in p["points"]
           if BARRIER_WORDS.search(x["info"] + x["label"])]
    zone = shapely.union_all(zones)
    barrier = shapely.union_all(bad) if bad else None
    lines = [LineString([xy(*c) for c in s.coords]) for s in segments]
    tree = shapely.STRtree(lines)
    out = set()
    for z in zones:
        for i in tree.query(z, predicate="intersects"):
            line = lines[i]
            if barrier is not None and line.intersects(barrier):
                continue
            if line.length > 0 and line.intersection(zone).length >= share * line.length:
                out.add(segments[i].seg_id)
    return frozenset(out)
