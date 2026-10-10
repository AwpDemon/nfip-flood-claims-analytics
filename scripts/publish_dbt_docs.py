"""Generate the single-file dbt docs site and copy it to docs/dbt-docs/index.html.

Absolute local paths (which dbt embeds in the manifest) are rewritten to repo-relative ones
so the committed file does not leak the build machine's directory layout.

Usage: python scripts/publish_dbt_docs.py   (needs the built warehouse for the catalog)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def scrub(html: str, root: Path) -> str:
    variants = {
        root.as_posix(),
        str(root),
        str(root).replace("\\", "\\\\"),
    }
    for v in sorted(variants, key=len, reverse=True):
        html = html.replace(v, ".")
    # any remaining absolute Windows/Posix home-directory prefixes
    return re.sub(r"[A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/)[^\\/\"']+", "~", html)


def main() -> None:
    res = subprocess.run([sys.executable, "-m", "nfip", "dbt", "docs", "generate", "--static", "--target-path", "target-docs"], cwd=ROOT)
    if res.returncode:
        raise SystemExit(res.returncode)
    src = ROOT / "dbt" / "target-docs" / "static_index.html"
    out = ROOT / "docs" / "dbt-docs" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(scrub(src.read_text(encoding="utf-8"), ROOT), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
