"""Memory utilities for agents."""

from typing import Optional
from langchain.memory import ConversationBufferMemory, BaseChatMemory


def get_memory_if_enabled(enabled: bool) -> Optional[BaseChatMemory]:
    """Return a ConversationBufferMemory if enabled, else ``None``.

    Parameters
    ----------
    enabled: bool
        Flag indicating whether memory should be activated.
    """
    if not enabled:
        return None
    # return messages so chat history is preserved across turns
    return ConversationBufferMemory(memory_key="chat_history", return_messages=True)
