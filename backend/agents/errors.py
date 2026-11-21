"""Errors shared by agent tools and the code storage computers run."""


class ToolError(Exception):
    """Raise from a tool handler for a clean is_error result the agent can
    reason about (vs. an unexpected crash)."""
