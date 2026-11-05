# JEJU LifePath AI

설계: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · 게이트 기록: [docs/VALIDATION.md](docs/VALIDATION.md)

## 로컬 검사 (GitHub CI 대체)
```powershell
uv sync
powershell -ExecutionPolicy Bypass -File scripts/check.ps1   # ruff + pytest
```
