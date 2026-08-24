from translator.web.path_policy import book_id_from_title, validate_book_id


def test_book_id_from_title_normalizes_spaces_and_punctuation() -> None:
    book_id = book_id_from_title("美人講師 汚辱・特別講座")

    assert book_id == "美人講師-汚辱-特別講座"
    assert validate_book_id(book_id) == book_id


def test_book_id_from_title_falls_back_for_punctuation_only_title() -> None:
    book_id = book_id_from_title("・・・")

    assert book_id.startswith("book-")
    assert validate_book_id(book_id) == book_id


def test_book_id_from_title_bounds_long_ids_with_stable_digest() -> None:
    title = "长" * 200

    first = book_id_from_title(title)
    second = book_id_from_title(title)

    assert first == second
    assert len(first) <= 128
    assert validate_book_id(first) == first
