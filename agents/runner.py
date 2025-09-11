# Run agent loop
# agents/runner.py

import os
from uuid import uuid4
from config.schema import AgentConfig
from agents.builder import build_agent
from database.client import get_agent_owner_id
from agents.rag import retrieve_topk, format_context
from agents.memory import persist_conversation
import json


def run_custom_agent(
    agent_id: str,
    config: AgentConfig,
    message: str,
    session_id: str | None = None,
    owner_id: str | None = None,
    rag_enable: bool | None = None,
) -> str:
    """Build agent from config and execute it on the provided message."""
    # 0. Best-effort: retrieve RAG context and augment prompt
    try:
        top_k = int(os.getenv("RAG_TOP_K", "5"))
    except Exception:
        top_k = 5
    # Build composite session id: "<user>:<agent>|<chat>"
    table_key = agent_id
    chat_sid = (session_id or "").strip() or uuid4().hex
    session_id_for_memory = f"{table_key}|{chat_sid}"
    try:
        user_id = owner_id or get_agent_owner_id(agent_id)
        if user_id:
            table_key = f"{user_id}:{agent_id}"
            session_id_for_memory = f"{table_key}|{chat_sid}"
            use_rag = os.getenv("RAG_ENABLED", "true").lower() == "true"
            if rag_enable is not None:
                use_rag = bool(rag_enable)
            snippets = retrieve_topk(user_id, agent_id, message, top_k=top_k, api_key=config.openai_api_key) if use_rag else []
            if snippets:
                ctx = format_context(snippets)
                # Build neat, readable log with scores and snippet previews
                scores = []
                previews = []
                max_chars = 200
                try:
                    max_chars = int(os.getenv("RAG_SNIPPET_PREVIEW_CHARS", "200"))
                except Exception:
                    pass
                for i, s in enumerate(snippets, start=1):
                    # Score list
                    try:
                        scores.append(f"{float(s.get('score')):.4f}")
                    except Exception:
                        scores.append("?")
                    # Snippet preview line
                    try:
                        tx = (s.get("text") or "").strip()
                        if len(tx) > max_chars:
                            tx = tx[:max_chars] + "..."
                        sc = s.get("score")
                        sc_s = f"{float(sc):.4f}" if sc is not None else "?"
                        previews.append(f"  [{i}] score={sc_s} text=\"{tx}\"")
                    except Exception:
                        previews.append(f"  [{i}] score=? text=<unavailable>")

                # Prepend context to system_message
                new_cfg = config.model_copy(deep=True)
                before_len = len(config.system_message or "")
                new_cfg.system_message = f"{ctx}\n\n{config.system_message}"
                after_len = len(new_cfg.system_message or "")
                # Consolidated block log
                lines = []
                lines.append("[RAG] ===== Context Injection =====")
                lines.append(f"[RAG] Agent={agent_id} User={user_id} Snippets={len(snippets)}")
                lines.append(f"[RAG] Scores: {', '.join(scores)}")
                if os.getenv("RAG_LOG_CONTEXT", "true").lower() == "true":
                    lines.append("[RAG] Snippet previews:")
                    for p in previews:
                        lines.append("[RAG]" + p)
                lines.append(f"[RAG] System message length: {before_len} → {after_len}")
                print("\n".join(lines))
                # Full system message block if enabled
                if os.getenv("RAG_LOG_SYSTEM_MESSAGE", "true").lower() == "true":
                    print("[RAG] ----- BEGIN SYSTEM MESSAGE -----\n" + new_cfg.system_message + "\n[RAG] ----- END SYSTEM MESSAGE -----")
                config = new_cfg
            else:
                print(f"[RAG] no context found for agent={agent_id} user={user_id}")
        else:
            print(f"[RAG] could not resolve user_id for agent={agent_id}; skipping RAG")
    except Exception as e:
        print(f"[RAG] unexpected error preparing context: {e}")

    agent = build_agent(config)
    payload = {"input": message}
    try:
        if config.memory_enabled:
            result = agent.invoke(payload, config={"configurable": {"session_id": session_id_for_memory}})
        else:
            payload["chat_history"] = []
            result = agent.invoke(payload)
    except Exception as exc:
        # Convert unexpected agent errors into a user-visible message instead of HTTP 500s
        raise ValueError(f"Agent execution failed: {exc}") from exc

    # Normalize various return types to a safe string
    if isinstance(result, dict):
        result = result.get("output") or json.dumps(result, default=str)
    elif hasattr(result, "content"):
        try:
            result = result.content  # e.g., AIMessage
        except Exception:
            result = str(result)
    elif not isinstance(result, str):
        try:
            result = json.dumps(result, default=str)
        except Exception:
            result = str(result)

    if result == "Agent stopped due to iteration limit or time limit.":
        raise ValueError(
            "Agent execution stopped before producing a final answer. "
            "Consider increasing max_iterations or revising the prompt."
        )
    # Best-effort: ensure conversation is persisted to per-agent memory table
    try:
        if os.getenv("MEMORY_FALLBACK_WRITE", "true").lower() == "true":
            persist_conversation(session_id_for_memory, message, result, chat_session_id=chat_sid)
    except Exception:
        pass
    return result
