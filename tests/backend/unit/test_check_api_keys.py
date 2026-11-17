import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(SCRIPTS))  # fetch_jeju_data imports check_api_keys
check_api_keys = _load("check_api_keys")
fetch_jeju_data = _load("fetch_jeju_data")

KEY = "abc+def/ghi=="


@pytest.mark.parametrize(
    "echoed",
    [
        KEY,
        urllib.parse.quote(KEY),
        urllib.parse.urlencode({"k": KEY})[2:],  # 실제 요청에 쓰는 인코딩
        json.dumps(KEY)[1:-1].replace("/", "\\/"),  # JSON 이스케이프된 슬래시
    ],
)
def test_summarize_never_echoes_keys(echoed):
    body = '{"resultMsg": "bad key ' + echoed + '", "totalCount": 3}'
    out = check_api_keys.summarize(200, body, [KEY])
    assert KEY not in out
    assert echoed not in out
    assert "totalCount" in out


def test_summarize_hides_place_details():
    body = json.dumps({"documents": [{"place_name": "어느약국", "x": "126.5"}], "meta": {}})
    out = check_api_keys.summarize(200, body, [])
    assert "어느약국" not in out
    assert '"documents": 1' in out


def test_summarize_ignores_empty_secret():
    assert check_api_keys.summarize(200, '{"totCnt": 1}', [""]) == 'HTTP 200 | {"totCnt": 1}'


def test_fetch_error_does_not_leak_url():
    status, body = check_api_keys.fetch(f"https://example.invalid/{KEY} with space")
    assert status == 0
    assert KEY not in body


def test_load_env_reads_values_and_skips_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# note=1\nA_KEY="x=1"\nEMPTY=\n', encoding="utf-8")
    assert check_api_keys.load_env(env) == {"A_KEY": "x=1", "EMPTY": ""}


def _pages(rows, total, has_more_forever=False):
    """Fake hub: returns rows[page] for each page number, optionally never ending."""

    def get(url):
        page = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["number"][0])
        chunk = rows[page - 1] if page <= len(rows) else []
        return {"totCnt": total, "hasMore": has_more_forever or page < len(rows), "data": chunk}

    return get


def test_hub_rows_stops_when_page_number_is_ignored():
    get = _pages([[{"a": 1}]], total=1, has_more_forever=True)
    assert fetch_jeju_data.hub_rows("b?", get=get) == [{"a": 1}]


def test_hub_rows_fails_instead_of_saving_missing_rows():
    get = _pages([[{"a": 1}, {"a": 2}]], total=3)
    with pytest.raises(RuntimeError, match="불완전"):
        fetch_jeju_data.hub_rows("b?", get=get)
    assert len(fetch_jeju_data.hub_rows("b?", get=get, allow_incomplete=True)) == 2


def test_hub_rows_rejects_error_body_with_http_200():
    with pytest.raises(RuntimeError, match="형식"):
        fetch_jeju_data.hub_rows("b?", get=lambda url: {"message": "Today's quota is full"})


def test_write_csv_refuses_empty_rows():
    with pytest.raises(RuntimeError):
        fetch_jeju_data.write_csv("x", [])


def test_summarize_masks_lowercase_percent_encoding():
    echoed = urllib.parse.quote(KEY, safe="").lower()
    out = check_api_keys.summarize(200, '{"resultMsg": "' + echoed + '"}', [KEY])
    assert echoed not in out


def test_get_json_stops_at_call_budget(monkeypatch):
    monkeypatch.setattr(fetch_jeju_data, "_calls", fetch_jeju_data.CALL_BUDGET)
    monkeypatch.setattr(fetch_jeju_data, "fetch", lambda url, headers=None: (200, "{}"))
    with pytest.raises(RuntimeError, match="상한"):
        fetch_jeju_data.get_json("u")
