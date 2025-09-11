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
from langchain_openai import ChatOpenAI


def _finalize_output(
    api_key: str,
    model_name: str,
    user_message: str,
    draft_text: str | None,
    tool_summaries: list[str] | None,
) -> str | None:
    """Run a lightweight finalization pass to produce a polished user-facing reply.

    Returns the finalized text, or None on failure.
    """
    try:
        if not api_key:
            return None
        text = (draft_text or "").strip()
        summaries = tool_summaries or []
        # Build a concise prompt that asks for a single final message
        sys = (
            "You are a helpful assistant. Produce one final message for the user. "
            "Integrate the draft answer and the tools' outcomes. Do not show raw JSON or a separate tool section. "
            "Be concise and keep the user's language."
        )
        human = (
            f"User asked: {user_message}\n\n"
            f"Draft answer (may be empty): {text}\n\n"
            f"Tool outcomes (summarized):\n" + ("\n".join(summaries) if summaries else "(none)")
        )
        # Use a fast model for the finalizer unless overridden
        model = os.getenv("FINALIZER_MODEL", model_name or "gpt-4o-mini")
        timeout = float(os.getenv("FINALIZER_TIMEOUT", "8"))
        max_retries = int(os.getenv("FINALIZER_MAX_RETRIES", "0"))
        llm = ChatOpenAI(model=model, api_key=api_key, temperature=0, timeout=timeout, max_retries=max_retries)
        msg = llm.invoke([{"role": "system", "content": sys}, {"role": "user", "content": human}])
        out = getattr(msg, "content", "")
        return out.strip() or None
    except Exception:
        return None


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

    # Normalize various return types to a safe string (combine model output + tool summaries)
    if isinstance(result, dict):
        output_text = result.get("output")
        inter = result.get("intermediate_steps") or []
        summaries = []
        gmail_text = None
        try:
            for step in inter:
                if not isinstance(step, (list, tuple)) or len(step) < 2:
                    continue
                action, obs = step[0], step[1]
                tool_name = getattr(action, "tool", None) or "tool"
                text_obs = None
                if isinstance(obs, dict):
                    msg = obs.get("message") or obs.get("detail") or obs.get("error") or obs.get("status")
                    rid = obs.get("id") or obs.get("threadId") or obs.get("messageId")
                    if msg:
                        text_obs = f"{msg}{f' (id: {rid})' if rid else ''}"
                elif isinstance(obs, str):
                    t = obs.strip()
                    if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
                        try:
                            o2 = json.loads(t)
                            if isinstance(o2, dict):
                                msg = o2.get("message") or o2.get("detail") or o2.get("error") or o2.get("status")
                                rid = o2.get("id") or o2.get("threadId") or o2.get("messageId")
                                if msg:
                                    text_obs = f"{msg}{f' (id: {rid})' if rid else ''}"
                                # Capture Gmail send details to craft a friendly final line
                                if (tool_name in {"gmail_send_message", "gmail"}) and gmail_text is None:
                                    details = o2.get("details") or {}
                                    to = details.get("to") or o2.get("to")
                                    subject = details.get("subject") or o2.get("subject")
                                    mid = o2.get("id") or o2.get("message_id") or o2.get("threadId") or o2.get("messageId")
                                    if to or subject or mid:
                                        parts = []
                                        if to:
                                            parts.append(f"ke {to}")
                                        if subject:
                                            parts.append(f"dengan subjek '{subject}'")
                                        sent_clause = " ".join(parts) if parts else ""
                                        suffix = f" (id: {mid})" if mid else ""
                                        gmail_text = "Saya sudah mengirim email" + (f" {sent_clause}" if sent_clause else "") + "." + suffix
                        except Exception:
                            pass
                    if text_obs is None and t:
                        text_obs = t[:300]
                if text_obs:
                    summaries.append(f"- {tool_name}: {text_obs}")
        except Exception:
            pass

        # Prefer conversational model output; humanize JSON output if needed
        final_text = None
        if isinstance(output_text, str) and output_text.strip():
            t = output_text.strip()
            is_json_like = (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]"))
            if not is_json_like:
                final_text = t
            else:
                try:
                    o = json.loads(t)
                    if isinstance(o, dict):
                        msg = o.get("message") or o.get("detail") or o.get("error")
                        rid = o.get("id") or o.get("threadId") or o.get("messageId")
                        if msg:
                            final_text = f"{msg}{f' (id: {rid})' if rid else ''}"
                except Exception:
                    pass

        # Prefer crafted Gmail sentence over raw tool message
        if gmail_text:
            final_text = gmail_text if not final_text else final_text

        # Optional finalizer pass to guarantee a polished user-facing message
        use_finalizer = os.getenv("FINALIZE_OUTPUT", "true").lower() == "true"
        finalized = None
        if use_finalizer:
            try:
                api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY", "")
                finalized = _finalize_output(api_key, config.model_name, message, final_text, summaries)
            except Exception:
                finalized = None

        if finalized:
            result = finalized
        else:
            show_tools = os.getenv("TOOL_RESULTS_IN_OUTPUT", "false").lower() == "true"
            if final_text and summaries:
                result = (
                    final_text
                    if not show_tools
                    else final_text + "\n\nTool results:\n" + "\n".join(summaries)
                )
            elif final_text:
                result = final_text
            elif summaries:
                result = ("\n".join(summaries)) if show_tools else (output_text or "")
            else:
                result = output_text or json.dumps(result, default=str)
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
    # Humanize common tool JSON outputs (e.g., Gmail) into a friendly sentence
    try:
        if isinstance(result, str):
            txt = result.strip()
            if (txt.startswith("{") and txt.endswith("}")) or (txt.startswith("[") and txt.endswith("]")):
                obj = json.loads(txt)
                if isinstance(obj, dict):
                    msg = obj.get("message") or obj.get("detail") or obj.get("error")
                    status = obj.get("status") or ("ok" if obj.get("ok") is True else None)
                    if msg and (status or (isinstance(msg, str) and ("success" in msg.lower() or "sent" in msg.lower()))):
                        rid = obj.get("id") or obj.get("threadId") or obj.get("messageId")
                        suffix = f" (id: {rid})" if rid else ""
                        result = f"{msg}{suffix}"
    except Exception:
        pass
    # Best-effort: ensure conversation is persisted to per-agent memory table
    try:
        if os.getenv("MEMORY_FALLBACK_WRITE", "true").lower() == "true":
            persist_conversation(session_id_for_memory, message, result, chat_session_id=chat_sid)
    except Exception:
        pass
    return result
