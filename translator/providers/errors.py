from __future__ import annotations


class ProviderRequestError(RuntimeError):
    """A provider request failure that retains routing and retry metadata."""

    def __init__(self, message: str, *, provider: str, retryable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.retries_exhausted = False


class ProviderHTTPError(ProviderRequestError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int,
        response_excerpt: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider, retryable=status_code in {429, 500, 502, 503, 504})
        self.status_code = status_code
        self.response_excerpt = response_excerpt
        self.retry_after_seconds = retry_after_seconds


class ProviderTimeoutError(ProviderRequestError):
    def __init__(self, message: str, *, provider: str, timeout_seconds: int) -> None:
        super().__init__(message, provider=provider, retryable=True)
        self.timeout_seconds = timeout_seconds


class ProviderConnectionError(ProviderRequestError):
    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=True)


class ProviderResponseError(ValueError):
    """The provider responded, but its content cannot satisfy the review contract."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.provider = provider
