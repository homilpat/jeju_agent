# frontend

React + Vite(JavaScript) 폰·웹 겸용 화면. 피그마 메이크 디자인("제주 안전 여행 플래너")에서 옮김.
UI는 FastAPI 백엔드의 공개 API(`/api/...`)만 사용하며 경로·지표 수치를 직접 계산하지 않는다.
카카오맵 JS SDK 결과는 화면에서만 쓰고 저장하지 않는다(장소ID·이름만 플래너에 저장).

## 실행

개발 (두 창):

```
# 1) API 서버
.venv/Scripts/python -m uvicorn jeju_lifepath.api.app:app --port 8001 --app-dir src/backend --reload
# 2) 화면 개발 서버 (src/frontend 에서)
npm install
npm run dev        # http://localhost:8000 , /api 는 8001로 전달
```

시연 (서버 하나):

```
cd src/frontend && npm run build && cd ../..
.venv/Scripts/python -m uvicorn jeju_lifepath.api.app:app --port 8000 --app-dir src/backend
# http://localhost:8000
```

연결 상태: 로그인·회원가입·플래너 목록/저장/삭제·계정 삭제는 서버와 연결됨.
장소 검색과 지도 핀은 카카오 JS SDK로 연결됨(`src/kakao.js`, 키는 저장소 최상위 `.env`의 `KAKAO_JS_KEY`, 등록 주소 `localhost:8000`). 검색은 제주를 덮는 사각형으로 좁힌다. 저장된 장소는 열 때 이름으로 다시 검색해 같은 장소 ID를 찾는다. 경로 비교, 여행 중 길 안내, 근처 찾기는 아직 "준비 중" 안내만 보여준다.
