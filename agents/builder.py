# Build agent from config
# agents/builder.py

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, AgentType, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from pydantic import ValidationError
from config.schema import AgentConfig
from agents.tools.registry import get_tools_by_names
from agents.memory import MemoryBackend, get_history_loader
import hashlib
import time

# Simple in-process cache for built executors to avoid per-run rebuild cost
_EXECUTOR_CACHE = {}
_EXECUTOR_CACHE_MAX = int(os.getenv("AGENT_EXECUTOR_CACHE_MAX", "24"))

def _cache_key(cfg: AgentConfig, api_key: str) -> str:
    parts = [
        cfg.model_name or "",
        cfg.system_message or "",
        ",".join(sorted(cfg.tools or [])),
        str(bool(cfg.memory_enabled)),
        (cfg.memory_backend.value if isinstance(cfg.memory_backend, MemoryBackend) else str(cfg.memory_backend)),
        str(cfg.memory_max_messages or ""),
        (cfg.agent_type.value if hasattr(cfg.agent_type, "value") else str(cfg.agent_type)),
        str(cfg.max_iterations or ""),
        str(cfg.max_execution_time or ""),
    ]
    key_suffix = hashlib.sha1((api_key or "").encode("utf-8")).hexdigest()[:8]
    raw = "|".join(parts) + "|" + key_suffix
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

load_dotenv()


def build_agent(config: AgentConfig, agent_id: str | None = None):
    """Construct a LangChain agent executor from the provided configuration."""

    # 1. Initialize LLM with provided or environment API key
    api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OpenAI API key not provided. Set OPENAI_API_KEY env var or include openai_api_key in config."
        )
    try:
        # langchain_openai.ChatOpenAI expects `model` and `api_key`
        # Apply a reasonable HTTP timeout and fewer retries to avoid long hangs
        timeout = float(os.getenv("OPENAI_TIMEOUT", "12"))
        max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "0"))
        llm = ChatOpenAI(
            model=config.model_name,
            temperature=0,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
    except ValidationError as exc:
        # Surface validation issues clearly (e.g., bad field names)
        raise ValueError(str(exc)) from exc
    except TypeError as exc:
        # Backward-compat or signature mismatch
        raise ValueError(
            "Failed to initialize OpenAI chat model. Ensure `model` is valid (e.g., 'gpt-4o-mini') "
            "and the OpenAI API key is set."
        ) from exc

    # 2. Gather tools from registry
    tools = get_tools_by_names(config.tools, agent_id=agent_id)

    # 2b. Light guidance to encourage tool usage (prevents model refusals)
    tool_names = [getattr(t, "name", "") for t in tools]
    has_gmail = any(n.startswith("gmail") for n in tool_names)
    maps_names = {"google_maps", "maps_geocode", "maps_directions", "maps_distance_matrix"}
    has_maps = any(n in maps_names for n in tool_names)
    calendar_names = {
        "calendar",
        "google_calendar",
        "create_calendar_event",
        "list_calendar_events",
        "get_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        "search_calendar_events",
        "get_free_busy",
        "list_calendars",
    }
    has_calendar = any(n in calendar_names for n in tool_names)
    docs_names = {"google_docs", "docs_create", "docs_get", "docs_append", "docs_export_pdf"}
    has_docs = any(n in docs_names for n in tool_names)
    extra_guidance = []
    if tools:
        extra_guidance.append(
            "You can and should use the available tools when they help answer the user's request."
        )
        extra_guidance.append(
            "If the user explicitly requests a specific tool by name, prioritize using that tool."
        )
    if has_gmail:
        extra_guidance.append(
            "Use the unified `gmail` tool with action = read | search | send."
        )
        extra_guidance.append(
            "When the user asks to read inbox or emails, call `gmail_read_messages` with an appropriate query (defaults to in:inbox is:unread)."
        )
        extra_guidance.append(
            "When the user asks to summarize emails, fetch messages (via `gmail_read_messages` or `gmail` action=read) and summarize in your reply."
        )
        extra_guidance.append(
            "When the user asks to send an email, call `gmail_send_message`."
        )
        extra_guidance.append(
            "Never send an email unless the user explicitly asks you to send one. Do not use send for read/search/summarization tasks."
        )
        extra_guidance.append(
            "To fetch a specific email by message ID, call `gmail_get_message` or use the unified `gmail` tool with action = get."
        )
        extra_guidance.append(
            "If a Gmail tool returns an error (e.g., missing authorization), report the error text to the user instead of refusing."
        )
        extra_guidance.append(
            "Assume the user has given consent to access their Gmail when they ask for these actions."
        )
        extra_guidance.append(
            "Do not say you cannot access email; instead, attempt to use the Gmail tools and return their results."
        )
        extra_guidance.append(
            "After using any tool, always provide a final response to the user in natural language summarizing what you did and the outcome. Do not return raw tool JSON as your final answer."
        )
    if has_calendar:
        extra_guidance.append(
            "Use the `calendar` tool when the user asks about Google Calendar events."
        )
        extra_guidance.append(
            "For listing or searching events, call `calendar` with action = list or search; for creating events use action = create."
        )
        extra_guidance.append(
            "Do not use Gmail tools for calendar requests; use the Calendar tools instead."
        )
    if has_docs:
        extra_guidance.append(
            "Use the `google_docs` tool for creating, reading, appending, and exporting Google Docs."
        )
        extra_guidance.append(
            "Prefer the unified `google_docs` tool with action = create|get|append|export (e.g., export to PDF)."
        )
        extra_guidance.append(
            "When the user asks to add content to a new or next page, call `google_docs` with action=append and set `new_page=true` to insert a page break before the content."
        )
    if has_maps:
        extra_guidance.append(
            "Use `google_maps` (or `maps_directions`) for directions, routes, ETA, distance, geocoding, and travel between places."
        )
        extra_guidance.append(
            "Do not use the calendar tool for travel directions or routing; only use it for managing events."
        )
        extra_guidance.append(
            "For quick directions, you can call `maps_directions` with input formatted as `origin|destination|mode?` (e.g., `Jakarta|Bandung|driving`)."
        )
        extra_guidance.append(
            "For nearest/nearby searches (e.g., 'nearest pharmacy'), call `maps_nearby` and pass `address|type` (e.g., `Bukit Golf Riverside|pharmacy`). When the type is not a standard Places type (e.g., 'musical instrument shop'), the tool will treat it as a keyword and default to 'store' type for relevance."
        )
    system_text = config.system_message
    if extra_guidance:
        system_text = (
            f"{config.system_message}\n\nTool usage guidance:\n- "
            + "\n- ".join(extra_guidance)
        )

    # 3. Ensure a supported conversational agent type
    supported_agent_types = {
        AgentType.CONVERSATIONAL_REACT_DESCRIPTION,
        AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION,
    }
    if config.agent_type not in supported_agent_types:
        raise ValueError(
            f"Unsupported agent type: {config.agent_type.value}"
        )

    # 4. Build an agent capable of calling tools multiple times
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_text),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )

    # Reuse cached executor when enabled and available
    cache_enabled = os.getenv("AGENT_EXECUTOR_CACHE_ENABLED", "true").lower() == "true"
    key = _cache_key(config, api_key)
    executor = None
    if cache_enabled:
        cached = _EXECUTOR_CACHE.get(key)
        if cached:
            _EXECUTOR_CACHE[key] = (time.time(), cached[1])
            executor = cached[1]
    if executor is None:
        agent = create_tool_calling_agent(llm, tools, prompt)
        executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            handle_parsing_errors=True,
            return_intermediate_steps=True,
            max_iterations=config.max_iterations,
            max_execution_time=config.max_execution_time,
        )
        if cache_enabled:
            _EXECUTOR_CACHE[key] = (time.time(), executor)
            if len(_EXECUTOR_CACHE) > _EXECUTOR_CACHE_MAX:
                oldest_key = sorted(_EXECUTOR_CACHE.items(), key=lambda kv: kv[1][0])[0][0]
                _EXECUTOR_CACHE.pop(oldest_key, None)

    # 6. Optionally wrap with message history for memory
    if config.memory_enabled:
        backend = MemoryBackend(config.memory_backend)
        # Lightweight observability for memory backend
        try:
            from urllib.parse import urlparse
            if backend == MemoryBackend.SQL:
                db_url = os.getenv("MEMORY_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
                host = db = ""
                if db_url:
                    p = urlparse(db_url)
                    host = f"{p.hostname}:{p.port}" if p.hostname else ""
                    db = (p.path or "/")[1:]
                lim = config.memory_max_messages if config.memory_max_messages is not None else -1
                print(f"[MEM] Using SQL memory backend (host={host} db={db} limit={lim})")
            else:
                lim = config.memory_max_messages if config.memory_max_messages is not None else -1
                print(f"[MEM] Using memory backend: {backend.value} limit={lim}")
        except Exception:
            pass
        history_loader = get_history_loader(backend, limit=config.memory_max_messages)
        executor = RunnableWithMessageHistory(
            executor,
            history_loader,
            input_messages_key="input",
            history_messages_key="chat_history",
            output_messages_key="output",
        )

    return executor
