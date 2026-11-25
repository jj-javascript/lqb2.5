"""Test doubles and canned-Goodreads-HTML builders for import_queue tests.

No test ever touches the network: import_queue.make_session is monkeypatched
to return a FakeSession, and download_cover/encode_cover_webp are replaced
with mocks. All filesystem paths (books, tags, queue, shelves config, and
the ROOT used to resolve cover overwrite paths) point at tmp_path.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import import_queue  # noqa: E402


# ------------------------------------------------------------ canned HTML


def book_page_html(
    book_id: str,
    title: str = "Test Book",
    author: str = "Test Author",
    description: str = "A public description.",
    cover_url: str | None = None,
) -> str:
    """A minimal Goodreads book page: JSON-LD + __NEXT_DATA__ apollo state."""
    cover_url = cover_url or f"https://covers.example/{book_id}.jpg"
    ld = json.dumps(
        {
            "name": title,
            "author": [{"name": name} for name in author.split(", ")],
            "image": cover_url,
        }
    )
    next_data = json.dumps(
        {
            "props": {
                "pageProps": {
                    "apolloState": {
                        "Book:1": {"title": title, "description": description}
                    }
                }
            }
        }
    )
    return (
        "<html><head>"
        f'<script type="application/ld+json">{ld}</script>'
        f'<script id="__NEXT_DATA__" type="application/json">{next_data}</script>'
        "</head><body></body></html>"
    )


def profile_html(slugs: list[str]) -> str:
    """A public profile page exposing the given shelves."""
    links = "".join(
        f'<a href="review/list/61877628-lawrence?shelf={slug}">{slug}</a>'
        for slug in slugs
    )
    return f"<html><body>{links}</body></html>"


def grid_html(book_ids: set[str]) -> str:
    """A grid widget page whose cover URLs embed the given book ids."""
    imgs = "".join(
        '<img src="https://i.gr-assets.com/images/S/compressed.photo'
        f'.goodreads.com/books/1719002769l/{bid}.jpg">'
        for bid in sorted(book_ids)
    )
    return f"<html><body>{imgs}</body></html>"


# ------------------------------------------------------------ fake session


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers: dict[str, str] = {}


class FakeSession:
    """Routes the three Goodreads URL shapes import_queue fetches.

    book_pages:    book_id -> html, or (status, html)
    profile_html:  html, or (status, html)
    grid_members:  shelf slug -> set of book ids, or (status, set)
    """

    def __init__(
        self,
        book_pages: dict | None = None,
        profile: object = "",
        grid_members: dict | None = None,
    ):
        self.book_pages = book_pages or {}
        self.profile = profile
        self.grid_members = grid_members or {}
        self.requested: list[str] = []

    def get(self, url: str, timeout: int = 60) -> FakeResponse:
        self.requested.append(url)
        if "/book/show/" in url:
            book_id = re.search(r"/book/show/(\d+)", url).group(1)
            entry = self.book_pages.get(book_id)
            if entry is None:
                return FakeResponse(404, "not found")
            if isinstance(entry, tuple):
                return FakeResponse(entry[0], entry[1])
            return FakeResponse(200, entry)
        if "grid_widget" in url:
            slug = re.search(r"shelf=([A-Za-z0-9_-]+)", url).group(1)
            entry = self.grid_members.get(slug, set())
            if isinstance(entry, tuple):
                return FakeResponse(entry[0], "")
            return FakeResponse(200, grid_html(entry))
        if "/user/show/" in url:
            if isinstance(self.profile, tuple):
                return FakeResponse(self.profile[0], self.profile[1])
            return FakeResponse(200, self.profile)
        return FakeResponse(404, f"unmapped url: {url}")


# ------------------------------------------------------------ file writers


def write_shelves_config(path: Path, shelves: list[tuple[str, str, bool]]) -> None:
    """shelves: (name, slug, tag) triples, mirroring goodreads_shelves.yml."""
    lines = ["shelves:"]
    for name, slug, tag in shelves:
        lines.append(f"  - name: {name}")
        lines.append(f"  - slug: {slug}")
        lines.append(f"    tag: {'true' if tag else 'false'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_doc(path: Path, front_matter: dict, body: str = "") -> Path:
    text = (
        "---\n"
        + yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=True)
        + "---\n"
        + body
    )
    path.write_text(text, encoding="utf-8")
    return path


def write_queue_item(
    queue_dir: Path,
    name: str,
    front_matter: dict,
    body: str = "",
) -> Path:
    return write_doc(queue_dir / name, front_matter, body)


def write_book(books_dir: Path, slug: str, front_matter: dict, body: str = "") -> Path:
    return write_doc(books_dir / f"{slug}.md", front_matter, body)


def read_book(path: Path) -> tuple[dict, str]:
    """(front matter dict, body) for a book file."""
    fm, _, body = import_queue.split_front_matter(path.read_text(encoding="utf-8"))
    return fm, body


# ------------------------------------------------------------ environment


@pytest.fixture
def env(tmp_path, monkeypatch) -> SimpleNamespace:
    """An isolated import_queue environment rooted at tmp_path."""
    books = tmp_path / "_books"
    tags = tmp_path / "_tags"
    queue = tmp_path / "_import_queue"
    covers = tmp_path / "covers"
    for d in (books, tags, queue, covers):
        d.mkdir()
    shelves_yml = tmp_path / "goodreads_shelves.yml"

    monkeypatch.setattr(import_queue, "ROOT", tmp_path)
    monkeypatch.setattr(import_queue, "BOOKS_DIR", books)
    monkeypatch.setattr(import_queue, "TAGS_DIR", tags)
    monkeypatch.setattr(import_queue, "QUEUE_DIR", queue)
    monkeypatch.setattr(import_queue, "COVERS_DIR", covers)
    monkeypatch.setattr(import_queue, "SHELVES_CONFIG", shelves_yml)
    monkeypatch.setattr(import_queue, "INTER_PAGE_DELAY", 0)

    session = FakeSession()
    monkeypatch.setattr(import_queue, "make_session", lambda: session)
    download = MagicMock(return_value=b"raw-image-bytes")
    encode = MagicMock(return_value=(b"webp-bytes", 200, 300))
    monkeypatch.setattr(import_queue, "download_cover", download)
    monkeypatch.setattr(import_queue, "encode_cover_webp", encode)

    return SimpleNamespace(
        tmp_path=tmp_path,
        books=books,
        tags=tags,
        queue=queue,
        covers=covers,
        shelves_yml=shelves_yml,
        session=session,
        download=download,
        encode=encode,
    )
