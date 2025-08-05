"""Simple calculator tool."""

from langchain.agents import Tool


def _evaluate(expression: str) -> str:
    """Evaluate a math expression and return the result as string."""
    try:
        # using eval with restricted globals for basic arithmetic
        result = eval(expression, {"__builtins__": {}})
        return str(result)
    except Exception as exc:  # pragma: no cover - error path
        return f"error: {exc}"


calc_tool = Tool(
    name="Calculator",
    func=_evaluate,
    description="Evaluate basic math expressions, e.g. '2 + 2'"
)

__all__ = ["calc_tool"]
