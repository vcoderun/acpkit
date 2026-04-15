# Integration Audit Guide

Use this guide before writing adapter code for a real agent project.

The point is not to map every existing concept into ACP. The point is to expose a truthful ACP surface with one clear owner for each capability.

## Audit Order

Run the audit in this order:

1. Find the real agent boundary.
2. Identify existing session state.
3. Identify host-owned capabilities.
4. Identify product-owned state that ACP should reflect.
5. Decide what should not be exposed.

If step 1 is unclear, stop there. ACP Kit is strongest when there is a real agent surface to wrap, not a loose pile of helpers.

## Step 1: Find The Real Agent Boundary

Good candidates:

- a stable `pydantic_ai.Agent`
- a session-aware agent factory
- a session-aware agent source

Bad candidates:

- private helper modules
- implicit global runtime state
- hand-wired orchestration code with no stable public boundary
- ad hoc tool registries that change shape unpredictably

Choose the narrowest construction seam that matches the target:

| Situation | Recommended seam |
| --- | --- |
| one existing agent instance is enough | `run_acp(...)` |
| another runtime should own lifecycle | `create_acp_agent(...)` |
| session should influence construction, but full source logic is unnecessary | `agent_factory=` |
| host binding and dependencies are session-owned | `AgentSource` |

## Step 2: Decide Ownership

For each ACP-visible capability, pick one owner:

- adapter-owned
- host-owned via provider
- bridge-owned runtime behavior
- intentionally not exposed

Do not mix owners for the same surface unless you have a very explicit reason.

## Ownership Heuristics

Use adapter-owned state when:

- the downstream app has no existing source of truth
- the state is naturally ACP-native
- the state should persist with ACP sessions

Use providers when:

- the product already owns models, modes, plans, or config options
- ACP should reflect state, not invent it
- the app has policy or storage outside the adapter

Use bridges when:

- the runtime needs ACP-visible behavior without changing the agent core
- the capability is additive
- the behavior is orthogonal to main agent construction

Do not expose the surface when:

- the underlying runtime cannot actually honor it
- there is no stable state to map
- the capability would be misleading in ACP clients

## Step 3: Classify The Host Backends

Ask these questions:

- should filesystem access stay client-backed?
- should terminal execution stay client-backed?
- does the host already own approval and command policy?
- should the client see tool execution as file/bash projections?

If the answer is yes, use `ClientHostContext` and projection maps instead of hiding those operations inside the source agent.

## Step 4: Audit Modes, Plans, And Config

For each one, ask:

- is there already a source of truth?
- is it session-local?
- can the runtime honor switching in real time?
- should it survive session replay?

Typical mapping:

- built-in selection when ACP can safely own it
- provider-owned when the host already owns policy
- bridge-owned when it changes tool visibility or runtime behavior

## Step 5: Decide What To Exclude

A truthful ACP adapter should leave some things out.

Common examples:

- low-signal internal hooks
- unstable internal helper tools
- host behaviors that do not have a clean ACP contract
- state that cannot be replayed or resumed coherently

## Anti-patterns

Do not:

- expose the same state through both built-in ownership and a provider
- advertise tool availability that the runtime cannot actually enforce
- wrap internal helpers instead of the real agent boundary
- project noisy payloads just because they exist
- treat every host behavior as ACP-visible by default

## Audit Output

At the end of the audit, you should be able to write:

- the chosen construction seam
- the owner for models, modes, plans, approvals, config options, and host backends
- the projection families you need
- the surfaces you intentionally will not expose

If you cannot produce that summary, the integration is not ready to implement.
