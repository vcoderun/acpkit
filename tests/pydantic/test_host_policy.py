from __future__ import annotations as _annotations

import asyncio

import pytest
from pydantic_acp import HostAccessPolicy
from pydantic_acp.host._policy_commands import extract_command_path_candidates, resolve_command_cwd

from .support import (
    UTC,
    AcpSessionContext,
    ClientHostContext,
    FilesystemRecordingClient,
    Path,
    TerminalRecordingClient,
    datetime,
)


def test_host_access_policy_warns_for_absolute_path_inside_workspace() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()
    policy = HostAccessPolicy()

    evaluation = policy.evaluate_path(
        session_cwd / "notes.txt",
        session_cwd=session_cwd,
        workspace_root=session_cwd,
    )

    assert evaluation.disposition == "warn"
    assert evaluation.is_absolute_input is True
    assert evaluation.outside_cwd is False
    assert evaluation.outside_workspace is False
    assert evaluation.should_warn is True
    assert evaluation.should_deny is False
    assert evaluation.headline == "Host access requires review"
    assert evaluation.recommendation == "Review the target carefully before continuing."
    assert evaluation.risk_codes == ("absolute_path",)
    assert "Path is absolute" in evaluation.message


def test_host_access_policy_denies_path_outside_workspace() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()
    policy = HostAccessPolicy()

    evaluation = policy.evaluate_path(
        "../outside.txt",
        session_cwd=session_cwd,
        workspace_root=session_cwd,
    )

    assert evaluation.disposition == "deny"
    assert evaluation.outside_cwd is True
    assert evaluation.outside_workspace is True
    assert "Path escapes the workspace root" in evaluation.message


def test_host_access_policy_warns_for_command_target_outside_cwd_without_workspace_root() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()
    policy = HostAccessPolicy()

    evaluation = policy.evaluate_command(
        "python",
        args=["../tools/run.py"],
        session_cwd=session_cwd,
    )

    assert evaluation.disposition == "warn"
    assert evaluation.references_external_paths is True
    assert evaluation.references_outside_workspace is False
    assert evaluation.primary_risk is not None
    assert evaluation.summary_lines()[0] == "Host access requires review"
    assert "outside the active session cwd" in evaluation.message


def test_host_access_policy_strict_preset_denies_external_command_paths() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()
    policy = HostAccessPolicy.strict()

    evaluation = policy.evaluate_command(
        "python",
        args=["../tools/run.py"],
        session_cwd=session_cwd,
    )

    assert evaluation.disposition == "deny"
    assert evaluation.should_deny is True
    assert evaluation.headline == "Host access denied"
    assert evaluation.recommendation == "Do not continue unless you intentionally relax the policy."


def test_host_access_policy_permissive_preset_warns_for_outside_workspace() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()
    policy = HostAccessPolicy.permissive()

    evaluation = policy.evaluate_path(
        "../outside.txt",
        session_cwd=session_cwd,
        workspace_root=session_cwd,
    )

    assert evaluation.disposition == "warn"
    assert evaluation.should_warn is True
    assert evaluation.headline == "Host access requires review"


def test_host_access_policy_allows_safe_relative_paths_and_commands() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()
    policy = HostAccessPolicy()

    path_evaluation = policy.enforce_path(
        "notes.txt",
        session_cwd=session_cwd,
        workspace_root=session_cwd,
    )
    command_evaluation = policy.enforce_command(
        "python",
        args=["./script.py"],
        session_cwd=session_cwd,
        workspace_root=session_cwd,
    )

    assert path_evaluation.disposition == "allow"
    assert path_evaluation.has_risks is False
    assert path_evaluation.primary_risk is None
    assert path_evaluation.message == "Host access allowed."
    assert path_evaluation.summary_lines() == (
        "Host access allowed",
        "Host access allowed.",
        "Safe to continue.",
    )
    assert command_evaluation.disposition == "allow"
    assert command_evaluation.has_risks is False
    assert command_evaluation.primary_risk is None
    assert command_evaluation.message == "Host access allowed."
    assert command_evaluation.referenced_paths == (
        (session_cwd / "script.py").resolve(strict=False),
    )
    assert command_evaluation.summary_lines() == (
        "Host access allowed",
        "Host access allowed.",
        "Safe to continue.",
    )


def test_host_access_policy_warns_for_command_cwd_outside_workspace() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()
    policy = HostAccessPolicy.permissive()

    evaluation = policy.evaluate_command(
        "python",
        cwd="../outside",
        session_cwd=session_cwd,
        workspace_root=session_cwd,
    )

    assert evaluation.disposition == "warn"
    assert evaluation.should_warn is True
    assert evaluation.outside_cwd is True
    assert evaluation.outside_workspace is True
    assert evaluation.risk_codes == (
        "command_cwd_outside_cwd",
        "command_cwd_outside_workspace",
    )
    assert "Command cwd escapes the workspace root" in evaluation.message


def test_command_path_helpers_extract_relative_flag_and_assignment_tokens() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()

    assert resolve_command_cwd(None, session_cwd=session_cwd) == session_cwd
    assert resolve_command_cwd("../runner", session_cwd=session_cwd) == (
        session_cwd / "../runner"
    ).resolve(strict=False)
    assert extract_command_path_candidates(
        "./bin/run.py",
        args=[
            "--output=../dist/result.txt",
            "cwd=..",
            "./bin/run.py",
            ".",
            "..",
            "--name=value",
            "name=value",
            "",
            "--flag",
            "not-a-path",
        ],
        command_cwd=session_cwd,
    ) == (
        (session_cwd / "bin/run.py").resolve(strict=False),
        (session_cwd / "../dist/result.txt").resolve(strict=False),
        session_cwd.parent.resolve(strict=False),
        session_cwd,
    )


def test_client_filesystem_backend_enforces_denying_policy() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()
    session = AcpSessionContext(
        session_id="session-host-policy-filesystem",
        cwd=session_cwd,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = FilesystemRecordingClient()
    host = ClientHostContext.from_session(
        client=client,
        session=session,
        access_policy=HostAccessPolicy(),
        workspace_root=session_cwd,
    )

    with pytest.raises(PermissionError, match="workspace root"):
        asyncio.run(host.filesystem.write_text_file("../outside.txt", "blocked"))

    assert client.write_calls == []


def test_client_terminal_backend_enforces_denying_policy() -> None:
    session_cwd = Path("/tmp/acpkit-host-policy").resolve()
    session = AcpSessionContext(
        session_id="session-host-policy-terminal",
        cwd=session_cwd,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = TerminalRecordingClient()
    host = ClientHostContext.from_session(
        client=client,
        session=session,
        access_policy=HostAccessPolicy(),
        workspace_root=session_cwd,
    )

    with pytest.raises(PermissionError, match="workspace root"):
        asyncio.run(
            host.terminal.create_terminal(
                "python",
                args=["/tmp/outside.py"],
            )
        )

    assert client.create_calls == []
