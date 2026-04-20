# LangChain ACP Projections And Event Projection Maps

`langchain-acp` does not have the same host-backend layer that `pydantic-acp`
uses for ACP client-owned filesystem and terminal access.

Its rendering story is graph- and event-centric instead:

1. tool projection maps
2. event projection maps

The adapter should project only what the graph really produced. If a tool family
has weakly structured output, generic rendering is better than a fake rich card.

## Projection Maps

Projection maps shape tool calls into ACP-visible updates.

Core surfaces:

- `ProjectionMap`
- `FileSystemProjectionMap`
- `CommunityFileManagementProjectionMap`
- `WebSearchProjectionMap`
- `HttpRequestProjectionMap`
- `BrowserProjectionMap`
- `CommandProjectionMap`
- `FinanceProjectionMap`
- `CompositeProjectionMap`
- `DeepAgentsProjectionMap`

Use them for:

- file read previews
- file write diffs
- community file-management toolkits
- search titles and result cards
- HTTP request previews and response summaries
- browser navigation and extraction status
- shell command previews
- finance/news lookup cards
- terminal output rendering

The common `langchain-community` families map cleanly here:

- `WebSearchProjectionMap` for search families such as DuckDuckGo, Brave,
  Serper, Tavily, SearchAPI, Searx, and Jina search tools
- `HttpRequestProjectionMap` for `requests_get`, `requests_post`,
  `requests_patch`, `requests_put`, and `requests_delete`
- `BrowserProjectionMap` for `navigate_browser`, `current_webpage`,
  `extract_text`, `extract_hyperlinks`, `get_elements`, `click_element`, and
  `previous_webpage`
- `CommunityFileManagementProjectionMap` for `read_file`, `write_file`,
  `file_search`, `list_directory`, `copy_file`, `move_file`, and `file_delete`
- `FinanceProjectionMap` for finance and news lookup families

`WebFetchProjectionMap` remains as a backward-compatible alias for
`HttpRequestProjectionMap`, but the intended public name for `requests_*`
families is now `HttpRequestProjectionMap`.

### Example

```python
from langchain_acp import (
    AdapterConfig,
    BrowserProjectionMap,
    CommunityFileManagementProjectionMap,
    DeepAgentsProjectionMap,
    FinanceProjectionMap,
    HttpRequestProjectionMap,
    WebSearchProjectionMap,
)

config = AdapterConfig(
    projection_maps=[
        DeepAgentsProjectionMap(),
        WebSearchProjectionMap(),
        HttpRequestProjectionMap(),
        BrowserProjectionMap(),
        CommunityFileManagementProjectionMap(),
        FinanceProjectionMap(),
    ]
)
```

## Event Projection Maps

LangChain and LangGraph runtimes can also emit callback or event payloads that
do not naturally look like tool calls.

That path is handled separately:

- `EventProjectionMap`
- `StructuredEventProjectionMap`
- `CompositeEventProjectionMap`

This lets the adapter project event payloads into ACP transcript updates such
as:

- `AgentMessageChunk`
- `ToolCallStart`
- `ToolCallProgress`
- `AgentPlanUpdate`
- `SessionInfoUpdate`

## Why These Are Separate

Tool calls and event payloads are not the same contract.

Keeping them separate avoids two bad outcomes:

- overloading tool projection to parse arbitrary callback payloads
- flattening structured events into plain text

## DeepAgents Projection

`DeepAgentsProjectionMap` is the compatibility preset for:

- `read_file`
- `edit_file`
- `write_file`
- `glob`
- `grep`
- `ls`
- `execute`

Use it when a DeepAgents graph should preserve familiar ACP tool rendering
without turning DeepAgents policy into the generic default.

## Example

```python
from langchain_acp import (
    AdapterConfig,
    DeepAgentsProjectionMap,
    StructuredEventProjectionMap,
)

config = AdapterConfig(
    projection_maps=[DeepAgentsProjectionMap()],
    event_projection_maps=[StructuredEventProjectionMap()],
)
```
