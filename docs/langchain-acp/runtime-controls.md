# LangChain ACP Models, Modes, And Config

`langchain-acp` exposes runtime controls only when the host graph really owns
them.

There is no Pydantic-style slash-command surface here. The LangChain adapter is
config-driven:

- models
- modes
- extra config options

## Built-in Controls

Use built-in config when the adapter can own the state directly:

- `available_models`
- `available_modes`
- `default_model_id`
- `default_mode_id`

This is the simplest shape for product code that already knows its supported
models and modes up front.

## Provider-owned Controls

Use providers when the host application already owns the state:

- `SessionModelsProvider`
- `SessionModesProvider`
- `ConfigOptionsProvider`

These providers expose ACP-visible state without moving ownership into the
adapter.

The important distinction is that the provider answers the question, while the
adapter only reflects the answer through ACP.

## Typed Return Objects

Two typed return surfaces carry most of the control state:

### `ModelSelectionState`

Carries:

- `available_models`
- `current_model_id`
- `allow_any_model_id`
- config option naming

### `ModeState`

Carries:

- `modes`
- `current_mode_id`
- config option naming

## A Real Product Pattern

Static built-ins are usually enough for examples. Real products often combine
providers and graph factories:

```python
from langchain.agents import create_agent
from langchain_acp import AdapterConfig, run_acp


def graph_from_session(session):
    model_id = session.session_model_id or "openai:gpt-5-mini"
    mode_id = session.session_mode_id or "default"
    return create_agent(
        model=model_id,
        tools=[],
        name=f"graph-{mode_id}",
        system_prompt=f"Run in {mode_id} mode.",
    )


config = AdapterConfig(
    available_models=[],
)

run_acp(graph_factory=graph_from_session, config=config)
```

The point is to keep the control plane explicit instead of hiding model or mode
switching inside opaque runtime state.

In a provider-backed setup, those built-in lists can stay empty while the
provider remains authoritative.

## Bridge-backed Control Surfaces

The built-in capability bridges give the same behavior through ACP Kit's bridge
architecture:

- `ModelSelectionBridge`
- `ModeSelectionBridge`
- `ConfigOptionsBridge`

Use these when control state belongs in the bridge layer instead of directly in
`AdapterConfig`.

## Example

```python
from acp.schema import ModelInfo, SessionMode
from langchain_acp import AdapterConfig

config = AdapterConfig(
    available_models=[
        ModelInfo(model_id="fast", name="Fast"),
        ModelInfo(model_id="deep", name="Deep"),
    ],
    available_modes=[
        SessionMode(id="ask", name="Ask"),
        SessionMode(id="agent", name="Agent"),
    ],
    default_model_id="fast",
    default_mode_id="ask",
)
```

## What This Does Not Expose

`langchain-acp` does not currently publish a Pydantic-style slash-command layer.

That is intentional:

- LangChain graphs do not share one upstream slash-command contract
- session controls are modeled as ACP config and session state instead

If your product wants slash-command UX, that belongs in the host product layer,
not in the generic LangChain adapter core.
