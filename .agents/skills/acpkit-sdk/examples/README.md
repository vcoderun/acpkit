# acpkit-sdk Examples

These are skill-local operator recipes for the root `acpkit` package.

## Run Adapter-Backed Targets

Pydantic AI:

```bash
acpkit run examples.pydantic.finance_agent:acp_agent
acpkit run examples.pydantic.travel_agent:acp_agent
```

LangChain / LangGraph:

```bash
acpkit run examples.langchain.workspace_graph:acp_agent
acpkit run examples.langchain.deepagents_graph:acp_agent
```

## Serve A Remote ACP Host

Pydantic AI:

```bash
acpkit serve examples.pydantic.finance_agent:acp_agent --host 0.0.0.0 --port 8080
```

LangChain / LangGraph:

```bash
acpkit serve examples.langchain.workspace_graph:acp_agent --host 0.0.0.0 --port 8080
```

Mirror the remote host back into a local ACP boundary:

```bash
acpkit run --addr ws://127.0.0.1:8080/acp/ws
```

Add `--unstable-protocol` when the upstream agent sends ACP 0.11 unstable
client requests such as elicitation.

## Launch Through Toad

```bash
acpkit launch examples.pydantic.finance_agent:acp_agent
acpkit launch examples.langchain.workspace_graph:acp_agent
```

Maintained examples write generated workspaces and local session state under `agent_demos/`.

If a script already starts ACP by itself:

```bash
acpkit launch --command "python3.11 some_script_that_starts_acp.py"
```
