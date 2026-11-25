"""Orchestrator error types. Expected failures are converted to a JSON error payload, never leaked."""


class OrchestratorError(Exception):
    """Base class for orchestrator failures."""


class LLMUnavailableError(OrchestratorError):
    """The LLM API could not be reached or refused the request (network, 429, 5xx, auth, ...)."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMOutputError(OrchestratorError):
    """The LLM answered, but the output is not usable (no JSON, wrong shape, truncated)."""
