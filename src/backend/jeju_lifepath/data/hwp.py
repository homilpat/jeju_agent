"""한글(HWP 5.x) 문서에서 본문 글자만 뽑는다. 표·그림 위치는 버리고 문단 글자만 이어 붙인다.

HWP 5 파일은 OLE 묶음이고, 본문은 BodyText/SectionN 흐름에 레코드로 들어 있다(압축이면 zlib raw).
레코드 머리 32비트: 종류 10비트, 단계 10비트, 크기 12비트(0xFFF면 다음 4바이트가 크기).
문단 글자 레코드(HWPTAG_PARA_TEXT=67)는 UTF-16LE이고, 32 미만 값은 제어 문자다:
1~9·11~12·14~23 중 일부는 8글자 폭(인라인·확장 제어), 나머지(0·10·13·24~31)는 1글자 폭.
근거: 한글과컴퓨터 "HWP 5.0 문서 파일 구조" 공개 문서(2018 개정).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import olefile

PARA_TEXT = 67
WIDE_CONTROLS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _para_text(data: bytes) -> str:
    out, i = [], 0
    while i + 1 < len(data):
        ch = struct.unpack_from("<H", data, i)[0]
        if ch >= 32:
            out.append(chr(ch))
            i += 2
        elif ch in WIDE_CONTROLS:
            i += 16  # 제어 문자 + 7글자 폭의 부가 정보
            if ch == 9:
                out.append("\t")
        else:
            if ch in (10, 13):
                out.append("\n")
            i += 2
    return "".join(out)


def _records(stream: bytes):
    i = 0
    while i + 4 <= len(stream):
        head = struct.unpack_from("<I", stream, i)[0]
        tag, size = head & 0x3FF, (head >> 20) & 0xFFF
        i += 4
        if size == 0xFFF:
            size = struct.unpack_from("<I", stream, i)[0]
            i += 4
        yield tag, stream[i : i + size]
        i += size


def hwp_text(path: Path) -> str:
    """HWP 5 문서의 본문 글자. 읽을 수 없는 문서면 ValueError."""
    if not olefile.isOleFile(str(path)):
        raise ValueError("HWP 5 문서가 아님")
    with olefile.OleFileIO(str(path)) as ole:
        header = ole.openstream("FileHeader").read()
        compressed = bool(header[36] & 1)
        sections = sorted(
            (e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText" and e[1].startswith("Section")),
            key=lambda e: int(e[1][7:]),
        )
        parts = []
        for entry in sections:
            raw = ole.openstream(entry).read()
            if compressed:
                raw = zlib.decompress(raw, -15)
            parts += [_para_text(body) for tag, body in _records(raw) if tag == PARA_TEXT]
    return "\n".join(p.strip() for p in parts if p.strip())
