---
name: "acpremote"
description: "Use for `acpremote` tasks: exposing ACP agents or stdio ACP commands over WebSocket, mirroring remote ACP endpoints locally, host ownership semantics, metadata/auth routes, and remote transport examples."
---

# acpremote Skill

Use this skill when the task is about the `acpremote` transport package.

This package is the repo's ACP transport/helper layer. It assumes the runtime already speaks ACP
and focuses on moving that ACP boundary across WebSocket transport or back into a local ACP proxy.

It is not an adapter.

That distinction matters:

- adapters turn framework runtimes into ACP
- `acpremote` transports or mirrors an ACP boundary that already exists

## Start Here

If you only need the shortest high-signal path:

1. read `Quick Routing`
2. open `server.py` for exposure-path questions
3. open `client.py` and `proxy_agent.py` for mirror-path questions
4. open `command.py` only when the upstream runtime is ACP-over-stdio

## Quick Routing

| If the task is about... | Use this skill? | Open first |
| --- | --- | --- |
| expose an existing ACP agent over WebSocket | Yes | `server.py`, `config.py` |
| expose a stdio ACP command over WebSocket | Yes | `command.py`, `server.py` |
| mirror a remote ACP endpoint locally | Yes | `client.py`, `proxy_agent.py` |
| bearer auth or metadata routes | Yes | `auth.py`, `metadata.py`, `server.py` |
| remote host ownership or `remote_cwd` | Yes | `proxy_agent.py`, `client.py`, `metadata.py` |
| line buffering / frame relay issues | Yes | `stream.py` |
| adapting a Pydantic or LangChain runtime to ACP | No, pair with adapter skill | adapter packages |

## Package Boundary

`acpremote` owns:

- WebSocket transport
- stdio-to-WebSocket ACP relaying
- remote mirror proxy behavior
- `/acp` metadata
- `/healthz`
- bearer-token protection
- transport latency metadata/projection
- host-ownership policy for mirrored clients

It does not own:

- adapting `pydantic_ai.Agent`
- adapting LangGraph/LangChain graphs
- root CLI target loading
- Codex auth parsing

## Do Not Confuse With

- `acpremote` vs `acpkit-sdk`
  `acpremote` is transport; `acpkit` is the root CLI/runtime package
- `acpremote` vs `pydantic-acp`
  `acpremote` exposes or mirrors ACP; `pydantic-acp` creates ACP from a Pydantic runtime
- `acpremote` vs `langchain-acp`
  `acpremote` transports ACP; `langchain-acp` adapts graph runtimes into ACP

## Primary References

Package references:

- Raw skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/acpremote/SKILL.md`
- Raw transport docs:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/acpremote.md`
- Raw remote-host docs:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/examples/remote-hosting.md`
- Rendered docs:
  `https://vcoderun.github.io/acpkit/acpremote/`
- Source tree:
  `https://github.com/vcoderun/acpkit/tree/main/packages/transports/acpremote`

Cross-skill references:

- Root package skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/acpkit-sdk/SKILL.md`
- Pydantic adapter skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/pydantic-acp/SKILL.md`
- LangChain adapter skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/langchain-acp/SKILL.md`

## Public Surface

Server-side seams:

- `serve_acp(...)`
- `serve_command(...)`
- `serve_stdio_command(...)`

Client-side seam:

- `connect_acp(...)`

Support types:

- `TransportOptions`
- `CommandOptions`
- `ServerOptions`

Package entrypoint:

- `https://github.com/vcoderun/acpkit/blob/main/packages/transports/acpremote/src/acpremote/__init__.py`

## Module Guide

| Subsystem | Key files | Use them for |
| --- | --- | --- |
| server and routing | `server.py`, `auth.py`, `metadata.py`, `config.py`, `limits.py` | server startup, mount paths, metadata, health, auth, limits |
| remote client and proxy behavior | `client.py`, `proxy_agent.py` | remote connection setup, metadata fetch, local mirroring, host ownership |
| command-backed transport | `command.py` | stdio ACP commands that need WebSocket exposure |
| stream plumbing | `stream.py` | line buffering, text/binary frame handling, sender/receiver lifecycle |

## Core Transport Shapes

### Shape 1: Existing ACP agent object

Use:

- `serve_acp(...)`

Meaning:

1. some runtime already produced an `acp.interfaces.Agent`
2. `acpremote` exposes that ACP boundary over WebSocket

### Shape 2: Existing stdio ACP command

Use:

- `serve_command(...)`
- `serve_stdio_command(...)`

Meaning:

1. the upstream runtime only exposes ACP on stdin/stdout
2. `acpremote` spawns that command
3. stdin/stdout ACP frames are bridged onto WebSocket

### Shape 3: Remote ACP mirrored locally

Use:

- `connect_acp(...)`

Meaning:

1. there is an existing remote ACP WebSocket endpoint
2. `acpremote` opens a remote connection
3. the local machine gets a proxy ACP agent that mirrors the remote endpoint

## Remote Host Ownership

This is one of the most important rules in the package.

The default should keep the remote host authoritative for:

- `cwd`
- host-backed filesystem ownership
- host-backed terminal ownership

That is why the proxy layer:

- prefers remote metadata like `remote_cwd`
- strips local host-backed capabilities before forwarding `initialize(...)`

Use client passthrough only when the product explicitly wants the local machine to become host
owner.

If a user reports:

- remote command executed in local cwd
- local filesystem used instead of remote filesystem
- remote agent owning the wrong host

inspect `proxy_agent.py` and metadata flow first.

## Metadata and Health Surface

The transport package also owns the lightweight HTTP surface around the WebSocket endpoint:

- `/acp`
- `/healthz`

Typical uses:

- health checks
- transport metadata inspection
- remote `cwd` discovery
- debugging auth and route configuration

This is transport-level behavior. Do not push it into adapter code.

## Latency and Transport Visibility

`acpremote` can emit transport-observed latency through:

- metadata fields
- optional visible latency projection

Keep the distinction explicit:

- this is proxy-observed timing
- it is not a claim of clock-synchronized one-way network truth

## Skill-Bundled Examples

Skill-local examples:

- `https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpremote/examples/serve_command.py`
- `https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpremote/examples/mirror_remote.py`
- `https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpremote/examples/README.md`

These demonstrate:

- exposing a stdio ACP command remotely
- mirroring a remote ACP endpoint locally
- using `TransportOptions` intentionally

## Handoff Rules

Pair or switch to:

- `acpkit-sdk`
  when the remote host is reached through `acpkit serve ...` or the local mirror is
  `acpkit run --addr ...`
- `pydantic-acp`
  when a Pydantic agent is adapted first, then exposed remotely
- `langchain-acp`
  when a LangGraph or DeepAgents graph is adapted first, then exposed remotely

Common end-to-end references:

- root recipe index:
  `https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpkit-sdk/examples/README.md`
- public Pydantic example:
  `https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/finance_agent.py`
- public LangChain example:
  `https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py`

## Guardrails

- Do not call `acpremote` an adapter.
- Do not claim it can adapt a framework runtime by itself.
- Do not blur ACP adaptation with ACP transport.
- If the task is really about `acpkit run ...` or `acpkit serve ...`, pair this skill with
  `acpkit-sdk`.
