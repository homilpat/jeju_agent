"""원도심 OSM 원본을 한 번 받아 data/osm/wondosim.json 에 저장 (실행 중 OSM 호출 안 함).

    python scripts/fetch_osm.py
"""

import json
import urllib.parse
import urllib.request
from pathlib import Path

BBOX = (33.505, 126.512, 33.521, 126.536)  # south, west, north, east — 제주시 원도심
OUT = Path(__file__).resolve().parents[1] / "data" / "osm" / "wondosim.json"

QUERY = f"""
[out:json][timeout:60];
(
  way["highway"]{BBOX};
  node["amenity"~"^(toilets|pharmacy)$"]{BBOX};
  nwr["name"]["tourism"]{BBOX};
  nwr["name"]["historic"]{BBOX};
  nwr["name"]["amenity"~"^(marketplace|townhall|library|place_of_worship)$"]{BBOX};
  nwr["name"]["leisure"~"^(park)$"]{BBOX};
  nwr["name"]["shop"="mall"]{BBOX};
);
out body geom;
"""

if __name__ == "__main__":
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": QUERY}).encode(),
        headers={"User-Agent": "jeju-poc-demo/0.1"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(len(data["elements"]), "elements ->", OUT)
