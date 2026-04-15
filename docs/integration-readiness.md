# Integration Readiness

Use this checklist before attempting to ACP-wrap a real agent project.

The goal is to reject bad candidates early and avoid forcing ACP onto a surface that has no stable ownership model.

## Ready

An integration is usually ready when most of these are true:

- there is a clear agent boundary to wrap
- model and tool surfaces are explicit
- session-local state already exists or can be made explicit
- host tools are separable from repo or local tools
- approvals can be represented truthfully
- hooks or events are structured enough to project
- replayable session state is possible

## Not Ready

An integration is usually not ready when any of these are true:

- the project has no real agent boundary
- most behavior lives in private helper functions
- tool registration is unstable or implicit
- global mutable state drives core runtime behavior
- there is no coherent notion of session state
- approvals are implicit or side-channel only
- mode or model switching cannot really be honored

## Preflight Questions

### Agent Boundary

- What is the actual object or factory that represents the agent?
- Can it be rebuilt per session if needed?

### State Ownership

- Which state is adapter-owned?
- Which state is host-owned?
- Which state should not be exposed?

### Host Backends

- Should filesystem access stay client-backed?
- Should terminal execution stay client-backed?
- Are command policies already host-owned?

### Session Truthfulness

- Can the integration replay transcript state after reload?
- Can plans, approvals, and config values survive reload coherently?

### Projection Quality

- Do core tool families have meaningful projection candidates?
- Are there domain-specific events worth shaping?

## Go / No-Go Rule

Proceed only if you can answer all of these clearly:

- what the agent boundary is
- who owns each ACP-visible surface
- how session replay should behave
- which host tools should be ACP-visible
- which surfaces should remain hidden

If those answers are still fuzzy, do the audit first and delay implementation.
