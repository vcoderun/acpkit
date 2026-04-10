from __future__ import annotations as _annotations

from pathlib import Path

_EXAMPLE_DESCRIPTIONS = {
    "approvals.py": "native deferred approval flow",
    "bridges.py": "bridge builder and ACP-visible capabilities",
    "factory_agent.py": "session-aware factory plus session-local model selection",
    "hook_projection.py": "hook event labels and visibility controls",
    "host_context.py": "client-backed filesystem and terminal helpers",
    "providers.py": "host-owned models, modes, config, plan state, and approval metadata",
    "static_agent.py": "smallest possible run_acp(agent=...) integration",
    "strong_agent.py": "full workspace coding-agent showcase",
    "strong_agent_v2.py": "alternative workspace integration shape",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main() -> None:
    examples_dir = _repo_root() / "examples" / "pydantic"
    for example_path in sorted(examples_dir.glob("*.py")):
        if example_path.name == "__init__.py":
            continue
        description = _EXAMPLE_DESCRIPTIONS.get(example_path.name, "no mapped description")
        print(f"{example_path.relative_to(_repo_root())}: {description}")


if __name__ == "__main__":
    main()
