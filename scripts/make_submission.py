"""제출용 ZIP 만들기: git에 올라간 파일만(HEAD) 묶고, 평가용 LLM 키 파일만 `.env`로 더한다.

사용: python scripts/make_submission.py <만들 zip 경로> [--eval-env <평가용 키 파일>]
- `.git`, `data/private/`, 배포 금지 엑셀, 출처 모를 PDF, 실제 데이터 API 키는 git에 없으므로 들어가지 않는다.
- 평가용 키 파일에는 LLM_* 값만 허용한다(대회 규칙 4.2·4.3: 외부 AI API 평가용 키만 예외).
"""

import io
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_DAILY_BUDGET_USD")


def eval_env_text(text: str) -> str:
    """평가용 키 파일 검사: LLM_* 말고 값이 든 줄이 있으면 거부(다른 키가 섞여 나가지 않게)."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        name, _, value = s.partition("=")
        if value.strip() and name.strip() not in ALLOWED:
            raise ValueError(f"평가용 키 파일에 넣으면 안 되는 값: {name.strip()}")
        if value.strip():
            out.append(f"{name.strip()}={value.strip()}")
    if not any(x.startswith("LLM_API_KEY=") for x in out):
        raise ValueError("LLM_API_KEY가 없음")
    return "# 평가용 LLM 키(사용 한도 설정, 평가 기간이 끝나면 폐기). 다른 키는 README의 환경변수 표대로 직접 넣는다\n" + "\n".join(out) + "\n"


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    out = Path(sys.argv[1])
    env = None
    if "--eval-env" in sys.argv:
        env = eval_env_text(Path(sys.argv[sys.argv.index("--eval-env") + 1]).read_text(encoding="utf-8"))
    if subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                      capture_output=True, text=True, check=True).stdout.strip():
        sys.exit("커밋하지 않은 변경이 있다. 커밋한 뒤 다시 실행")
    data = subprocess.run(["git", "archive", "--format=zip", "--prefix=jeju_agent/", "HEAD"], cwd=ROOT,
                          capture_output=True, check=True).stdout
    buf = io.BytesIO(data)
    if env:
        with zipfile.ZipFile(buf, "a") as z:
            z.writestr("jeju_agent/.env", env)
    out.write_bytes(buf.getvalue())
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    print(f"{out.name}: 파일 {len(names)}개, {out.stat().st_size / 1e6:.1f}MB, 평가용 키 {'포함' if env else '없음'}")


if __name__ == "__main__":
    main()
