import re
from pathlib import Path

import pytest

from jeju_lifepath.geo.graph import build_graph, load_edges
from jeju_lifepath.schemas import RoadEdge

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def toy_edges():
    return load_edges(FIXTURES / "toy_graph.json")


@pytest.fixture
def toy_graph(toy_edges):
    return build_graph(toy_edges)


# 사용자 문구 누출 검사: JSON 기호, 밑줄(필드명), 구간 ID, 대문자 코드(허용어 제외)
_LEAK = re.compile(r"[{}\[\]_]|\be_[A-Za-z]+|\b(?:sx|xt|sy|yt)\b|\b[A-Z]{2,}\b")
_ALLOWED_CAPS = {"CCTV"}


@pytest.fixture
def assert_user_text():
    def check(text: str) -> None:
        bad = [m for m in _LEAK.findall(text) if m not in _ALLOWED_CAPS]
        assert not bad, f"내부값 노출 {bad}: {text}"

    return check


def edge(eid, s, t, length, **kw):
    kw.setdefault("accessibility", "PASS")
    kw.setdefault("closure_status", "OPEN")
    return RoadEdge(
        edge_id=eid, source_node=s, target_node=t, geometry=[(0, 0), (1, 1)],
        length_m=length, source_ids=["DEMO"], **kw,
    )


@pytest.fixture
def two_way():
    # S-X-T: 짧고 CCTV 없음 / S-Y-T: 10% 길고 CCTV 높음
    return build_graph([
        edge("sx", "S", "X", 100, cctv_index=0.0),
        edge("xt", "X", "T", 100, cctv_index=0.0),
        edge("sy", "S", "Y", 110, cctv_index=1.0),
        edge("yt", "Y", "T", 110, cctv_index=1.0),
    ])
