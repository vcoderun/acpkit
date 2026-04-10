# Provider-backed Session State

[`examples/pydantic/providers.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/providers.py) shows the cleanest way to keep product-owned state out of the adapter core.

It provides:

- models
- modes
- config options
- plan state
- approval metadata

All of that state is read from a host-owned provider object keyed by `session_id`.

## Why This Example Matters

Use it when:

- your application already stores session state elsewhere
- ACP should reflect that state, not own it
- you want a thin adapter boundary with explicit ownership

## Related Example: ApprovalRequired

If you want the live approval flow rather than only approval metadata, pair this with [`examples/pydantic/approvals.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/approvals.py).

That example demonstrates:

- `ApprovalRequired`
- `NativeApprovalBridge`
- remembered approval choices

Together, `providers.py` and `approvals.py` cover most host-owned session state patterns.
