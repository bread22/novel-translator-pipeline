from translator.review.reviewer import dynamic_review_timeout


def test_dynamic_review_timeout_uses_complete_payload() -> None:
    small = {
        "items": [{"id": "p1", "source": "短文", "translated": "短文"}],
        "book_memory": {},
        "glossary": {},
    }
    large_context = {
        **small,
        "book_memory": {"entries": [{"key": "人物", "value": "背景" * 2000}]},
        "glossary": {"terms": [{"source": "用语", "target": "术语"}] * 200},
    }

    assert dynamic_review_timeout(small) == 120
    assert dynamic_review_timeout(large_context) > dynamic_review_timeout(small)


def test_dynamic_review_timeout_is_capped_at_doubled_limit() -> None:
    payload = {"items": [{"source": "正文"}], "book_memory": {"summary": "背景" * 100_000}}

    assert dynamic_review_timeout(payload) == 720
