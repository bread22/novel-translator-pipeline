from __future__ import annotations

from email.message import Message
import io
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from translator.core.job_control import JobCancelled
from translator.providers.errors import ProviderHTTPError, ProviderTimeoutError
from translator.providers.openai_provider import OpenAIProvider
from translator.review.reviewer import (
    _execute_review_with_fallbacks,
    _execute_segment_with_adaptive_split,
    should_adaptively_split,
)


def http_error(status: int = 503, retry_after: float | None = None) -> ProviderHTTPError:
    return ProviderHTTPError(
        f"provider HTTP {status}",
        provider="primary",
        status_code=status,
        response_excerpt="overloaded",
        retry_after_seconds=retry_after,
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_503_retries_same_payload_and_split_path_then_succeeds() -> None:
    calls: list[dict] = []
    states: list[dict] = []
    failures = [http_error(), http_error()]

    class Provider:
        def review(self, _kind, payload, _schema, **_kwargs):
            calls.append(payload)
            if failures:
                raise failures.pop(0)
            return {"checked_ids": ["p1"]}

    clock = FakeClock()
    with (
        patch("translator.review.reviewer._review_backends", return_value=["primary"]),
        patch("translator.review.reviewer.get_provider", return_value=Provider()),
    ):
        result = _execute_review_with_fallbacks(
            "chapter",
            {"items": [{"id": "p1"}]},
            Path("schema.json"),
            on_reviewer_status=states.append,
            retry_config={"transient_http_retries": 3},
            random_uniform=lambda low, _high: low,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

    assert result == {"checked_ids": ["p1"]}
    assert len(calls) == 3 and all(call is calls[0] for call in calls)
    assert {state["split_path"] for state in states} == {"root"}
    assert [state["status"] for state in states] == [
        "reviewing", "retry_wait", "retrying", "retry_wait", "retrying", "completed"
    ]
    assert [state["retry_delay_seconds"] for state in states if state["status"] == "retry_wait"] == [10.0, 20.0]


def test_transient_retry_exhaustion_falls_back_without_split() -> None:
    calls: list[str] = []
    states: list[dict] = []

    class Provider:
        def __init__(self, name: str) -> None:
            self.name = name

        def review(self, *_args, **_kwargs):
            calls.append(self.name)
            if self.name == "primary":
                raise http_error()
            return {"checked_ids": ["p1"]}

    clock = FakeClock()
    with (
        patch("translator.review.reviewer._review_backends", return_value=["primary", "fallback"]),
        patch("translator.review.reviewer.get_provider", side_effect=Provider),
    ):
        result = _execute_review_with_fallbacks(
            "chapter", {"items": [{"id": "p1"}]}, Path("schema.json"),
            on_reviewer_status=states.append,
            retry_config={"transient_http_retries": 3},
            random_uniform=lambda low, _high: low,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

    assert result["checked_ids"] == ["p1"]
    assert calls == ["primary"] * 4 + ["fallback"]
    assert all(state["split_path"] == "root" for state in states)
    exhausted = [state for state in states if state["status"] == "failed"]
    assert exhausted[0]["retries_exhausted"] is True


def test_timeout_becomes_split_eligible_only_after_retry_exhaustion() -> None:
    error = ProviderTimeoutError("timed out", provider="primary", timeout_seconds=120)
    assert should_adaptively_split(error) is False
    error.retries_exhausted = True
    assert should_adaptively_split(error) is True
    assert should_adaptively_split(http_error()) is False


def test_retry_wait_is_cancelled_before_another_request() -> None:
    calls = 0
    clock = FakeClock()
    cancelled = False

    class Provider:
        def review(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise http_error()

    def cancel_check() -> None:
        if cancelled:
            raise JobCancelled("cancelled")

    def sleeper(seconds: float) -> None:
        nonlocal cancelled
        clock.sleep(seconds)
        cancelled = True

    with (
        patch("translator.review.reviewer._review_backends", return_value=["primary"]),
        patch("translator.review.reviewer.get_provider", return_value=Provider()),
        pytest.raises(JobCancelled),
    ):
        _execute_review_with_fallbacks(
            "chapter", {}, Path("schema.json"), cancel_check=cancel_check,
            retry_config={"transient_http_retries": 3},
            random_uniform=lambda low, _high: low,
            monotonic=clock.monotonic,
            sleeper=sleeper,
        )

    assert calls == 1
    assert clock.sleeps == [0.25]


def test_openai_review_preserves_bounded_http_metadata(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    provider = OpenAIProvider("provider-a", {"model": "model", "api_key": "sk-test"})
    headers = Message()
    headers["Retry-After"] = "17.5"
    body = b'{"error":"Bearer secret-token sk-1234567890 overloaded"}' + b"x" * 2000
    error = HTTPError("https://example.invalid", 503, "overloaded", headers, io.BytesIO(body))

    with patch.object(provider, "_post_chat", side_effect=error), pytest.raises(ProviderHTTPError) as caught:
        provider.review("chapter", {"items": []}, schema, timeout=123)

    exc = caught.value
    assert exc.provider == "provider-a"
    assert exc.status_code == 503
    assert exc.retryable is True
    assert exc.retry_after_seconds == 17.5
    assert len(exc.response_excerpt) <= 1000
    assert "secret-token" not in exc.response_excerpt
    assert "1234567890" not in exc.response_excerpt


def test_openai_review_maps_read_timeout(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    provider = OpenAIProvider("provider-a", {"model": "model", "api_key": "sk-test"})

    with patch.object(provider, "_post_chat", side_effect=TimeoutError("read timed out")), pytest.raises(ProviderTimeoutError) as caught:
        provider.review("chapter", {"items": []}, schema, timeout=321)

    assert caught.value.timeout_seconds == 321
    assert caught.value.provider == "provider-a"


def review_result(ids: list[str]) -> dict:
    return {
        "checked_ids": ids,
        "fixes": [],
        "glossary_delta": {},
        "memory_delta": {},
        "chapter_state": {},
    }


def test_503_exhaustion_uses_fallback_without_adaptive_children() -> None:
    states: list[dict] = []
    calls: list[str] = []

    class Provider:
        def __init__(self, name: str) -> None:
            self.name = name

        def review(self, _kind, payload, _schema, **_kwargs):
            calls.append(self.name)
            if self.name == "primary":
                raise http_error()
            return review_result([item["id"] for item in payload["items"]])

    with (
        patch("translator.review.reviewer._review_backends", return_value=["primary", "fallback"]),
        patch("translator.review.reviewer.get_provider", side_effect=Provider),
        patch("translator.review.reviewer.load_config", return_value={
            "pipeline": {
                "transient_http_retries": 3,
                "transient_backoff_min_seconds": 0,
                "transient_backoff_max_seconds": 0,
                "transient_backoff_cap_seconds": 0,
            }
        }),
    ):
        result = _execute_segment_with_adaptive_split(
            {}, [{"id": "p1"}, {"id": "p2"}], Path("schema.json"),
            backend="primary", on_reviewer_status=states.append,
        )

    assert result["checked_ids"] == ["p1", "p2"]
    assert calls == ["primary"] * 4 + ["fallback"]
    assert {state["split_path"] for state in states} == {"root"}


def test_timeout_retries_original_then_splits_into_children() -> None:
    states: list[dict] = []
    calls: list[tuple[str, ...]] = []

    class Provider:
        def review(self, _kind, payload, _schema, **_kwargs):
            ids = tuple(item["id"] for item in payload["items"])
            calls.append(ids)
            if len(ids) > 1:
                raise ProviderTimeoutError("timed out", provider="primary", timeout_seconds=120)
            return review_result(list(ids))

    with (
        patch("translator.review.reviewer._review_backends", return_value=["primary"]),
        patch("translator.review.reviewer.get_provider", return_value=Provider()),
        patch("translator.review.reviewer.load_config", return_value={"pipeline": {"timeout_retries": 1}}),
    ):
        result = _execute_segment_with_adaptive_split(
            {}, [{"id": "p1"}, {"id": "p2"}], Path("schema.json"),
            backend="primary", on_reviewer_status=states.append,
        )

    assert result["checked_ids"] == ["p1", "p2"]
    assert calls == [("p1", "p2"), ("p1", "p2"), ("p1",), ("p2",)]
    reviewing_paths = [
        state["split_path"] for state in states if state["status"] in {"reviewing", "retrying"}
    ]
    assert reviewing_paths == ["root", "root", "root.L", "root.R"]
