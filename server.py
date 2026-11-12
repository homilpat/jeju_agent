"""F11 키 보관 서버: poc/ 를 서빙하고, .env 의 키를 브라우저에 LLM 키 없이 넘긴다.

    python server.py   → http://localhost:8000

- /config.js : KAKAO_JS_KEY 만 내려줌 (카카오 JS 키는 원래 브라우저용, 보호는 도메인 등록으로)
- /api/chat  : LLM_API_KEY 로 서버에서만 LLM 호출 → 의도/슬롯 JSON + 답변 (F3, F10)
"""

import json
import os
import re
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


load_env()
KAKAO = os.environ.get("KAKAO_JS_KEY", "")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
# 미정: Muse Spark 1.3 엔드포인트·모델명. OpenAI 호환 chat/completions 형식 가정.
LLM_BASE = os.environ.get("LLM_API_BASE", "https://api.llama.com/compat/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "Muse-Spark-1.3")

SYSTEM = """너는 제주시 원도심 보행 길안내 도우미다. 사용자의 말에서 아래 JSON만 출력한다.
{"origin": 출발지 한국어 이름 또는 null, "destination": 목적지 한국어 이름 또는 null,
 "route_type": "safe"|"short"|"bal"|null, "wheelchair": true|false,
 "facility": "toilets"|"pharmacy"|null, "lang": "ko"|"en", "reply": 사용자 언어로 짧은 답변}
- 외국어·로마자 지명은 국어 로마자 표기법에 따라 한국어로 바꿔 적는다 (예: Gwandeokjeong → 관덕정).
- 경로는 네가 만들지 않는다. 거리·시간·길 설명을 지어내지 않는다.
- 범죄 확률, CCTV 촬영범위, Wi-Fi 무중단, 휠체어 통행 보장 같은 표현을 쓰지 않는다."""

# F10 금지 문구 → 대체 문구 (LLM 출력 뒤 코드로 한 번 더 검사)
FORBIDDEN = [
    (r"범죄\s*(발생\s*)?(확률|가능성)", "주변 환경 지표"),
    (r"CCTV\s*(촬영\s*)?(범위|사각)", "CCTV 설치 밀도"),
    (r"(Wi-?Fi|와이파이)\s*(가\s*)?(무중단|끊김\s*없)", "와이파이 설치 위치 근접도"),
    (r"휠체어\s*(통행|이용)?\s*(보장|100%)", "휠체어 통행 확인 구간"),
    (r"crime\s*(probability|rate|risk)", "environment indicators"),
    (r"CCTV\s*coverage", "CCTV density"),
    (r"(uninterrupted|seamless)\s*Wi-?Fi", "Wi-Fi hotspot proximity"),
    (r"wheelchair[- ]?(accessible\s*)?guarantee[d]?", "verified wheelchair segments"),
]


def scrub(text: str) -> str:
    for pat, rep in FORBIDDEN:
        text = re.sub(pat, rep, text, flags=re.I)
    return text


def ask_llm(message: str) -> dict:
    body = json.dumps({"model": LLM_MODEL, "temperature": 0,
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": message}]}).encode()
    req = urllib.request.Request(f"{LLM_BASE}/chat/completions", data=body, headers={
        "Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        content = json.load(r)["choices"][0]["message"]["content"]
    out = json.loads(content[content.find("{"): content.rfind("}") + 1])
    out["reply"] = scrub(str(out.get("reply") or ""))
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT / "poc"), **kw)

    def _json(self, code: int, obj) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/config.js":
            js = f"window.KAKAO_JS_KEY={json.dumps(KAKAO)};window.HAS_LLM={json.dumps(bool(LLM_KEY))};"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.end_headers()
            self.wfile.write(js.encode())
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/chat" or not LLM_KEY:
            return self._json(404, {"error": "LLM 미설정"})
        n = min(int(self.headers.get("Content-Length", 0)), 4000)
        try:
            msg = str(json.loads(self.rfile.read(n))["message"])[:500]
            self._json(200, ask_llm(msg))
        except Exception as e:  # LLM 실패 → 브라우저가 규칙 모드로 처리
            self._json(502, {"error": type(e).__name__})


if __name__ == "__main__":
    assert scrub("범죄 확률이 낮아요") == "주변 환경 지표이 낮아요"
    print("http://localhost:8000  (LLM:", "ON" if LLM_KEY else "OFF", ", Kakao:", "ON" if KAKAO else "OFF", ")")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
