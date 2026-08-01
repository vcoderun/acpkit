# Versioning and Stability

ACP Kit uses Semantic Versioning and publishes all workspace packages with one
synchronized version:

- `acpkit`
- `pydantic-acp`
- `langchain-acp`
- `codex-auth-helper`
- `acpremote`

## Stable v1 Status

`1.0.0` is the first stable release and establishes the public v1 compatibility
contract.

```bash
uv add "acpkit[all]>=1.0.0,<2.0.0"
```

```bash
pip install "acpkit[all]>=1.0.0,<2.0.0"
```

## Public API Contract

Starting with `1.0.0`, the following surfaces follow Semantic Versioning:

- names exported by a package's top-level `__all__`
- documented CLI commands, options, and exit behavior
- documented configuration fields, provider protocols, bridge protocols, and
  projection contracts
- persisted session formats where the documentation promises compatibility

Private modules, underscore-prefixed names, test helpers outside documented
testing packages, and implementation details are not public API.

## Compatibility Policy

Framework compatibility is bounded independently from ACP Kit's package
version. The supported ranges are declared in package metadata and exercised by
the release matrix. A future upstream version is not supported merely because
dependency resolution accepts it.

The v1 baseline is:

| Integration | Supported baseline |
|---|---|
| Python | 3.11, 3.12, 3.13 |
| Pydantic AI | 2.9.0 through 2.22.0 |
| LangChain | 1.3.11 |
| LangGraph | 1.2.7 |
| DeepAgents | 0.6.12 |
| ACP Python SDK | 0.11.0 |

## Deprecation Policy

Public v1 APIs are deprecated before removal whenever security or correctness
does not require an immediate break. Deprecations include a replacement path
and remain available for at least one minor release. Incompatible public API
removals are reserved for the next major release.
