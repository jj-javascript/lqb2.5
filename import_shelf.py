#!/usr/bin/env python3
"""Event-driven import: Goodreads #special-books RSS -> Sveltia (_books, _tags, covers).

Triggered by the RSS-to-webhook bridge (IFTTT/Zapier) via GitHub Actions
repository_dispatch; also runnable manually (workflow_dispatch or locally).

Every run re-fetches the full shelf feed and diffs it against the CMS, so the
bridge payload is ignored and duplicate/late bridge events are harmless no-ops.

Per run, for each feed item:
  1. Dedupe: normalized title + primary author matched against existing books.
  2. New book -> create entry (cover, Goodreads URL, summary body).
  3. Existing book -> if the Goodreads cover source URL changed, overwrite the
     cover image in place (same file path).
  4. Both paths -> map the item's other shelves to Sveltia `tag` documents
     (created only if missing) and append missing tag references to the book.
     Existing references are never removed or reordered.

Failure policy: an unreachable or malformed feed aborts the run BEFORE any
write; all staged writes land together at the end so a failure can never leave
a book referencing a tag document that was never created.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import yaml  # PyYAML: parsing only; all writes are minimal text edits

from harvest import (
    COVERS_DIR,
    BOOKS_DIR,
    CoverDownloadError,
    download_cover,
    encode_cover_webp,
    make_session,
    norm_title,
    parse_rss_item,
    pick_summary,
    slugify,
    title_key,
    yaml_escape,
)

ROOT = Path(__file__).resolve().parent
TAGS_DIR = ROOT / "_tags"

GOODREADS_USER_ID = "61877628"
SHELF = os.environ.get("GOODREADS_SHELF", "special-books")
RSS_URL = (
    f"https://www.goodreads.com/review/list_rss/{GOODREADS_USER_ID}"
    f"?shelf={quote(SHELF)}"
)
PAGE_SIZE = 100
INTER_PAGE_DELAY = 2.0

# Shelves that never become tag documents: the ingest shelf itself and
# Goodreads' built-in shelves.
EXCLUDED_TAG_SLUGS = {
    slugify(SHELF),
    "read",
    "currently-reading",
    "to-read",
}


class ShelfImportError(Exception):
    """Fatal run failure; abort before any write."""


# ---------------------------------------------------------------- feed


def parse_shelves(item_xml: str) -> list[str]:
    """Extract the item's other Goodreads shelves from <user_shelves>."""
    m = re.search(
        r"<user_shelves>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</user_shelves>",
        item_xml,
        re.S,
    )
    if not m:
        return []
    return [s.strip() for s in m.group(1).split(",") if s.strip()]


def fetch_feed(session) -> list[dict]:
    """Fetch and validate the full shelf feed. Raises before any write on
    unavailable or malformed data; an empty-but-valid shelf is a no-op."""
    items: list[dict] = []
    page = 1
    while True:
        resp = session.get(f"{RSS_URL}&page={page}", timeout=60)
        try:
            resp.raise_for_status()
        except Exception as exc:
            raise ShelfImportError(f"RSS fetch failed (page {page}): {exc}")
        text = resp.text
        if "<rss" not in text or "<channel>" not in text:
            raise ShelfImportError(
                f"Malformed feed on page {page}: not an RSS document "
                f"(shelf {SHELF!r} may not exist yet)."
            )
        chunks = re.findall(r"<item>.*?</item>", text, re.S)
        for chunk in chunks:
            book = parse_rss_item(chunk)
            if not book.title or not book.author:
                raise ShelfImportError(
                    f"Malformed feed item on page {page}: missing title/author."
                )
            items.append({"book": book, "shelves": parse_shelves(chunk)})
        if len(chunks) < PAGE_SIZE:
            break
        page += 1
        time.sleep(INTER_PAGE_DELAY)
    return items


# ------------------------------------------------------- front matter I/O


def split_front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise ShelfImportError("Book file missing front matter block.")
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def load_existing_books() -> dict[tuple[str, str], dict]:
    """Index existing books by (normalized title key, primary author key)."""
    index: dict[tuple[str, str], dict] = {}
    for path in sorted(BOOKS_DIR.glob("*.md")):
        try:
            fm, body = split_front_matter(path.read_text(encoding="utf-8"))
        except ShelfImportError as exc:
            print(f"  warning: skipping unparseable book {path.name}: {exc}")
            continue
        title, author = str(fm.get("title", "")), str(fm.get("author", ""))
        if not title or not author:
            print(f"  warning: {path.name} missing title/author; skipped")
            continue
        index[(title_key(title), author_key(author))] = {
            "path": path,
            "fm": fm,
            "body": body,
        }
    return index


def author_key(author: str) -> str:
    """Primary author only, normalized (matches harvest conventions)."""
    primary = re.split(r",|\s+with\s+|\s+&\s+|\s+and\s+", author, maxsplit=1)[0]
    return norm_title(primary)


def set_scalar(fm_text: str, key: str, rendered_value: str) -> str:
    """Replace or append a scalar front matter line (minimal-diff edit)."""
    line = f"{key}: {rendered_value}"
    pattern = rf"^{re.escape(key)}:.*$"
    if re.search(pattern, fm_text, re.M):
        return re.sub(pattern, line, fm_text, count=1, flags=re.M)
    return fm_text + "\n" + line if fm_text else line


def set_list(fm_text: str, key: str, values: list[str]) -> str:
    """Replace or append a block-style list field (minimal-diff edit)."""
    block = key + ":\n" + "\n".join(f"  - {v}" for v in values)
    pattern = rf"^{re.escape(key)}:[^\n]*(?:\n[ \t]+-[^\n]*)*"
    if re.search(pattern, fm_text, re.M):
        return re.sub(pattern, block, fm_text, count=1, flags=re.M)
    return fm_text + "\n" + block if fm_text else block


def fm_to_text(fm_lines: list[str]) -> str:
    return "\n".join(fm_lines)


def write_book_file(fm_text: str, body: str) -> str:
    return f"---\n{fm_text}\n---\n{body}"


# ---------------------------------------------------------------- tags


def tag_slugs(shelves: list[str]) -> dict[str, str]:
    """Map raw shelf names to {slug: display name}, minus excluded shelves."""
    out: dict[str, str] = {}
    for name in shelves:
        slug = slugify(name)
        if slug and slug not in EXCLUDED_TAG_SLUGS:
            out.setdefault(slug, name)
    return out


def stage_tag_docs(
    pending: dict[Path, str], needed: dict[str, str]
) -> list[str]:
    """Stage creation of tag documents that do not already exist."""
    created = []
    for slug, name in sorted(needed.items()):
        path = TAGS_DIR / f"{slug}.md"
        if path.exists() or path in pending:
            continue
        fm = fm_to_text(
            [f"name: {yaml_escape(name)}", f"slug: {yaml_escape(slug)}"]
        )
        pending[path] = write_book_file(fm, "")
        created.append(slug)
    return created


# ---------------------------------------------------------------- main


def main() -> int:
    session = make_session()
    print(f"Fetching Goodreads RSS shelf {SHELF!r}...")
    items = fetch_feed(session)
    print(f"  {len(items)} feed item(s)")

    index = load_existing_books()
    print(f"  {len(index)} existing book(s) in CMS")

    pending: dict[Path, str] = {}  # path -> text, flushed together at the end
    pending_bin: dict[Path, bytes] = {}
    new_books: list[str] = []
    covers_updated: list[str] = []
    tags_created: list[str] = []
    tags_appended: list[str] = []
    warnings: list[str] = []
    needed_tags: dict[str, str] = {}
    seen_keys: set[tuple[str, str]] = set()

    for item in items:
        book, shelves = item["book"], item["shelves"]
        key = (title_key(book.title), author_key(book.author))
        if key in seen_keys:
            continue  # duplicate feed item within the same run
        seen_keys.add(key)
        tags = tag_slugs(shelves)
        needed_tags.update(tags)
        existing = index.get(key)

        if existing is None:
            # --- new book import
            slug = book.slug
            if (BOOKS_DIR / f"{slug}.md").exists() or any(
                p.name == f"{slug}.md" and p.parent == BOOKS_DIR for p in pending
            ):
                slug = f"{slug}-{book.book_id or 'import'}"
            cover_rel = f"/assets/images/books/{slug}.webp"
            width = height = 0
            if book.cover_url:
                try:
                    raw = download_cover(session, book.cover_url)
                    webp, width, height = encode_cover_webp(raw)
                    pending_bin[COVERS_DIR / f"{slug}.webp"] = webp
                except CoverDownloadError as exc:
                    warnings.append(f"{book.title}: cover skipped ({exc})")
            fm_lines = [
                f"title: {yaml_escape(book.title)}",
                f"author: {yaml_escape(book.author)}",
                f"cover: {yaml_escape(cover_rel)}",
                f"cover_width: {width or 200}",
                f"cover_height: {height or 300}",
            ]
            if book.goodreads_url:
                fm_lines.append(f"goodreads: {yaml_escape(book.goodreads_url)}")
            fm_lines.append("review: false")
            yr = book.year_read()
            if yr:
                fm_lines.append(f"year_read: {yr}")
            fm_lines.append("sample: false")
            if tags:
                fm_lines.append("tags:")
                fm_lines.extend(f"  - {s}" for s in sorted(tags))
            if book.cover_url:
                fm_lines.append(
                    f"cover_source_url: {yaml_escape(book.cover_url)}"
                )
            body = "\n" + pick_summary(book) + "\n" if pick_summary(book) else ""
            pending[BOOKS_DIR / f"{slug}.md"] = write_book_file(
                fm_to_text(fm_lines), body
            )
            new_books.append(book.title)
            continue

        # --- existing book: cover check, then append-only tags
        path: Path = existing["path"]
        text = (
            pending.get(path)
            if path in pending
            else path.read_text(encoding="utf-8")
        )
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
        fm_text, body = m.group(1), m.group(2)
        fm = existing["fm"]

        stored_cover = str(fm.get("cover_source_url") or "")
        if book.cover_url and stored_cover != book.cover_url:
            if not stored_cover:
                # First observation: record the source without overwriting.
                fm_text = set_scalar(
                    fm_text, "cover_source_url", yaml_escape(book.cover_url)
                )
            else:
                try:
                    raw = download_cover(session, book.cover_url)
                    webp, width, height = encode_cover_webp(raw)
                    cover_rel = str(fm.get("cover") or "").lstrip("/")
                    if not cover_rel:
                        raise CoverDownloadError("book has no cover path")
                    pending_bin[ROOT / cover_rel] = webp  # overwrite in place
                    fm_text = set_scalar(
                        fm_text, "cover_source_url", yaml_escape(book.cover_url)
                    )
                    fm_text = set_scalar(fm_text, "cover_width", str(width))
                    fm_text = set_scalar(fm_text, "cover_height", str(height))
                    covers_updated.append(book.title)
                except CoverDownloadError as exc:
                    warnings.append(f"{book.title}: cover update skipped ({exc})")

        current_tags = fm.get("tags") or []
        if isinstance(current_tags, str):
            current_tags = [current_tags]
        merged = sorted(set(current_tags) | set(tags))
        if merged != sorted(current_tags):
            fm_text = set_list(fm_text, "tags", merged)
            tags_appended.append(book.title)

        new_text = write_book_file(fm_text, body)
        if new_text != text:
            pending[path] = new_text

    created = stage_tag_docs(pending, needed_tags)
    tags_created.extend(created)

    # --- flush: all writes land together, only after every item processed
    for path, text in pending.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for path, data in pending_bin.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    print("\nRun summary")
    print("-----------")
    print(f"New books imported:   {len(new_books)}")
    for t in new_books:
        print(f"  + {t}")
    print(f"Covers overwritten:   {len(covers_updated)}")
    for t in covers_updated:
        print(f"  ~ {t}")
    print(f"Tag docs created:     {len(tags_created)}")
    for s in tags_created:
        print(f"  + {s}")
    print(f"Books w/ tags added:  {len(tags_appended)}")
    for t in tags_appended:
        print(f"  ~ {t}")
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  ! {w}")
    if not pending and not pending_bin:
        print("No changes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ShelfImportError as exc:
        print(f"SHELF IMPORT SKIPPED: {exc}", file=sys.stderr)
        raise SystemExit(1)
