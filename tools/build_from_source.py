"""Rebuild this repository's code from the private source setup.

    python3 tools/build_from_source.py /path/to/private/homeassistant

Copies the shared files, applies the English text pairs in tools/i18n_en.py,
then copies tools/overrides on top. Fails if any pair no longer matches.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from i18n_en import PAIRS  # noqa: E402
import re  # noqa: E402
import i18n_dashboard_en as UI  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SOURCES = {  # repo path -> path in the source tree
    "custom_components/narwal_local": "ha/custom_components/narwal_local",
    "custom_components/narwal_chmura": "ha/custom_components/narwal_chmura",
    "custom_components/narwal_mapa": "ha/custom_components/narwal_mapa",
    "addons/narwal_kamera": "addons/narwal_kamera",
    "www/narwal/dashboard.js": "ha/www/narwal/dashboard.js",
    "www/narwal/camera-audio.js": "ha/www/narwal/camera-audio.js",
    "www/narwal/audio-worklet.js": "ha/www/narwal/audio-worklet.js",
}
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def sub(text: str, old: str, new: str) -> tuple[str, bool]:
    """Exact replacement, or a regex when the key starts with "re:"."""
    if old.startswith("re:"):
        out, n = re.subn(old[3:], lambda _m: new, text)
        return out, n > 0
    return text.replace(old, new), old in text


def main(src: Path) -> None:
    for dst, s in SOURCES.items():
        a, b = src / s, REPO / dst
        if b.is_dir():
            shutil.rmtree(b)
        b.parent.mkdir(parents=True, exist_ok=True)
        if a.is_dir():
            shutil.copytree(a, b, ignore=SKIP)
        else:
            shutil.copy2(a, b)
    missing = []
    for rel, pairs in PAIRS.items():
        f = REPO / rel
        text = f.read_text(encoding="utf-8")
        for old, new in sorted(pairs, key=lambda p: -len(p[0])):
            text, ok = sub(text, old, new)
            if not ok:
                missing.append(f"{rel}: {old[:70]!r}")
        f.write_text(text, encoding="utf-8")
    missing += translate_ui()
    ov = REPO / "tools" / "overrides"
    for f in ov.rglob("*"):
        if f.is_file():
            t = REPO / f.relative_to(ov)
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, t)
    if missing:
        sys.exit("Pairs that no longer match:\n  " + "\n  ".join(missing))
    print("ok")


RAW = re.compile(r"[^A-Za-z0-9]|[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")


def translate_ui() -> list[str]:
    files = {rel: (REPO / rel).read_text(encoding="utf-8")
             for rel in ("www/narwal/dashboard.js", "www/narwal/camera-audio.js")}
    missing = []
    for rel, pairs in UI.CODE.items():
        for old, new in pairs:
            files[rel], ok = sub(files[rel], old, new)
            if not ok:
                missing.append(f"{rel}: {old[:70]!r}")
    for old, new in UI.IDS.items():
        files["www/narwal/dashboard.js"] = files["www/narwal/dashboard.js"].replace(old, new)
    for old, new in sorted(UI.TEXT.items(), key=lambda p: -len(p[0])):
        hit = False
        for rel, text in files.items():
            if RAW.search(old.replace(" ", "x") if " " not in old else old) or " " in old:
                if old in text:
                    hit = True
                    files[rel] = text.replace(old, new)
            else:
                pat = re.compile(r"(?<=['\">`}])" + re.escape(old) + r"(?=['\"<`$])")
                if pat.search(text):
                    hit = True
                    files[rel] = pat.sub(lambda _m: new, text)
        if not hit:
            missing.append(f"UI text: {old[:70]!r}")
    for rel, text in files.items():
        (REPO / rel).write_text(text, encoding="utf-8")
    return missing


if __name__ == "__main__":
    main(Path(sys.argv[1]))
