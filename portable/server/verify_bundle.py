"""Verify the extracted bundle against its local, unsigned SHA-256 manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parent
    try:
        manifest = json.loads((root / "BUNDLE-MANIFEST.json").read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            target = (root / entry["path"]).resolve()
            if not target.is_relative_to(root):
                raise ValueError("Manifest path leaves the bundle")
            content = target.read_bytes()
            if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ValueError("Bundle file failed verification: " + entry["path"])
        print(json.dumps({"ok": True, "bundle": manifest["bundle"], "files": len(manifest["files"])}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
