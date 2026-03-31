import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/rename_workspace.py <new_name>")
        sys.exit(1)

    new_name_underscore = sys.argv[1]
    new_name_dash = new_name_underscore.replace("_", "-")

    root_dir = Path(__file__).parent.parent.resolve()
    pyproject_path = root_dir / "pyproject.toml"

    current_name_dash: str = ""
    if pyproject_path.exists():
        for line in pyproject_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("name = "):
                current_name_dash = line.split("=")[1].strip().strip('"').strip("'")
                break

    if not current_name_dash:
        print("Error: Could not find the current project name in pyproject.toml!")
        sys.exit(1)

    current_name_underscore: str = current_name_dash.replace("-", "_")

    skip_dirs = {".git", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache", "scripts"}

    changed_files_count = 0

    for file_path in root_dir.rglob("*"):
        if not file_path.is_file():
            continue

        if any(part in skip_dirs for part in file_path.parts):
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
            if current_name_underscore in content or current_name_dash in content:
                new_content = content.replace(current_name_underscore, new_name_underscore)
                new_content = new_content.replace(current_name_dash, new_name_dash)

                file_path.write_text(new_content, encoding="utf-8")
                changed_files_count += 1
        except UnicodeDecodeError:
            pass

    src_dir = root_dir / "src" / current_name_underscore
    if src_dir.exists() and src_dir.is_dir():
        src_dir.rename(root_dir / "src" / new_name_underscore)

    print(f"     -> Updated {changed_files_count} files.")
    print(f"     -> Source directory updated to src/{new_name_underscore}")


if __name__ == "__main__":
    main()
