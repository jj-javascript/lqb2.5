"""Contract tests: importer output must match what the bookshelf consumes.

The bookshelf templates read a fixed set of front matter fields with fixed
types (title/author strings, cover path, integer dimensions, tags list,
boolean review flag, optional year/links). These tests fail if a future
import_queue change emits anything the templates can't render — catching
runtime breakage before it reaches the site. The real _books collection is
linted against the same contract.
"""

from __future__ import annotations

import yaml

from helpers import (
    REPO_ROOT,
    book_page_html,
    import_queue,
    profile_html,
    read_book,
    write_queue_item,
    write_shelves_config,
)


def setup_env(env):
    write_shelves_config(
        env.shelves_yml,
        [("Fiction", "fiction", True), ("Love", "love", True)],
    )
    env.session.profile = profile_html(["fiction", "love"])


def test_generated_front_matter_matches_template_contract(env):
    setup_env(env)
    env.session.grid_members = {"fiction": {"12345"}, "love": set()}
    env.session.book_pages["12345"] = book_page_html(
        "12345",
        title='The "Best" Book: A Memoir',
        author="José O'Brien",
        description="",
    )
    write_queue_item(
        env.queue,
        "item.md",
        {"goodreads": "https://www.goodreads.com/book/show/12345"},
    )

    assert import_queue.main() == 0

    book_path = env.books / "the-best-book-a-memoir.md"
    text = book_path.read_text(encoding="utf-8")

    # The raw front matter must round-trip through a strict YAML parser —
    # this is what Jekyll does at build time.
    _, fm_text, _ = import_queue.split_front_matter(text)
    data = yaml.safe_load(fm_text)

    assert data["title"] == 'The "Best" Book: A Memoir'  # quotes survive
    assert data["author"] == "José O'Brien"
    assert isinstance(data["cover"], str)
    assert data["cover"].startswith("/assets/images/books/")
    assert isinstance(data["cover_width"], int) and data["cover_width"] > 0
    assert isinstance(data["cover_height"], int) and data["cover_height"] > 0
    assert data["review"] is False  # boolean, not the string "false"
    assert isinstance(data["goodreads"], str)
    assert "goodreads.com/book/show/" in data["goodreads"]
    assert "year_read" not in data  # absent, never empty/null
    assert isinstance(data["tags"], list)
    assert all(isinstance(tag, str) for tag in data["tags"])
    assert data["tags"] == ["fiction"]


def test_generated_tags_are_sorted_slug_strings(env):
    setup_env(env)
    env.session.grid_members = {"fiction": {"12345"}, "love": {"12345"}}
    env.session.book_pages["12345"] = book_page_html("12345")
    write_queue_item(
        env.queue,
        "item.md",
        {"goodreads": "https://www.goodreads.com/book/show/12345"},
    )

    assert import_queue.main() == 0
    fm, _ = read_book(env.books / "test-book.md")
    assert fm["tags"] == ["fiction", "love"]  # sorted slugs


def test_real_library_matches_template_contract():
    """Every book currently in _books must be renderable by the templates."""
    books_dir = REPO_ROOT / "_books"
    paths = [
        p
        for p in sorted(books_dir.glob("*.md"))
        if not p.name.startswith("zz-fixture-")
    ]
    assert len(paths) >= 70, "expected the real library to be present"

    for path in paths:
        fm, _, _ = import_queue.split_front_matter(
            path.read_text(encoding="utf-8")
        )
        name = path.name

        assert isinstance(fm.get("title"), str) and fm["title"].strip(), name
        assert isinstance(fm.get("author"), str) and fm["author"].strip(), name

        cover = fm.get("cover")
        assert isinstance(cover, str) and cover.startswith(
            "/assets/images/books/"
        ), name
        assert (REPO_ROOT / cover.lstrip("/")).exists(), f"{name}: {cover}"

        for dim in ("cover_width", "cover_height"):
            if dim in fm:
                assert isinstance(fm[dim], int) and fm[dim] > 0, f"{name} {dim}"

        if "tags" in fm:
            assert isinstance(fm["tags"], list), name
            assert all(isinstance(tag, str) for tag in fm["tags"]), name
        if "review" in fm:
            assert isinstance(fm["review"], bool), name
        if "year_read" in fm:
            assert isinstance(fm["year_read"], int), name
            assert 1900 <= fm["year_read"] <= 2100, name
        if "goodreads" in fm:
            assert "goodreads.com" in str(fm["goodreads"]), name
        if "highlights" in fm:
            assert str(fm["highlights"]).startswith("http"), name
