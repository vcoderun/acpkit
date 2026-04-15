from __future__ import annotations as _annotations

import pytest

from acpkit import CompatibilityManifest, SurfaceSupport


def test_compatibility_manifest_validates_and_renders_markdown() -> None:
    manifest = CompatibilityManifest(
        integration_name="workspace-agent",
        adapter="pydantic-acp",
        surfaces={
            "session.load": SurfaceSupport(
                status="implemented",
                owner="adapter",
                mapping="FileSessionStore + load_session",
            ),
            "mode.switch": SurfaceSupport(
                status="partial",
                owner="bridge",
                mapping="PrepareToolsBridge dynamic modes",
                rationale="Only explicitly exposed runtime modes are surfaced.",
            ),
            "authenticate": SurfaceSupport(
                status="planned",
                rationale="No auth handshake has been added yet.",
            ),
        },
    )

    manifest.validate()
    markdown = manifest.to_markdown()

    assert "# Compatibility Manifest: workspace-agent" in markdown
    assert (
        "| session.load | implemented | adapter | FileSessionStore + load_session |  |" in markdown
    )
    assert "| authenticate | planned |  |  | No auth handshake has been added yet. |" in markdown


def test_compatibility_manifest_requires_owner_for_implemented_surface() -> None:
    manifest = CompatibilityManifest(
        integration_name="workspace-agent",
        adapter="pydantic-acp",
        surfaces={
            "session.load": SurfaceSupport(
                status="implemented",
                mapping="FileSessionStore + load_session",
            ),
        },
    )

    with pytest.raises(ValueError, match="must declare an owner"):
        manifest.validate()


def test_compatibility_manifest_requires_mapping_for_implemented_surface() -> None:
    manifest = CompatibilityManifest(
        integration_name="workspace-agent",
        adapter="pydantic-acp",
        surfaces={
            "session.load": SurfaceSupport(
                status="implemented",
                owner="adapter",
            ),
        },
    )

    with pytest.raises(ValueError, match="must declare a mapping"):
        manifest.validate()


def test_compatibility_manifest_requires_rationale_for_intentionally_unused_surface() -> None:
    manifest = CompatibilityManifest(
        integration_name="workspace-agent",
        adapter="pydantic-acp",
        surfaces={
            "hooks.visible": SurfaceSupport(
                status="intentionally_not_used",
            ),
        },
    )

    with pytest.raises(ValueError, match="must declare a rationale"):
        manifest.validate()


def test_compatibility_manifest_requires_rationale_for_mixed_owner() -> None:
    manifest = CompatibilityManifest(
        integration_name="workspace-agent",
        adapter="pydantic-acp",
        surfaces={
            "model.switch": SurfaceSupport(
                status="partial",
                owner="mixed",
                mapping="Built-in selection plus provider override",
                rationale="",
            ),
        },
    )

    with pytest.raises(ValueError, match="must declare a rationale"):
        manifest.validate()
