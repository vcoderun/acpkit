# Providers

Providers let the host own richer session state while the adapter stays truthful about what it can expose through ACP.

## Available Provider Interfaces

### SessionModelsProvider

Controls:

- available ACP models
- current model id
- optional free-form model ids
- model write-back for `set_session_model(...)`

Key return type:

- `ModelSelectionState`

### SessionModesProvider

Controls:

- available ACP modes
- current mode id
- mode write-back for `set_session_mode(...)`

Key return type:

- `ModeState`

### ConfigOptionsProvider

Controls:

- additional ACP config options
- config write-back for `set_config_option(...)`

Supported option types:

- `SessionConfigOptionSelect`
- `SessionConfigOptionBoolean`

### PlanProvider

Controls:

- ACP `PlanEntry` emission for current session state
- plan updates during session bootstrap and prompt execution

### ApprovalStateProvider

Controls:

- approval metadata surfaced into session metadata

This is distinct from the live approval flow handled by `ApprovalBridge`.

## When To Use Providers

Use providers when the session state already belongs to the host or product layer:

- available models come from host policy
- mode selection is product-defined
- config options are product-specific
- plan state is generated externally
- approval metadata is stored outside the adapter core

## Return Shape

Provider methods may return:

- a concrete value
- `None`
- an awaitable of either

That means sync and async host integrations are both supported.
