from __future__ import annotations as _annotations

import ast
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _extract_exports(module_path: Path) -> tuple[str, ...]:
    module = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "__all__":
                value = ast.literal_eval(node.value)
                if isinstance(value, tuple | list) and all(isinstance(item, str) for item in value):
                    return tuple(value)
    raise ValueError(f"Could not locate __all__ in {module_path}")


def main() -> None:
    root = _repo_root()
    module_paths = {
        "acpkit": root / "src" / "acpkit" / "__init__.py",
        "pydantic_acp": root
        / "packages"
        / "adapters"
        / "pydantic-acp"
        / "src"
        / "pydantic_acp"
        / "__init__.py",
        "codex_auth_helper": root
        / "packages"
        / "helpers"
        / "codex-auth-helper"
        / "src"
        / "codex_auth_helper"
        / "__init__.py",
    }
    for package_name, module_path in module_paths.items():
        print(f"[{package_name}]")
        for export_name in _extract_exports(module_path):
            print(f"- {export_name}")
        print()


if __name__ == "__main__":
    main()
