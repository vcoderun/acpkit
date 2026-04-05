from __future__ import annotations as _annotations

import json
import os
from pathlib import Path


def main() -> None:
    workspace_root = Path(os.environ["VCODE_HOOK_WORKSPACE_ROOT"])
    payload = os.environ.get("VCODE_HOOK_PAYLOAD_JSON", "{}")
    output_dir = workspace_root / ".vcode" / "test-artifacts" / "hooks"
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "last-payload.json"
    snapshot_path.write_text(
        json.dumps(json.loads(payload), ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("snapshot:updated")


if __name__ == "__main__":
    main()
