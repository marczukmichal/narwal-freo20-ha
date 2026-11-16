"""Fail if the repository contains personal data from the private setup.

    python3 tools/scan_private.py [extra-term ...]

Checks generic patterns (IPs, emails, tokens, keys, long hex ids) and any extra
terms given on the command line or in the PRIVATE_TERMS environment variable
(comma separated), such as a device id or a home domain. The extra terms are
never written to the repository.
"""
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__"}
SKIP_FILES = {"tools/scan_private.py"}
PATTERNS = {
    "private IP": re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}"),
    "bearer/JWT": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "32-hex id": re.compile(r"\b[0-9a-f]{32}\b"),
    "long token": re.compile(r"(?i)(token|secret|password|apikey|api_key)\s*[:=]\s*['\"][A-Za-z0-9+/_=-]{16,}['\"]"),
}
# Documented example values that are fine to keep.
ALLOW = {"192.168.1.50"}


def main() -> int:
    terms = [t for t in sys.argv[1:] + os.environ.get("PRIVATE_TERMS", "").split(",") if t.strip()]
    hits = []
    for f in sorted(REPO.rglob("*")):
        rel = f.relative_to(REPO).as_posix()
        if not f.is_file() or SKIP_DIRS & set(f.parts) or rel in SKIP_FILES:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for name, pat in PATTERNS.items():
                for m in pat.finditer(line):
                    if m.group(0) not in ALLOW:
                        hits.append(f"{rel}:{n}: {name}: {m.group(0)[:40]}")
            low = line.lower()
            for t in terms:
                if t.strip().lower() in low:
                    hits.append(f"{rel}:{n}: private term #{terms.index(t) + 1}")
    print("\n".join(hits) if hits else "clean")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
