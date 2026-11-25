"""Full main() runs of import_queue against an isolated tmp environment."""

from __future__ import annotations

from helpers import (
    book_page_html,
    import_queue,
    profile_html,
    read_book,
    write_book,
    write_queue_item,
    write_shelves_config,
)

GOODREADS_URL = "https://www.goodreads.com/book/show/12345"


def setup_run(env, grid_members=None, profile_slugs=None):
    """Standard shelf config + profile: fiction and love enabled."""
    write_shelves_config(
        env.shelves_yml,
        [("Fiction", "fiction", True), ("Love", "love", True)],
    )
    env.session.profile = profile_html(profile_slugs or ["fiction", "love", "read"])
    env.session.grid_members = grid_members or {"fiction": set(), "love": set()}


def new_book_queue_item(env, name="item.md", book_id="12345", review="", year=None):
    fm = {"goodreads": f"https://www.goodreads.com/book/show/{book_id}"}
    if review:
        fm["review"] = review
    if year:
        fm["year_read"] = year
    return write_queue_item(env.queue, name, fm)


# ------------------------------------------------------------- new imports


def test_new_book_happy_path(env, capsys):
    setup_run(env, grid_members={"fiction": {"12345"}, "love": set()})
    env.session.book_pages["12345"] = book_page_html(
        "12345", title="Test Book", author="Test Author", description="A desc."
    )
    new_book_queue_item(env, review="My own review.", year=2021)

    assert import_queue.main() == 0

    # Queue consumed; book file created with the full importer field set.
    assert list(env.queue.glob("*.md")) == []
    book_path = env.books / "test-book.md"
    fm, body = read_book(book_path)
    assert fm["title"] == "Test Book"
    assert fm["author"] == "Test Author"
    assert fm["cover"] == "/assets/images/books/test-book.webp"
    assert fm["cover_width"] == 200 and fm["cover_height"] == 300
    assert fm["goodreads"] == GOODREADS_URL
    assert fm["review"] is True
    assert fm["year_read"] == 2021
    assert fm["tags"] == ["fiction"]
    assert fm["cover_source_url"] == "https://covers.example/12345.jpg"
    assert "My own review." in body  # pasted review beats the description

    # Cover bytes came through the (mocked) download + encode pipeline.
    assert (env.covers / "test-book.webp").read_bytes() == b"webp-bytes"

    # 'love' is switched on but unused: it lands in the _tags waiting room.
    love_doc = env.tags / "love.md"
    assert love_doc.exists()
    love_fm, _ = read_book(love_doc)
    assert love_fm["slug"] == "love"

    out = capsys.readouterr().out
    assert "New books imported:   1" in out
    assert "+ Test Book" in out


def test_new_book_without_review_or_year_uses_description(env):
    setup_run(env)
    env.session.book_pages["12345"] = book_page_html(
        "12345", title="No Review Book", author="Some Author", description="Desc only."
    )
    new_book_queue_item(env)  # no review, no year

    assert import_queue.main() == 0
    fm, body = read_book(env.books / "no-review-book.md")
    assert fm["review"] is False
    assert "year_read" not in fm
    assert "Desc only." in body
    assert "tags" not in fm  # no shelf membership


# ------------------------------------------------------- already-present books


def existing_test_book(env, **overrides):
    fm = {
        "title": "Test Book",
        "author": "Test Author",
        "cover": "/assets/images/books/test-book.webp",
        "cover_width": 200,
        "cover_height": 300,
        "goodreads": GOODREADS_URL,
        "review": True,
        "year_read": 2020,
        "tags": ["fiction"],
        "cover_source_url": "https://covers.example/12345.jpg",
    }
    fm.update(overrides)
    return write_book(env.books, "test-book", fm, body="\nExisting review body.\n")


def test_existing_book_never_overwrites_review_or_year(env):
    setup_run(env, grid_members={"fiction": {"12345"}, "love": set()})
    env.session.book_pages["12345"] = book_page_html("12345")
    existing = existing_test_book(env)
    before = existing.read_text(encoding="utf-8")
    new_book_queue_item(env, review="A different review.", year=2021)

    assert import_queue.main() == 0

    assert existing.read_text(encoding="utf-8") == before  # untouched

    # The queue item is KEPT with a status note for the editor.
    queue_files = list(env.queue.glob("*.md"))
    assert len(queue_files) == 1
    qfm, _ = read_book(queue_files[0])
    assert "Already in collection: Test Book" in qfm["status"]
    assert "existing review kept" in qfm["status"]
    assert "year read added" not in qfm["status"]


def test_existing_book_attaches_missing_review_and_year(env):
    setup_run(env, grid_members={"fiction": {"12345"}, "love": set()})
    env.session.book_pages["12345"] = book_page_html("12345")
    existing = env.books / "test-book.md"
    write_book(
        env.books,
        "test-book",
        {
            "title": "Test Book",
            "author": "Test Author",
            "cover": "/assets/images/books/test-book.webp",
            "cover_width": 200,
            "cover_height": 300,
            "goodreads": GOODREADS_URL,
            "review": False,
            "tags": ["fiction"],
            "cover_source_url": "https://covers.example/12345.jpg",
        },
    )
    new_book_queue_item(env, review="Finally wrote my review.", year=2021)

    assert import_queue.main() == 0

    fm, body = read_book(existing)
    assert fm["review"] is True
    assert fm["year_read"] == 2021
    assert "Finally wrote my review." in body

    qfm, _ = read_book(next(env.queue.glob("*.md")))
    assert "your review was added" in qfm["status"]
    assert "year read added" in qfm["status"]


def test_existing_book_tags_merge_append_only(env):
    setup_run(env, grid_members={"fiction": {"12345"}, "love": {"12345"}})
    env.session.book_pages["12345"] = book_page_html("12345")
    existing = existing_test_book(env)  # already tagged fiction
    new_book_queue_item(env)

    assert import_queue.main() == 0

    fm, _ = read_book(existing)
    assert fm["tags"] == ["fiction", "love"]  # merged, sorted, nothing removed


# --------------------------------------------------------- failure handling


def test_fetch_failure_keeps_queue_file_for_retry(env, capsys):
    setup_run(env)
    env.session.book_pages["12345"] = (500, "server error")
    new_book_queue_item(env)

    assert import_queue.main() == 0  # transient failure is not fatal

    queue_files = list(env.queue.glob("*.md"))
    assert len(queue_files) == 1
    qfm, _ = read_book(queue_files[0])
    assert "status" not in qfm  # untouched, so a re-run retries it
    assert list(env.books.glob("*.md")) == []
    assert "Warnings" in capsys.readouterr().out


def test_invalid_url_gets_a_status_note(env, capsys):
    setup_run(env)
    write_queue_item(env.queue, "bad.md", {"goodreads": "https://example.com/x"})

    assert import_queue.main() == 0

    qfm, _ = read_book(env.queue / "bad.md")
    assert "not a Goodreads BOOK link" in qfm["status"]
    assert "Invalid queue links:  1" in capsys.readouterr().out


def test_duplicate_submission_in_one_run_consumed_once(env):
    setup_run(env)
    env.session.book_pages["12345"] = book_page_html("12345")
    new_book_queue_item(env, name="first.md")
    new_book_queue_item(env, name="second.md")

    assert import_queue.main() == 0

    assert len(list(env.books.glob("*.md"))) == 1
    assert list(env.queue.glob("*.md")) == []  # both consumed


# --------------------------------------------------------------- idempotency


def test_resubmitting_imported_book_is_a_no_op(env):
    setup_run(env, grid_members={"fiction": {"12345"}, "love": set()})
    env.session.book_pages["12345"] = book_page_html("12345")
    new_book_queue_item(env, review="My review.", year=2021)
    assert import_queue.main() == 0
    book_path = env.books / "test-book.md"
    after_first = book_path.read_text(encoding="utf-8")

    # Re-submit the same book via a fresh queue item.
    new_book_queue_item(env, name="again.md")
    assert import_queue.main() == 0

    assert len(list(env.books.glob("*.md"))) == 1
    assert book_path.read_text(encoding="utf-8") == after_first
    qfm, _ = read_book(env.queue / "again.md")
    assert "Already in collection" in qfm["status"]


# --------------------------------------------------------- library cover sweep


def test_sweep_updates_stale_covers_only_after_new_import(env):
    setup_run(env)
    # Existing library book with a stale cover source, not in the queue.
    other = write_book(
        env.books,
        "other-book",
        {
            "title": "Other Book",
            "author": "Other Author",
            "cover": "/assets/images/books/other-book.webp",
            "cover_width": 200,
            "cover_height": 300,
            "goodreads": "https://www.goodreads.com/book/show/777",
            "review": False,
            "tags": ["fiction"],
            "cover_source_url": "https://covers.example/777-old.jpg",
        },
    )
    env.session.book_pages["12345"] = book_page_html("12345")
    env.session.book_pages["777"] = book_page_html(
        "777",
        title="Other Book",
        author="Other Author",
        cover_url="https://covers.example/777-new.jpg",
    )
    # First encode call is the new import's cover; second is the sweep's.
    env.encode.side_effect = [
        (b"webp-new-book", 200, 300),
        (b"webp-other-book", 400, 250),
    ]
    new_book_queue_item(env)

    assert import_queue.main() == 0

    fm, _ = read_book(other)
    assert fm["cover_source_url"] == "https://covers.example/777-new.jpg"
    assert fm["cover_width"] == 400
    assert fm["cover_height"] == 250
    overwritten = env.tmp_path / "assets" / "images" / "books" / "other-book.webp"
    assert overwritten.read_bytes() == b"webp-other-book"


def test_no_sweep_when_nothing_new_imported(env, capsys):
    setup_run(env)
    existing_test_book(env)
    env.session.book_pages["12345"] = book_page_html("12345")
    new_book_queue_item(env)  # resolves to the existing book: nothing new

    assert import_queue.main() == 0
    assert "Library cover sweep" not in capsys.readouterr().out
