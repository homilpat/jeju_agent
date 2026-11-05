# 게이트 기록

## G0 — 저장소·스키마·toy graph (2026-11-06)
- 변경: `pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`, `scripts/check.ps1`, `src/jeju_lifepath/schemas/{edge,request,result}.py`, `src/jeju_lifepath/geo/graph.py`, `tests/`
- 테스트: `scripts/check.ps1` 로컬 실행 — ruff 통과, pytest 12 passed
- 데이터: toy graph는 DEMO 합성 데이터 (제주 공식 데이터 아님)
- 블로커: GitHub CI 미사용 → 로컬 검사로 대체. G1(공식 데이터 확보) 미착수.

## G5·G6 — 통행 제약 + CA-MOR 경로 (toy graph, 2026-11-06)
- 변경: `routing/{constraints,cost,optimizer}.py`, `validation/route.py`, `tests/unit/test_routing.py`
- 테스트: ruff 통과, pytest 23 passed
- 검증 항목: BLOCK/CLOSED 미사용, PASS-only 접근성, UNKNOWN 포함 시 OK 미표시, 우회율 상한, 중복 후보 명시, 결정론, 결측 지표 ≠ 안전
- 한계: 가중치·lambda는 잠정값(`provisional-v0`, G4에서 근거 검증). 좌표 스냅 미구현(G2). 평행 구간은 비용 최소 1개만 후보.
