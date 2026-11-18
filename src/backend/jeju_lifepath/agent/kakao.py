"""카카오 로컬 REST(서버 쪽). 결과(이름·주소·좌표)는 이번 계산에만 쓰고 저장하지 않는다(카카오 운영정책).
저장되는 것은 장소 ID와 사용자가 쓴 검색어뿐이다."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from jeju_lifepath.agent.models import ToolError

BASE = "https://dapi.kakao.com/v2/local"
# 제주도 전체 사각형(경도, 위도). api/app.py JEJU_LON/JEJU_LAT과 같음
JEJU_RECT = "125.9,33.0,127.0,34.05"
SOURCE = "카카오 장소 검색"


def to_place(doc: dict) -> dict:
    return {
        "ref": doc["id"],
        "name": doc.get("place_name") or doc.get("address_name") or "",
        "category": (doc.get("category_name") or "").split(" > ")[-1] or None,
        # 분류 경로 전체(종류 거르기용, 화면에 쓰지 않음)
        "category_path": [x for x in (doc.get("category_name") or "").split(" > ") if x],
        "address": doc.get("road_address_name") or doc.get("address_name") or "",
        "lon": float(doc["x"]),
        "lat": float(doc["y"]),
        "url": doc.get("place_url") or f"https://place.map.kakao.com/{doc['id']}",
        "distance_m": int(doc["distance"]) if doc.get("distance") else None,
    }


class KakaoLocal:
    def __init__(
        self, api_key: str, timeout_s: float = 8, opener: Callable = urllib.request.urlopen
    ):
        self._key = api_key
        self.timeout_s = timeout_s
        self._open = opener

    def _get(self, path: str, params: dict) -> dict:
        if not self._key:
            raise ToolError("auth_config", "kakao key missing")
        url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {self._key}"})
        try:
            with self._open(req, timeout=self.timeout_s) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ToolError("auth_config", f"kakao http {e.code}") from None
            if e.code == 429 or e.code >= 500:
                ra = e.headers.get("Retry-After")
                raise ToolError(
                    "transient", f"kakao http {e.code}", float(ra) if ra and ra.isdigit() else None
                ) from None
            raise ToolError("invalid_args", f"kakao http {e.code}") from None
        except TimeoutError:
            raise ToolError("timeout", "kakao") from None
        except (urllib.error.URLError, OSError):
            raise ToolError("transient", "kakao network") from None

    def keyword(
        self,
        query: str,
        near: tuple[float, float] | None = None,
        radius_m: int | None = None,
        size: int = 15,
    ) -> list[dict]:
        params: dict = {"query": query, "size": size}
        if near and radius_m:
            params.update(x=near[0], y=near[1], radius=min(radius_m, 20000), sort="distance")
        else:
            params["rect"] = JEJU_RECT
        docs = self._get("search/keyword.json", params)["documents"]
        return [to_place(d) for d in docs if (d.get("address_name") or "").startswith("제주")]

    def category(
        self, code: str, near: tuple[float, float], radius_m: int, size: int = 15
    ) -> list[dict]:
        docs = self._get(
            "search/category.json",
            {
                "category_group_code": code,
                "x": near[0],
                "y": near[1],
                "radius": min(radius_m, 20000),
                "sort": "distance",
                "size": size,
            },
        )["documents"]
        return [to_place(d) for d in docs]

    def search_all(
        self,
        *,
        query: str | None = None,
        code: str | None = None,
        near: tuple[float, float] | None = None,
        radius_m: int | None = None,
        rect: tuple[float, float, float, float] | None = None,
    ) -> tuple[list[dict], bool]:
        """카카오가 주는 만큼(쪽당 15곳, 최대 3쪽 = 45곳). 키워드(query) 또는 분류 코드(code),
        위치는 원(near, radius_m) 또는 사각형(rect = 서, 남, 동, 북).
        반환 (장소들, 잘림): 잘림은 카카오가 45곳보다 더 있다고 알렸을 때 True."""
        params: dict = {"size": 15}
        if query:
            params["query"] = query
        else:
            params["category_group_code"] = code
        if rect:
            params["rect"] = ",".join(str(v) for v in rect)
        else:
            params.update(x=near[0], y=near[1], radius=min(radius_m, 20000), sort="distance")
        path = "search/keyword.json" if query else "search/category.json"
        out: list[dict] = []
        meta: dict = {}
        for page in (1, 2, 3):
            data = self._get(path, {**params, "page": page})
            out += [to_place(d) for d in data["documents"]
                    if (d.get("address_name") or "").startswith("제주")]
            meta = data.get("meta") or {}
            if meta.get("is_end", True):
                break
        return out, int(meta.get("total_count") or 0) > int(meta.get("pageable_count") or 0)

    def address(self, query: str) -> tuple[float, float] | None:
        docs = self._get("search/address.json", {"query": query})["documents"]
        return (float(docs[0]["x"]), float(docs[0]["y"])) if docs else None

    def region(self, lon: float, lat: float) -> dict | None:
        docs = self._get("geo/coord2regioncode.json", {"x": lon, "y": lat})["documents"]
        jeju = [d for d in docs if d["region_1depth_name"].startswith("제주")]
        if not jeju:
            return None
        return {
            "city": jeju[0]["region_2depth_name"],
            "regions": sorted({d["region_3depth_name"] for d in jeju if d["region_3depth_name"]}),
        }
