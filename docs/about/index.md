# About ACP Kit

ACP Kit exists to turn agent framework APIs into ACP servers without pretending the adapter knows more than the source framework actually exposes.

## Design Goals

- Keep ACP exposure truthful.
- Preserve native framework semantics where the framework already supports them.
- Keep session state inside the adapter instead of leaking product-specific assumptions into the framework.
- Expose optional capabilities through explicit provider and bridge seams.

## Current Implementation

The current workspace implements:

- the root `acpkit` CLI package
- the `pydantic-acp` adapter package
- the `codex-auth-helper` helper package

All seven `pydantic-acp` milestones are currently implemented, including the three host-backend phases in Milestone 7.

Additional adapter packages can live under `packages/adapters/`, and small support packages can live under `packages/helpers/`.

## License

ACP Kit is distributed under the MIT License.
