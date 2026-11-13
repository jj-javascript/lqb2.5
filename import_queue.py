#!/usr/bin/env python3
"""Sveltia import queue: _import_queue/*.md -> Sveltia (_books, _tags, covers).

Lawrence pastes a Goodreads book URL into the "Import Queue" collection in
/admin. Sveltia commits a small file to _import_queue/; that push triggers
the book-import GitHub Action, which runs this script. No Goodreads login or
stored credentials are involved: all data comes from the PUBLIC book page
(goodreads.com/book/show/<id>), which remains anonymously accessible even
though Goodreads removed RSS and login-walled user shelves (Nov 2026).

Per queue item:
  1. Extract the numeric book id from the URL and fetch the public page.
  2. Parse JSON-LD (title, authors, full-size cover) and the embedded
     __NEXT_DATA__ Apollo state (description).
  3. Dedupe: normalized title + primary author matched against existing
     books. Already-present books are a no-op apart from the cover-source
     check and append-only tag merge.
  4. New book -> create entry (cover re-encoded to WebP, Goodreads URL,
     body = review pasted into the queue form (review: true) or else the
     public description, tag references from Lawrence's own shelves).
     A pasted review is also attached to an already-present book that has
     none; a book's existing review is never overwritten.
  5. Tags come from Lawrence's OWN Goodreads category shelves, not from
     community genres: the shelf list in _data/goodreads_shelves.yml
     (editable in /admin under Site Data as "Bookshelves") is auto-seeded
     each run from the shelves on his PUBLIC profile page (top shelves
     only; status shelves and special-books excluded). Each entry has a
     "Show on site" flag - uncheck it in /admin to hide a shelf (deleting
     the entry would just make discovery re-add it). Membership is then
     read anonymously via the public grid widget
     (review/grid_widget?shelf=<slug>), which - unlike the shelf pages and
     RSS - still works without login and honors the shelf parameter
     (verified Nov 2026; returns up to ~200 books per shelf). The
     bookshelf menu reads _data/goodreads_shelves.yml directly, showing
     each switched-on shelf that at least one library book carries. The
     _tags collection is a waiting room of the remaining switched-on
     shelves (ones no library book uses yet), auto-managed by this script:
     a tag doc is created for each unused switched-on shelf and deleted
     once the shelf is used by a book or switched off. Existing tag
     references on a book are never removed.
  6. On successful NEW import the queue file is deleted. If the book is
     already in the collection, or the URL is not a /book/show/ link, the
     queue file is KEPT and given a `status` note so the editor sees why
     in /admin (status-marked items are skipped on later runs). On
     transient failure it is left in place untouched so the item can be
     retried by a re-run; other items still process.

Idempotency: re-running with the same queue file, or re-submitting an
already-imported book, never duplicates books or tag documents. All writes
are staged and flushed together at the end of the run.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml  # PyYAML: parsing only; all writes are minimal text edits

from harvest import (
    BOOKS_DIR,
    COVERS_DIR,
    CoverDownloadError,
    HarvestError,
    download_cover,
    encode_cover_webp,
    make_session,
    norm_title,
    slugify,
    title_key,
    yaml_escape,
)

ROOT = Path(__file__).resolve().parent
TAGS_DIR = ROOT / "_tags"
QUEUE_DIR = ROOT / "_import_queue"
SHELVES_CONFIG = ROOT / "_data" / "goodreads_shelves.yml"

GOODREADS_USER_ID = "61877628"
PROFILE_URL = f"https://www.goodreads.com/user/show/{GOODREADS_USER_ID}"
# Never mirrored as tags: reading-status shelves plus special-books (which
# marks digital-library membership, not a category).
STATUS_SHELVES = {"read", "to-read", "currently-reading", "did-not-finish", "special-books"}
# Status shelves render as ?shelf= links, custom category shelves as ?tag=
# links (observed Nov 2026); the user id may carry a "-lawrence" suffix.
SHELF_LINK_RE = re.compile(
    rf"review/list/{GOODREADS_USER_ID}(?:-[A-Za-z0-9-]+)?\?(?:shelf|tag)=([A-Za-z0-9_-]+)"
)
BOOK_URL_RE = re.compile(r"goodreads\.com/book/show/(\d+)")
GRID_WIDGET_URL = (
    "https://www.goodreads.com/review/grid_widget/{user}.x"
    "?num_books=200&shelf={shelf}&widget_id=1"
)
# Cover URLs inside the widget embed the book id: .../books/1719002769l/212924187.jpg
GRID_BOOK_ID_RE = re.compile(r"books/\d+[a-z]?/(\d+)\.")


class QueueItemError(Exception):
    """This queue item failed; leave its file in place and continue."""


# ------------------------------------------------------------ book page


def fetch_book(session, book_id: str) -> dict:
    """Fetch and parse the public Goodreads book page."""
    url = f"https://www.goodreads.com/book/show/{book_id}"
    resp = session.get(url, timeout=60)
    if resp.status_code != 200:
        raise QueueItemError(f"book page HTTP {resp.status_code} for {url}")
    text = resp.text

    m = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', text, re.S
    )
    if not m:
        raise QueueItemError(f"no JSON-LD on {url} (page layout changed?)")
    ld = json.loads(m.group(1))
    title = ld.get("name", "")
    authors = [a.get("name", "") for a in ld.get("author", []) if a.get("name")]
    if not title or not authors:
        raise QueueItemError(f"JSON-LD missing title/author on {url}")

    description = ""
    m2 = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        text,
        re.S,
    )
    if m2:
        apollo = (
            json.loads(m2.group(1))
            .get("props", {})
            .get("pageProps", {})
            .get("apolloState", {})
        )
        books = [
            v
            for k, v in apollo.items()
            if k.startswith("Book:") and v.get("description")
        ]
        # Prefer the entry matching the JSON-LD title; related books on the
        # page can also be Book: entries.
        book = next(
            (b for b in books if b.get("title") and title.startswith(b["title"])),
            books[0] if books else None,
        )
        if book:
            description = book.get("description", "")

    return {
        "book_id": book_id,
        "title": title,
        "author": ", ".join(authors),
        "cover_url": ld.get("image", ""),
        "goodreads_url": url,
        "description": description,
    }


# ------------------------------------------------------- front matter I/O


def split_front_matter(text: str) -> tuple[dict, str, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise QueueItemError("file missing front matter block")
    return yaml.safe_load(m.group(1)) or {}, m.group(1), m.group(2)


def author_key(author: str) -> str:
    """Primary author only, normalized (matches harvest conventions)."""
    primary = re.split(r",|\s+with\s+|\s+&\s+|\s+and\s+", author, maxsplit=1)[0]
    return norm_title(primary)


def load_existing_books() -> dict[tuple[str, str], dict]:
    """Index existing books by (normalized title key, primary author key)."""
    index: dict[tuple[str, str], dict] = {}
    for path in sorted(BOOKS_DIR.glob("*.md")):
        try:
            fm, fm_text, body = split_front_matter(
                path.read_text(encoding="utf-8")
            )
        except (QueueItemError, yaml.YAMLError) as exc:
            print(f"  warning: skipping unparseable book {path.name}: {exc}")
            continue
        title, author = str(fm.get("title", "")), str(fm.get("author", ""))
        if not title or not author:
            print(f"  warning: {path.name} missing title/author; skipped")
            continue
        index[(title_key(title), author_key(author))] = {
            "path": path,
            "fm": fm,
            "fm_text": fm_text,
            "body": body,
        }
    return index


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


def write_doc(fm_text: str, body: str) -> str:
    return f"---\n{fm_text}\n---\n{body}"


def mark_queue_item(pending: dict[Path, str], path: Path, status: str) -> None:
    """Stage a status note onto a queue item instead of deleting it.

    The item stays visible in the /admin Import Queue with the status
    message, which is how the editor is notified (e.g. that the book is
    already in the collection). Items carrying a status are skipped on
    later runs; the editor deletes the entry once read.
    """
    _, fm_text, body = split_front_matter(path.read_text(encoding="utf-8"))
    pending[path] = write_doc(
        set_scalar(fm_text, "status", yaml_escape(status)), body
    )


# ---------------------------------------------------------------- tags


def load_shelf_config() -> list[dict]:
    """Lawrence's shelves from config: [{name, slug, tag}, ...].

    Entries with tag=false are known (so discovery won't re-add them) but
    are not mirrored as tags.
    """
    if not SHELVES_CONFIG.exists():
        return []
    data = yaml.safe_load(SHELVES_CONFIG.read_text(encoding="utf-8")) or {}
    shelves = []
    for entry in data.get("shelves") or []:
        slug = slugify(str(entry.get("slug") or entry.get("name") or ""))
        if slug:
            shelves.append(
                {
                    "name": str(entry.get("name") or slug),
                    "slug": slug,
                    "tag": bool(entry.get("tag", True)),
                }
            )
    return shelves


def discover_shelves(session) -> list[dict]:
    """Shelves on Lawrence's PUBLIC profile page (top shelves only, no login).

    Status shelves and special-books are excluded. Raises QueueItemError on
    fetch failure or if no shelves parse (page layout changed?).
    """
    resp = session.get(PROFILE_URL, timeout=60)
    if resp.status_code != 200:
        raise QueueItemError(f"profile page HTTP {resp.status_code}")
    slugs: list[str] = []
    for slug in SHELF_LINK_RE.findall(resp.text):
        s = slug.lower()
        if s not in STATUS_SHELVES and s not in slugs:
            slugs.append(s)
    if not slugs:
        raise QueueItemError("no shelves found on profile page (layout changed?)")
    return [
        {"name": s.replace("-", " ").title(), "slug": s, "tag": True}
        for s in slugs
    ]


def seed_shelf_config(shelves: list[dict], discovered: list[dict]) -> list[dict]:
    """Append newly discovered shelves to the config file; return new entries.

    The editor stops tagging with a shelf by unchecking "Mirror as tag" in
    /admin - NOT by deleting the entry, which would make discovery re-add it.
    Leading comment lines in the config file are preserved.
    """
    known = {s["slug"] for s in shelves}
    new = [d for d in discovered if d["slug"] not in known]
    if not new:
        return []
    lines: list[str] = []
    if SHELVES_CONFIG.exists():
        for line in SHELVES_CONFIG.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                lines.append(line)
            else:
                break
    lines.append("shelves:")
    for s in shelves + new:
        lines.append(f"  - name: {yaml_escape(s['name'])}")
        lines.append(f"    slug: {yaml_escape(s['slug'])}")
        lines.append(f"    tag: {'true' if s.get('tag', True) else 'false'}")
    SHELVES_CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return new


def fetch_shelf_members(session, shelf_slug: str) -> set[str]:
    """Book ids on one shelf, via the public grid widget (no login).

    An unknown/misspelled shelf returns an empty grid rather than an error,
    so an empty result is reported as a warning by the caller.
    """
    url = GRID_WIDGET_URL.format(user=GOODREADS_USER_ID, shelf=shelf_slug)
    resp = session.get(url, timeout=60)
    if resp.status_code != 200:
        raise QueueItemError(f"grid widget HTTP {resp.status_code} for {shelf_slug!r}")
    return set(GRID_BOOK_ID_RE.findall(resp.text))


def book_tags(
    book_id: str, shelf_members: dict[str, set[str]], shelves: list[dict]
) -> dict[str, str]:
    """Tags for a book = configured shelves whose membership contains it."""
    return {
        s["slug"]: s["name"]
        for s in shelves
        if book_id in shelf_members.get(s["slug"], set())
    }


def sync_unused_tags(shelves: list[dict]) -> tuple[list[str], list[str]]:
    """Sync _tags/ to hold exactly the switched-on shelves no book uses.

    The _tags collection is a waiting room: it lists shelves whose
    "Show on site" flag (tag=true in _data/goodreads_shelves.yml) is on
    but which no book in _books/ carries yet. (The bookshelf menu reads
    _data/goodreads_shelves.yml directly, filtered to shelves present on
    at least one book, so it does not need these docs.) A tag doc is
    created for each unused switched-on shelf, and any tag doc whose
    shelf became used - or was switched off - is deleted. Returns
    (created_slugs, deleted_slugs).
    """
    used: set[str] = set()
    if BOOKS_DIR.exists():
        for path in BOOKS_DIR.glob("*.md"):
            try:
                fm, _, _ = split_front_matter(path.read_text(encoding="utf-8"))
            except (QueueItemError, yaml.YAMLError):
                continue
            tags = fm.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            used.update(str(tag) for tag in tags)
    unused = {
        s["slug"]: s["name"]
        for s in shelves
        if s["tag"] and s["slug"] not in used
    }
    created: list[str] = []
    for slug, name in sorted(unused.items()):
        path = TAGS_DIR / f"{slug}.md"
        if path.exists():
            continue
        fm = f"name: {yaml_escape(name)}\nslug: {yaml_escape(slug)}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(write_doc(fm, ""), encoding="utf-8")
        created.append(slug)
        print(f"unused tag doc created: {slug!r}")
    deleted: list[str] = []
    if TAGS_DIR.exists():
        for path in sorted(TAGS_DIR.glob("*.md")):
            slug = path.stem
            try:
                fm, _, _ = split_front_matter(path.read_text(encoding="utf-8"))
                slug = str(fm.get("slug") or path.stem)
            except (QueueItemError, yaml.YAMLError):
                pass
            if slug not in unused:
                path.unlink(missing_ok=True)
                deleted.append(slug)
                print(f"tag doc deleted (no longer unused): {slug!r}")
    return created, deleted


# ---------------------------------------------------------------- main


def read_queue() -> tuple[list[tuple[Path, str, str]], list[tuple[Path, str]]]:
    """Split the queue into importable and permanently-invalid items.

    Returns (items, invalid): items are (queue file, goodreads book id,
    pasted review text or ""); invalid are (queue file, human-readable
    reason) for URLs that can never import (e.g. not a /book/show/ link) -
    these get a status note written back rather than being retried forever.
    Items that already carry a status from a previous run are skipped
    entirely.
    """
    items: list[tuple[Path, str, str]] = []
    invalid: list[tuple[Path, str]] = []
    if not QUEUE_DIR.exists():
        return items, invalid
    for path in sorted(QUEUE_DIR.glob("*.md")):
        try:
            fm, _, _ = split_front_matter(path.read_text(encoding="utf-8"))
        except (QueueItemError, yaml.YAMLError) as exc:
            print(f"  ! queue item kept for retry: {path.name}: {exc}")
            continue
        if fm.get("status"):
            continue  # already processed and marked on a previous run
        url = str(fm.get("goodreads", ""))
        m = BOOK_URL_RE.search(url)
        if not m:
            invalid.append(
                (
                    path,
                    "Error: not a Goodreads BOOK link - need"
                    f" goodreads.com/book/show/<id>, got: {url}",
                )
            )
            continue
        items.append((path, m.group(1), str(fm.get("review") or "").strip()))
    return items, invalid


def main() -> int:
    queue, invalid_items = read_queue()
    if not queue and not invalid_items:
        print("Import queue is empty. Nothing to do.")
        return 0
    print(f"{len(queue)} queue item(s), {len(invalid_items)} invalid")

    session = make_session()
    index = load_existing_books()
    print(f"{len(index)} existing book(s) in CMS")

    shelves = load_shelf_config()
    try:
        seeded = seed_shelf_config(shelves, discover_shelves(session))
        if seeded:
            shelves = load_shelf_config()  # reload including new entries
            for s in seeded:
                print(f"new shelf discovered, added to config: {s['slug']!r}")
    except QueueItemError as exc:
        seeded = []
        print(f"  ! shelf discovery skipped this run: {exc}")

    enabled_shelves = [s for s in shelves if s["tag"]]
    shelf_members: dict[str, set[str]] = {}
    for s in enabled_shelves:
        try:
            members = fetch_shelf_members(session, s["slug"])
            shelf_members[s["slug"]] = members
            note = "" if members else "  (empty - misspelled slug?)"
            print(f"shelf {s['slug']!r}: {len(members)} book(s){note}")
        except QueueItemError as exc:
            print(f"  ! shelf {s['slug']!r} skipped this run: {exc}")
    if not enabled_shelves:
        print("no shelves enabled for tagging; no tags will be added")

    pending: dict[Path, str] = {}  # staged text writes, flushed together
    pending_bin: dict[Path, bytes] = {}
    to_delete: list[Path] = []
    new_books: list[str] = []
    already_present: list[str] = []
    covers_updated: list[str] = []
    tags_appended: list[str] = []
    warnings: list[str] = []
    seen_keys: set[tuple[str, str]] = set()

    # Un-importable links: notify the editor via a status note on the entry.
    for path, reason in invalid_items:
        mark_queue_item(pending, path, reason)
        print(f"  ! {path.name}: {reason}")

    for queue_path, book_id, review_text in queue:
        try:
            book = fetch_book(session, book_id)
        except QueueItemError as exc:
            warnings.append(f"{queue_path.name}: {exc}")
            continue  # queue file left in place for retry

        key = (title_key(book["title"]), author_key(book["author"]))
        if key in seen_keys:
            to_delete.append(queue_path)  # duplicate submission in same run
            continue
        seen_keys.add(key)

        tags = book_tags(book_id, shelf_members, enabled_shelves)
        existing = index.get(key)

        if existing is None:
            # --- new book import
            slug = slugify(book["title"])
            if (BOOKS_DIR / f"{slug}.md").exists():
                slug = f"{slug}-{book_id}"
            cover_rel = f"/assets/images/books/{slug}.webp"
            width = height = 0
            if book["cover_url"]:
                try:
                    raw = download_cover(session, book["cover_url"])
                    webp, width, height = encode_cover_webp(raw)
                    pending_bin[COVERS_DIR / f"{slug}.webp"] = webp
                except CoverDownloadError as exc:
                    warnings.append(f"{book['title']}: cover skipped ({exc})")
            fm_lines = [
                f"title: {yaml_escape(book['title'])}",
                f"author: {yaml_escape(book['author'])}",
                f"cover: {yaml_escape(cover_rel)}",
                f"cover_width: {width or 200}",
                f"cover_height: {height or 300}",
                f"goodreads: {yaml_escape(book['goodreads_url'])}",
                f"review: {'true' if review_text else 'false'}",
                "sample: false",
            ]
            if tags:
                fm_lines.append("tags:")
                fm_lines.extend(f"  - {s}" for s in sorted(tags))
            if book["cover_url"]:
                fm_lines.append(
                    f"cover_source_url: {yaml_escape(book['cover_url'])}"
                )
            # Pasted review takes precedence over the public description.
            body_text = review_text or book["description"]
            body = f"\n{body_text}\n" if body_text else ""
            pending[BOOKS_DIR / f"{slug}.md"] = write_doc(
                "\n".join(fm_lines), body
            )
            new_books.append(book["title"])
            to_delete.append(queue_path)
            continue

        # --- existing book: cover-source check, then append-only tags
        path: Path = existing["path"]
        fm, fm_text, body = existing["fm"], existing["fm_text"], existing["body"]

        stored_cover = str(fm.get("cover_source_url") or "")
        if book["cover_url"] and stored_cover != book["cover_url"]:
            if not stored_cover:
                # First observation: record the source without overwriting.
                fm_text = set_scalar(
                    fm_text, "cover_source_url", yaml_escape(book["cover_url"])
                )
            else:
                try:
                    raw = download_cover(session, book["cover_url"])
                    webp, width, height = encode_cover_webp(raw)
                    cover_rel = str(fm.get("cover") or "").lstrip("/")
                    if not cover_rel:
                        raise CoverDownloadError("book has no cover path")
                    pending_bin[ROOT / cover_rel] = webp  # overwrite in place
                    fm_text = set_scalar(
                        fm_text, "cover_source_url", yaml_escape(book["cover_url"])
                    )
                    fm_text = set_scalar(fm_text, "cover_width", str(width))
                    fm_text = set_scalar(fm_text, "cover_height", str(height))
                    covers_updated.append(book["title"])
                except CoverDownloadError as exc:
                    warnings.append(
                        f"{book['title']}: cover update skipped ({exc})"
                    )

        current_tags = fm.get("tags") or []
        if isinstance(current_tags, str):
            current_tags = [current_tags]
        merged = sorted(set(current_tags) | set(tags))
        if merged != sorted(current_tags):
            fm_text = set_list(fm_text, "tags", merged)
            tags_appended.append(book["title"])

        # Pasted review: attach it unless the book already has one.
        status_extra = ""
        if review_text and fm.get("review"):
            status_extra = " - existing review kept (edit the book in /admin to change it)"
        elif review_text:
            fm_text = set_scalar(fm_text, "review", "true")
            body = f"\n{review_text}\n"
            status_extra = " - your review was added"

        new_text = write_doc(fm_text, body)
        if new_text != path.read_text(encoding="utf-8"):
            pending[path] = new_text
        # Notify the editor instead of silently consuming the queue item.
        mark_queue_item(
            pending,
            queue_path,
            f"Already in collection: {book['title']} by {book['author']}"
            + status_extra,
        )
        already_present.append(book["title"] + status_extra)

    # --- flush: all writes land together, only after every item processed
    for path, text in pending.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for path, data in pending_bin.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for path in to_delete:
        path.unlink(missing_ok=True)

    # _tags waiting room: reflect the now-current book tags on disk.
    created_unused, deleted_unused = sync_unused_tags(shelves)

    print("\nRun summary")
    print("-----------")
    print(f"New books imported:   {len(new_books)}")
    for t in new_books:
        print(f"  + {t}")
    print(f"Already in collection: {len(already_present)}")
    for t in already_present:
        print(f"  = {t} (queue entry marked with a status note)")
    print(f"Invalid queue links:  {len(invalid_items)}")
    for path, reason in invalid_items:
        print(f"  ! {path.name}")
    print(f"Covers overwritten:   {len(covers_updated)}")
    for t in covers_updated:
        print(f"  ~ {t}")
    print(f"Shelves auto-seeded:  {len(seeded)}")
    for s in seeded:
        print(f"  + {s['slug']}")
    print(
        f"Unused tag docs:      created {len(created_unused)},"
        f" deleted {len(deleted_unused)}"
    )
    for s in created_unused:
        print(f"  + {s}")
    for s in deleted_unused:
        print(f"  - {s}")
    print(f"Books w/ tags added:  {len(tags_appended)}")
    for t in tags_appended:
        print(f"  ~ {t}")
    print(f"Queue items consumed: {len(to_delete)}")
    if warnings:
        print("Warnings (queue files kept for retry):")
        for w in warnings:
            print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarvestError as exc:
        print(f"IMPORT FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
