# Compatibility Matrix Template

Use this template when documenting a real ACP Kit integration.

The goal is to make ownership and scope obvious at a glance.

Use it after the integration already has real seams and at least one black-box proof path.

If the integration still cannot demonstrate session reload, approval behavior, or host-backed side effects truthfully, finish that proof work first and come back to the manifest afterward.

ACP Kit also now ships a typed root-level schema for this:

```python
from acpkit import CompatibilityManifest, SurfaceSupport
```

The intended workflow is code-first, not Markdown-first.

Write the manifest as Python, validate it in tests or CI, and render Markdown from that typed source when you need human-facing documentation.

## Recommended Workflow

1. Build the integration truthfully first.
2. Inventory the ACP surfaces you actually expose.
3. Declare a `CompatibilityManifest` in the integration repo.
4. Run `manifest.validate()` in tests.
5. Optionally publish `manifest.to_markdown()` into docs.

That order matters. The manifest should describe real behavior, not aspirational behavior.

For the proof step, start here:

- [Integration Testing](https://vcoderun.github.io/acpkit/integration-testing/)

Checklist before writing the manifest:

- the integration already has real adapter seams
- at least one black-box proof path exists
- session and host ownership are already decided
- the mapping text can point to real code, not future intent

## Minimal Code Example

```python
from acpkit import CompatibilityManifest, SurfaceSupport

manifest = CompatibilityManifest(
    integration_name='workspace-agent',
    adapter='pydantic-acp',
    surfaces={
        'session.load': SurfaceSupport(
            status='implemented',
            owner='adapter',
            mapping='FileSessionStore + load_session',
        ),
        'mode.switch': SurfaceSupport(
            status='partial',
            owner='bridge',
            mapping='PrepareToolsBridge dynamic modes',
            rationale='Only explicitly exposed runtime modes are surfaced.',
        ),
        'hooks.visible': SurfaceSupport(
            status='intentionally_not_used',
            rationale='The runtime keeps the hook seam but does not expose it to ACP clients.',
        ),
        'authenticate': SurfaceSupport(
            status='planned',
            rationale='No auth handshake has been added yet.',
        ),
    },
)

manifest.validate()
print(manifest.to_markdown())
```

Smallest valid example:

```python
from acpkit import CompatibilityManifest, SurfaceSupport

manifest = CompatibilityManifest(
    integration_name='workspace-agent',
    adapter='pydantic-acp',
    surfaces={
        'session.load': SurfaceSupport(
            status='implemented',
            owner='adapter',
            mapping='FileSessionStore + load_session',
        ),
    },
)

manifest.validate()
```

## How To Generate The Manifest

Do not "generate" it from guesses.

Generate it from an integration audit:

1. Start from real seams:
   - `AgentSource`
   - `AdapterConfig`
   - providers
   - bridges
   - host-backed tools
   - output serializer
2. Walk the ACP surface area one slice at a time.
3. For each slice, decide:
   - implemented
   - partial
   - intentionally not used
   - planned
4. Record:
   - owner
   - exact mapping seam
   - rationale when the state is partial, intentionally not used, or planned

The manifest is not a generated capability probe.

It is a reviewable declaration that should be written by the integrator who understands the ownership model.

## How To Use It

For each ACP surface, mark:

- `implemented`
- `partial`
- `intentionally not used`
- `planned`

Also record:

- owner
- mapping
- rationale

## Validation Rules

The typed manifest currently enforces these rules:

- every manifest needs a non-empty integration name
- every manifest needs a non-empty adapter name
- every manifest needs at least one declared surface
- `implemented` and `partial` surfaces must declare an owner
- `implemented` surfaces must declare a concrete mapping
- `partial`, `intentionally_not_used`, and `planned` surfaces must explain why
- `mixed` owner entries must explain how the split works

These rules are deliberately opinionated. The point is to stop integrations from publishing vague "supported" claims with no ownership or mapping detail.

## Template

| ACP Surface | Status | Owner | Mapping | Rationale |
| --- | --- | --- | --- | --- |
| Agent sourcing |  |  |  |  |
| Session storage |  |  |  |  |
| Model selection |  |  |  |  |
| Modes |  |  |  |  |
| Config options |  |  |  |  |
| Plans |  |  |  |  |
| Approvals |  |  |  |  |
| MCP metadata |  |  |  |  |
| Hooks |  |  |  |  |
| History processing |  |  |  |  |
| Thinking |  |  |  |  |
| File projections |  |  |  |  |
| Command projections |  |  |  |  |
| Host-backed filesystem |  |  |  |  |
| Host-backed terminal |  |  |  |  |
| Output serialization |  |  |  |  |
| End-to-end test coverage |  |  |  |  |

## Suggested Surface Names

Use stable dotted names instead of ad hoc prose labels when possible.

Good examples:

- `session.load`
- `session.fork`
- `session.resume`
- `model.switch`
- `mode.switch`
- `config.options`
- `plan.native`
- `plan.provider`
- `approval.live`
- `approval.state`
- `host.filesystem`
- `host.terminal`
- `projection.filesystem`
- `projection.commands`
- `hooks.visible`
- `thinking.visible`
- `authenticate`

You do not need one universal closed vocabulary on day one, but each integration should use stable surface ids consistently.

## Owner Vocabulary

Use one of these owner labels:

- `adapter`
- `provider`
- `bridge`
- `host`
- `mixed` only when the split is deliberate and documented

Avoid vague owner labels such as "runtime" or "integration layer" unless the exact seam is also named.

## Good Rationale Examples

- implemented through `AgentSource` because the host binds session-local dependencies
- provider-owned because model policy already exists outside the adapter
- intentionally not used because the runtime cannot replay that state truthfully
- planned because the tool family still uses generic fallback projections

## Recommended Placement In An Integration Repo

Prefer a real Python module, for example:

- `my_integration/compatibility.py`
- `docs/compatibility.py` if the docs build imports it intentionally

Then add a test such as:

```python
from my_integration.compatibility import manifest


def test_manifest_is_valid() -> None:
    manifest.validate()
```

That is the minimum quality bar.

## Review Rule

Every `intentionally not used` entry should explain why.

Every `mixed` owner entry should explain how conflicts are prevented.

Every `implemented` entry should point to a real mapping seam, not a vague claim.
