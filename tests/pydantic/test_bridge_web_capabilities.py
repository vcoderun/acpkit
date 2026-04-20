from __future__ import annotations as _annotations

from .support import (
    UTC,
    AcpSessionContext,
    Agent,
    AgentBridgeBuilder,
    Path,
    TestModel,
    WebFetchBridge,
    WebSearchBridge,
    datetime,
)


def _session(tmp_path: Path) -> AcpSessionContext:
    now = datetime.now(UTC)
    return AcpSessionContext(
        session_id="session-1",
        cwd=tmp_path,
        created_at=now,
        updated_at=now,
    )


def _agent() -> Agent[None, str]:
    return Agent(TestModel())


def test_web_search_bridge_builds_capability_and_metadata(tmp_path: Path) -> None:
    bridge = WebSearchBridge(
        local=False,
        search_context_size="high",
        allowed_domains=["example.com"],
        max_uses=2,
    )
    builder = AgentBridgeBuilder(
        session=_session(tmp_path),
        capability_bridges=[bridge],
    )

    contributions = builder.build()

    assert len(contributions.capabilities) == 1
    assert type(contributions.capabilities[0]).__name__ == "WebSearch"
    assert bridge.get_tool_kind("web_search") == "search"
    assert bridge.get_tool_kind("duckduckgo_search") == "search"
    metadata = bridge.get_session_metadata(_session(tmp_path), agent=_agent())
    assert metadata["search_context_size"] == "high"
    assert metadata["allowed_domains"] == ["example.com"]
    assert metadata["max_uses"] == 2


def test_web_fetch_bridge_builds_capability_and_metadata(tmp_path: Path) -> None:
    bridge = WebFetchBridge(
        local=False,
        allowed_domains=["example.com"],
        max_uses=3,
        enable_citations=True,
    )
    builder = AgentBridgeBuilder(
        session=_session(tmp_path),
        capability_bridges=[bridge],
    )

    contributions = builder.build()

    assert len(contributions.capabilities) == 1
    assert type(contributions.capabilities[0]).__name__ == "WebFetch"
    assert bridge.get_tool_kind("web_fetch") == "fetch"
    metadata = bridge.get_session_metadata(_session(tmp_path), agent=_agent())
    assert metadata["allowed_domains"] == ["example.com"]
    assert metadata["max_uses"] == 3
    assert metadata["enable_citations"] is True


def test_web_bridges_support_custom_tool_names_and_location_metadata(
    tmp_path: Path,
) -> None:
    search_bridge = WebSearchBridge(
        local=False,
        user_location={"city": "Istanbul", "country": "TR"},
        tool_names=frozenset({"search_web"}),
    )
    fetch_bridge = WebFetchBridge(
        local=False,
        blocked_domains=["blocked.example"],
        max_content_tokens=512,
        tool_names=frozenset({"fetch_url"}),
    )

    search_metadata = search_bridge.get_session_metadata(
        _session(tmp_path),
        agent=_agent(),
    )
    fetch_metadata = fetch_bridge.get_session_metadata(
        _session(tmp_path),
        agent=_agent(),
    )

    assert search_bridge.get_tool_kind("web_search") is None
    assert search_bridge.get_tool_kind("search_web") == "search"
    assert search_metadata["user_location"] == {"city": "Istanbul", "country": "TR"}
    assert search_metadata["tool_names"] == ["search_web"]
    assert fetch_bridge.get_tool_kind("fetch_url") == "fetch"
    assert fetch_bridge.get_tool_kind("web_fetch") is None
    assert fetch_metadata["blocked_domains"] == ["blocked.example"]
    assert fetch_metadata["max_content_tokens"] == 512
    assert fetch_metadata["tool_names"] == ["fetch_url"]
