# 게이트 기록

## G0 — 저장소·스키마·toy graph (2026-11-06)
- 변경: `pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`, `scripts/check.ps1`, `src/jeju_lifepath/schemas/{edge,request,result}.py`, `src/jeju_lifepath/geo/graph.py`, `tests/`
- 테스트: `scripts/check.ps1` 로컬 실행 — ruff 통과, pytest 12 passed
- 데이터: toy graph는 DEMO 합성 데이터 (제주 공식 데이터 아님)
- 블로커: GitHub CI 미사용 → 로컬 검사로 대체. G1(공식 데이터 확보) 미착수.
