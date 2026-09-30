"""Check every "In the code" anchor in the decision records against the source.

Run from the repository root: ``python3 docs/decisions/check_anchors.py``.
An anchor line looks like ``- `modes.py:350-354` - `Mode.UNOCCUPIED` - text``.
The symbol must appear within two lines of the cited range, in the file under
``custom_components/abode_hvac_coordinator/``. Exits non-zero on any miss.
"""

from __future__ import annotations

import pathlib
import re
import sys

BASE = pathlib.Path("custom_components/abode_hvac_coordinator")
RECORDS = pathlib.Path("docs/decisions")
ANCHOR = re.compile(r"^- `([\w./]+):(\d+)(?:-(\d+))?` - `([^`]+)`")


def main() -> int:
    checked = 0
    misses = 0
    for record in sorted(RECORDS.glob("0*.md")):
        for line in record.read_text(encoding="utf-8").splitlines():
            match = ANCHOR.match(line)
            if not match:
                continue
            checked += 1
            path, start, end, symbol = match.groups()
            first = int(start)
            last = int(end or start)
            source = (BASE / path).read_text(encoding="utf-8").splitlines()
            window = "\n".join(source[max(0, first - 3) : last + 2])
            if symbol not in window:
                misses += 1
                print(f"MISS {record.name}: {path}:{first}-{last} `{symbol}`")
    print(f"{checked} anchors checked, {misses} missed")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
