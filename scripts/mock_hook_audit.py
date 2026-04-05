from __future__ import annotations as _annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    workspace_root = Path(os.environ["VCODE_HOOK_WORKSPACE_ROOT"])
    payload = os.environ.get("VCODE_HOOK_PAYLOAD_JSON", "{}")
    output_dir = workspace_root / ".vcode" / "test-artifacts" / "hooks"
    output_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "event": os.environ.get("VCODE_HOOK_EVENT", ""),
        "mode": os.environ.get("VCODE_HOOK_MODE_ID", ""),
        "session_id": os.environ.get("VCODE_HOOK_SESSION_ID", ""),
        "payload": json.loads(payload),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    audit_path = output_dir / "audit.jsonl"
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True, sort_keys=True))
        handle.write("\n")
    print(f"audit:{entry['event']}")


if __name__ == "__main__":
    main()
