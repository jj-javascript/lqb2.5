"""Pure-function and queue-parsing tests for import_queue."""

from __future__ import annotations

import pytest

from helpers import (
    book_page_html,
    import_queue,
    profile_html,
    write_queue_item,
)


# ------------------------------------------------------------ parse_year_read


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2021", 2021),
        ("1995", 1995),
        (2021, 2021),
        (" 2020 ", 2020),
        ("", None),
        (None, None),
        ("21", None),
        ("1899", None),
        ("2101", None),
        ("20951", None),
        ("abcd", None),
        ("c. 2001", None),
    ],
)
def test_parse_year_read(value, expected, tmp_path):
    assert import_queue.parse_year_read(value, tmp_path / "q.md") == expected


# ------------------------------------------------------- split_front_matter


def test_split_front_matter_round_trip():
    fm, fm_text, body = import_queue.split_front_matter(
        "---\ntitle: Hi\ntags:\n  - a\n---\nbody text\n"
    )
    assert fm == {"title": "Hi", "tags": ["a"]}
    assert fm_text == "title: Hi\ntags:\n  - a"
    assert body == "body text\n"


def test_split_front_matter_missing_block():
    with pytest.raises(import_queue.QueueItemError):
        import_queue.split_front_matter("no front matter here")


# ------------------------------------------------------- set_scalar / set_list


def test_set_scalar_replaces_existing_line():
    out = import_queue.set_scalar("title: Old\nyear_read: 2020", "year_read", "2021")
    assert out == "title: Old\nyear_read: 2021"


def test_set_scalar_appends_missing_key():
    out = import_queue.set_scalar("title: Old", "year_read", "2021")
    assert out == "title: Old\nyear_read: 2021"


def test_set_scalar_on_empty_front_matter():
    assert import_queue.set_scalar("", "review", "true") == "review: true"


def test_set_list_replaces_block_list():
    original = "title: X\ntags:\n  - fiction\n  - scifi\nreview: false"
    out = import_queue.set_list(original, "tags", ["fiction", "love"])
    assert out == "title: X\ntags:\n  - fiction\n  - love\nreview: false"


def test_set_list_appends_missing_key():
    out = import_queue.set_list("title: X", "tags", ["fiction"])
    assert out == "title: X\ntags:\n  - fiction"


def test_set_list_replaces_flush_left_items():
    # PyYAML/Sveltia-style dump: list items at column 0, not indented.
    original = "title: X\ntags:\n- fiction\n- scifi\nreview: false"
    out = import_queue.set_list(original, "tags", ["fiction", "love"])
    assert out == "title: X\ntags:\n  - fiction\n  - love\nreview: false"


# ------------------------------------------------------------- key functions


@pytest.mark.parametrize(
    "author,expected",
    [
        ("Octavia E. Butler", "octavia e. butler"),
        ("bell hooks, George Sanders", "bell hooks"),
        ("First Author with Second Author", "first author"),
        ("First Author & Second Author", "first author"),
        ("First Author and Second Author", "first author"),
        ("José O'Brien", "jose o'brien"),
    ],
)
def test_author_key(author, expected):
    assert import_queue.author_key(author) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Deep Work: Rules for Focused Success", "deep work"),
        ("Octavia's Brood: Science Fiction Stories", "octavias brood"),
        ("A World Without Email", "a world without email"),
        ("Kindred: A Graphic Novel Adaptation", "kindred"),
    ],
)
def test_title_key_matches_importer_dedupe(title, expected):
    assert import_queue.title_key(title) == expected


# ------------------------------------------------------------------ read_queue


def test_read_queue_splits_valid_invalid_and_status_items(env):
    write_queue_item(
        env.queue,
        "valid.md",
        {
            "goodreads": "https://www.goodreads.com/book/show/12345",
            "review": "Loved it.",
            "year_read": 2021,
        },
    )
    write_queue_item(
        env.queue,
        "invalid.md",
        {"goodreads": "https://www.example.com/not-a-book"},
    )
    write_queue_item(
        env.queue,
        "already-marked.md",
        {
            "goodreads": "https://www.goodreads.com/book/show/67890",
            "status": "Already in collection: Some Book",
        },
    )

    items, invalid = import_queue.read_queue()

    assert [(p.name, book_id, review, year) for p, book_id, review, year in items] == [
        ("valid.md", "12345", "Loved it.", 2021)
    ]
    assert len(invalid) == 1
    assert invalid[0][0].name == "invalid.md"
    assert "not a Goodreads BOOK link" in invalid[0][1]


def test_read_queue_keeps_unparseable_items_for_retry(env):
    (env.queue / "broken.md").write_text("no front matter at all", encoding="utf-8")
    items, invalid = import_queue.read_queue()
    assert items == []
    assert invalid == []
    assert (env.queue / "broken.md").exists()


def test_read_queue_empty_directory(env):
    assert import_queue.read_queue() == ([], [])


# ------------------------------------------------------------------ fetch_book


def test_fetch_book_parses_json_ld_and_apollo_description():
    html = book_page_html(
        "42",
        title="Some Title",
        author="First Author, Second Author",
        description="The description.",
        cover_url="https://covers.example/42.jpg",
    )

    class S:
        def get(self, url, timeout=60):
            class R:
                status_code = 200
                text = html

            return R()

    book = import_queue.fetch_book(S(), "42")
    assert book["title"] == "Some Title"
    assert book["author"] == "First Author, Second Author"
    assert book["cover_url"] == "https://covers.example/42.jpg"
    assert book["description"] == "The description."
    assert book["goodreads_url"] == "https://www.goodreads.com/book/show/42"


def test_fetch_book_http_error_raises_queue_item_error():
    class S:
        def get(self, url, timeout=60):
            class R:
                status_code = 404
                text = ""

            return R()

    with pytest.raises(import_queue.QueueItemError, match="HTTP 404"):
        import_queue.fetch_book(S(), "42")


# ------------------------------------------------------------ discover_shelves


def test_discover_shelves_excludes_status_shelves():
    class S:
        def get(self, url, timeout=60):
            class R:
                status_code = 200
                text = profile_html(
                    ["fiction", "read", "to-read", "scifi", "special-books"]
                )

            return R()

    shelves = import_queue.discover_shelves(S())
    assert [s["slug"] for s in shelves] == ["fiction", "scifi"]
    assert all(s["tag"] for s in shelves)


def test_discover_shelves_raises_when_layout_changed():
    class S:
        def get(self, url, timeout=60):
            class R:
                status_code = 200
                text = "<html><body>no shelves here</body></html>"

            return R()

    with pytest.raises(import_queue.QueueItemError, match="no shelves"):
        import_queue.discover_shelves(S())
