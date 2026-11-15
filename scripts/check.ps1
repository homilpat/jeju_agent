# GitHub CI 대체 로컬 검사: 린트 + 테스트. 실패 시 non-zero 종료.
$ErrorActionPreference = "Stop"
uv sync --locked --extra dev
if ($LASTEXITCODE) { exit $LASTEXITCODE }
uv run ruff check .
if ($LASTEXITCODE) { exit $LASTEXITCODE }
uv run pytest -q
exit $LASTEXITCODE
