"""cover_sync (Goodreads-side cover changes) and sync_unused_tags tests."""

from __future__ import annotations

import pytest

from helpers import import_queue, write_book

COVER_URL = "https://covers.example/1.jpg"


# ---------------------------------------------------------------- cover_sync


def test_first_observation_records_source_without_touching_image(env):
    fm_text, overwritten = import_queue.cover_sync(
        env.session, COVER_URL, {}, "title: X\ncover: /assets/images/books/x.webp", {}
    )
    assert overwritten is False
    assert f'cover_source_url: "{COVER_URL}"' in fm_text
    env.download.assert_not_called()  # first sighting never downloads


def test_unchanged_cover_url_is_a_no_op(env):
    original = (
        "title: X\n"
        "cover: /assets/images/books/x.webp\n"
        f'cover_source_url: "{COVER_URL}"'
    )
    fm = {"cover_source_url": COVER_URL}
    pending = {}
    fm_text, overwritten = import_queue.cover_sync(
        env.session, COVER_URL, fm, original, pending
    )
    assert fm_text == original
    assert overwritten is False
    assert pending == {}
    env.download.assert_not_called()


def test_changed_cover_url_overwrites_image_and_dimensions(env):
    env.encode.return_value = (b"new-webp-bytes", 400, 250)
    fm = {
        "cover": "/assets/images/books/x.webp",
        "cover_source_url": "https://covers.example/old.jpg",
    }
    original = (
        "cover: /assets/images/books/x.webp\n"
        "cover_width: 200\n"
        "cover_height: 300\n"
        'cover_source_url: "https://covers.example/old.jpg"'
    )
    pending = {}

    fm_text, overwritten = import_queue.cover_sync(
        env.session, COVER_URL, fm, original, pending
    )

    assert overwritten is True
    assert f'cover_source_url: "{COVER_URL}"' in fm_text
    assert "cover_width: 400" in fm_text
    assert "cover_height: 250" in fm_text
    # The image is staged to overwrite the existing cover file in place.
    target = env.tmp_path / "assets" / "images" / "books" / "x.webp"
    assert pending[target] == b"new-webp-bytes"


def test_cover_download_failure_raises_without_writes(env):
    env.download.side_effect = import_queue.CoverDownloadError("boom")
    fm = {
        "cover": "/assets/images/books/x.webp",
        "cover_source_url": "https://covers.example/old.jpg",
    }
    pending = {}
    with pytest.raises(import_queue.CoverDownloadError):
        import_queue.cover_sync(env.session, COVER_URL, fm, "cover: x", pending)
    assert pending == {}


def test_cover_sync_without_cover_path_raises(env):
    fm = {"cover_source_url": "https://covers.example/old.jpg"}  # no cover field
    with pytest.raises(import_queue.CoverDownloadError, match="no cover path"):
        import_queue.cover_sync(env.session, COVER_URL, fm, "title: X", {})


# ----------------------------------------------------------- sync_unused_tags


def shelves(*entries: tuple[str, str, bool]) -> list[dict]:
    return [
        {"name": name, "slug": slug, "tag": tag}
        for name, slug, tag in entries
    ]


def test_unused_switched_on_shelves_get_waiting_room_docs(env):
    write_book(
        env.books,
        "a-book",
        {"title": "A Book", "author": "An Author", "tags": ["love"]},
    )
    created, deleted = import_queue.sync_unused_tags(
        shelves(
            ("Fiction", "fiction", True),  # switched on, unused -> create
            ("Love", "love", True),  # switched on, used -> no doc
            ("On Loan", "on-loan", False),  # switched off -> no doc
        )
    )
    assert created == ["fiction"]
    assert deleted == []
    assert (env.tags / "fiction.md").exists()
    assert not (env.tags / "love.md").exists()
    assert not (env.tags / "on-loan.md").exists()

    fm, _, _ = import_queue.split_front_matter(
        (env.tags / "fiction.md").read_text(encoding="utf-8")
    )
    assert fm == {"name": "Fiction", "slug": "fiction"}


def test_stale_and_newly_used_tag_docs_are_deleted(env):
    write_book(
        env.books,
        "a-book",
        {"title": "A Book", "author": "An Author", "tags": ["love"]},
    )
    write_book(env.tags, "love", {"name": "Love", "slug": "love"})
    write_book(env.tags, "stale", {"name": "Stale", "slug": "stale"})
    write_book(env.tags, "on-loan", {"name": "On Loan", "slug": "on-loan"})

    created, deleted = import_queue.sync_unused_tags(
        shelves(
            ("Love", "love", True),  # used now -> doc must go
            ("On Loan", "on-loan", False),  # switched off -> doc must go
            ("Fiction", "fiction", True),  # unused -> create
        )
    )

    assert created == ["fiction"]
    assert sorted(deleted) == ["love", "on-loan", "stale"]
    assert not (env.tags / "love.md").exists()
    assert not (env.tags / "stale.md").exists()
    assert not (env.tags / "on-loan.md").exists()
    assert (env.tags / "fiction.md").exists()


def test_sync_unused_tags_is_idempotent(env):
    write_book(
        env.books,
        "a-book",
        {"title": "A Book", "author": "An Author", "tags": ["love"]},
    )
    config = shelves(("Fiction", "fiction", True), ("Love", "love", True))
    first = import_queue.sync_unused_tags(config)
    second = import_queue.sync_unused_tags(config)
    assert first == (["fiction"], [])
    assert second == ([], [])
