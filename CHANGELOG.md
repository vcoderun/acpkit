# Changelog

ACP Kit uses synchronized versions for `acpkit`, `pydantic-acp`, `langchain-acp`,
`codex-auth-helper`, and `acpremote`.

## [Unreleased]

## [1.5.1] - 2026-07-24

### Added

- `pydantic-acp`'s `AcpProvider` recovers from an `auth_required` (`-32000`)
  rejection of `session/new` by running the ACP `authenticate` flow — using an
  advertised agent-managed method or an explicit `auth_method_id=` — and
  retrying session creation once. Environment-variable and terminal methods
  remain client-owned because they require credential injection or an
  interactive process.
- Public `AcpProvider.ensure_session()` and `AcpProvider.set_session_mode()`
  bootstrap a session and select a session mode without sending a prompt turn,
  so callers no longer reach into the private `_ensure_session`.
- Opt-in `AcpProvider(raise_on_empty_turn=True)` raises
  `UnexpectedModelBehavior` with an ACP-specific diagnostic when a text-output
  turn yields no visible text. The default remains `False`, preserving the
  empty-response contract.

### Fixed

- `pydantic-acp`'s `request_prompt` propagates the ACP agent's real error
  (rate limit, auth rejection, upstream API failure) by unwrapping single-child
  anyio TaskGroup exception wrappers and dropping matching TaskGroup
  `__context__` noise, while preserving unrelated aggregate errors and
  cancellation.

## [1.5.0] - 2026-07-24

### Changed

- `pydantic-acp` now supports `pydantic-ai-slim>=2.9.0,<=2.16.0`. The runtime
  and type-check matrix covers every supported Pydantic AI minor from 2.9.0
  through 2.16.0; pre-2.9.0 matrix entries are no longer exercised.
- Development and CI now use `pydantic-ai-harness[code-mode]==0.10.0`.
- GitHub Actions workflows use `actions/setup-python@v7`.

## [1.4.0] - 2026-07-15

### Changed

- All ACP-facing packages now require `agent-client-protocol==0.11.0` and use
  its public Python SDK contracts.
- `pydantic-acp` and `langchain-acp` expose adapter-owned `AdapterModel`
  values instead of the removed SDK `ModelInfo` surface. Model selection is
  negotiated through the standard selectable `"model"` session config option,
  not the removed wire-level `session/set_model` request.
- Plan emission defaults to complete `AgentPlanUpdate` messages. When
  `AdapterConfig(plan_update_mode="content")` is selected, adapters emit
  content/removal deltas only after the client advertises the ACP `plan`
  capability; otherwise they retain complete-update behavior.
- Session lifecycle now persists ACP 0.11 `additional_directories` and
  `AcpMcpServer` descriptors across new, load, fork, and resume flows.

### Added

- Typed form and URL elicitation forwarding through `AcpSessionContext`, with
  explicit client capability checks and ACP host delegation support for the
  Pydantic provider bridge.
- `AcpMcpServer` descriptors appear in persisted session state and
  `/mcp-servers` inspection output. They remain host-owned descriptors: ACP
  Kit does not claim ACP-MCP transport support without a public SDK router.
- `acpremote` mirrors the ACP 0.11 lifecycle, config-option model selection,
  elicitation callbacks, descriptor types, and protocol-correct client method
  signatures.
- Updated Pydantic and LangChain examples, package READMEs, guides, and agent
  skills covering config-option selection, capability-gated plans, additional
  directories, typed elicitation, and the ACP-MCP ownership boundary.

### Fixed

- Client filesystem, terminal, permission, and prompt delegation now use the
  ACP 0.11 positional contract, preventing session identifiers from being
  interpreted as file paths in direct SDK callers.
- The maintained test harness accepts ACP 0.11 MCP descriptor variants, and
  full repository coverage validates all public migration paths.

## [1.3.0] - 2026-07-15

### Changed

- `pydantic-acp` now supports `pydantic-ai-slim>=2.0.0,<=2.9.1`; the runtime
  and type-check matrix covers every supported Pydantic AI 2.x minor through
  2.9.1.
- Development and CI now use `pydantic-ai-harness[code-mode]==0.7.0`.

### Fixed

- Harness bridge coverage now constructs real `FileSystem`, `Shell`, and
  `CodeMode` capabilities, guarding the public upstream imports and constructor
  contracts used by ACP Kit.

## [1.2.0] - 2026-07-09

### Added

- `pydantic-acp` now exposes `SessionMcpBridge`, allowing ACP
  `session/new.mcpServers` payloads to become real Pydantic AI MCP toolsets
  instead of only appearing in `/mcp-servers` session metadata.
- Pydantic examples and docs now include a session MCP agent showing
  client-provided stdio, streamable HTTP, and SSE MCP server wiring.

### Fixed

- ACP-backed Pydantic model responses now tolerate prompt response and
  `session_update` notification ordering differences, avoiding empty text
  responses from stdio ACP command agents.
- Session MCP serialization now preserves stdio environment variables and
  HTTP/SSE headers for connection setup while documenting secret-safe metadata
  behavior.

## [1.1.1] - 2026-07-09

### Added

- `pydantic-acp` now exposes `create_acp_model(...)`, allowing ACP agents to be
  consumed as Pydantic AI models through the public factory surface.
- `create_acp_model(acp_command=...)` can launch a local stdio ACP command and
  use it as an ACP-backed Pydantic AI model, enabling external ACP agent servers
  to participate in Pydantic AI orchestration.

### Fixed

- ACP-backed model tests now validate command transport and cleanup at the model
  boundary instead of depending on Pydantic AI output retry formatting details.
- Coverage metadata now reflects full line and branch coverage for the ACP
  provider and stdio command bridge paths.

## [1.1.0] - 2026-07-07

### Added

- Pydantic AI v2 ACP client provider bridge (`AcpProvider`, `AcpModel`,
  `AcpHostBridge`) in `pydantic-acp`, enabling ACP agents to be consumed as
  Pydantic AI providers with model profiles, streaming, tool calls, approvals,
  sessions, and projections.
- Public export of the provider bridge surface from `pydantic_acp`.

### Changed

- `pydantic-acp` now requires Pydantic AI v2.
- Dev dependency aligned with Pydantic AI v2.
- `AcpProvider` construction now uses the explicit `acp_agent=` keyword for
  ACP agent instances.
- `provider.model()` now leaves ACP model selection to the wrapped agent's
  session default; pass a concrete model name only when the ACP agent accepts
  that `session/set_model` id.
- Maintained examples now write generated workspaces and local session state
  under `agent_demos/`, with `.gitignore` ignoring that single runtime output
  directory.
- Workspace packages synchronized to `1.1.0`.

### Fixed

- Type-safety and forward-reference handling in the ACP provider bridge.
- Default ACP provider models no longer send `session/set_model("agent")` to
  wrapped ACP agents that reject `"agent"` as an explicit remote model id.
- `AcpProvider.model(history_mode=...)` no longer mutates the provider-wide
  default history mode for other model instances created from the same
  provider.

## [1.0.0] - 2026-07-03

This is the first stable release of the synchronized ACP Kit workspace.

### Added

- Pydantic AI 2.0 through 2.4 compatibility, including agent capabilities,
  harness-backed filesystem, shell, and code-mode bridges, prompt resources,
  approvals, model and mode controls, native plans, hooks, and projections.
- LangChain 1.3, LangGraph 1.2, and DeepAgents 0.6 integration with graph
  factories, session persistence, approvals, native plans, capability bridges,
  structured event projection, and tool projection maps.
- `acpremote` SDK and CLI support for exposing ACP agents or stdio commands over
  WebSocket and mirroring remote ACP endpoints locally.
- `codex-auth-helper` factories for Pydantic AI Responses models and LangChain
  chat models using local Codex authentication.
- Explicit public API inventories through package-level `__all__` declarations.

### Changed

- All workspace packages now report `1.0.0` and use production/stable package
  metadata.
- Root extras require matching v1 adapter, helper, and transport packages.
- Release validation now checks synchronized versions, tag alignment, changelog
  coverage, built artifact metadata, and clean-environment installation.
- Maintained examples and skills now document production boundaries, persistent
  sessions, workspace confinement, authentication, and real-model configuration.

### Fixed

- LangChain cancellation now interrupts blocked graph streams while preserving
  normal external task cancellation.
- LangChain session reload preserves existing MCP servers when the caller omits
  replacement state.
- Streamed LangChain tool calls wait for complete JSON arguments before emitting
  a truthful tool-call start.
- Pydantic session titles are emitted consistently after the first prompt.
- Codex token refresh is serialized across sync and async callers, and Responses
  models always disable server-side storage.
- Remote metadata fetches are bounded and WebSocket, ACP stream, reconnect, and
  failure paths close every owned resource deterministically.
- CLI target import failures retain actionable root-cause details.

[1.3.0]: https://github.com/vcoderun/acpkit/releases/tag/v1.3.0
[1.4.0]: https://github.com/vcoderun/acpkit/releases/tag/v1.4.0
[1.2.0]: https://github.com/vcoderun/acpkit/releases/tag/v1.2.0
[1.1.1]: https://github.com/vcoderun/acpkit/releases/tag/v1.1.1
[1.1.0]: https://github.com/vcoderun/acpkit/releases/tag/v1.1.0
[1.0.0]: https://github.com/vcoderun/acpkit/releases/tag/v1.0.0
