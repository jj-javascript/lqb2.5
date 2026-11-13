#!/usr/bin/env python3
"""One-time harvest: Goodreads RSS + libraryviz + highlights.md -> _books/*.md + covers."""

from __future__ import annotations

import html
import io
import re
import sys
import time
import unicodedata
import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
BOOKS_DIR = ROOT / "_books"
COVERS_DIR = ROOT / "assets" / "images" / "books"
REPORT_PATH = ROOT / "harvest-report.txt"
LIBRARYVIZ_PATH = ROOT / "_libraryviz" / "index.html"
HIGHLIGHTS_PATH = ROOT / "highlights.md"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
RSS_BASE = "https://www.goodreads.com/review/list_rss/61877628?shelf=read"
EXPECTED_SHELF_SIZE = 338
SHELF_DRIFT_TOLERANCE = 0.10
PAGE_SIZE = 100
INTER_PAGE_DELAY = 2.0
COVER_DISPLAY_WIDTH = 300
COVER_WEBP_QUALITY = 78

ORPHANS = [
    {
        "title": "Wild Seed",
        "author": "Octavia E. Butler",
        "goodreads_id": "52318",
        "goodreads": "https://www.goodreads.com/book/show/52318-wild-seed",
    },
    {
        "title": "Mind of My Mind",
        "author": "Octavia E. Butler",
        "goodreads_id": "116254",
        "goodreads": "https://www.goodreads.com/book/show/116254-mind-of-my-mind",
    },
    {
        "title": "Jackson Rising: The Struggle for Economic Democracy and Black Self-Determination in Jackson, Mississippi",
        "author": "Kali Akuno",
        "goodreads_id": None,
        "goodreads": None,
    },
    {
        "title": "Power-Curve Society: The Future of Innovation, Opportunity and Social Equity in the Emerging Networked Economy",
        "author": "David Bollier",
        "goodreads_id": None,
        "goodreads": None,
    },
]


class HarvestError(Exception):
    """Base harvest failure."""


class TruncatedPageError(HarvestError):
    """RSS page returned fewer items than expected."""


class ShelfSizeDriftError(HarvestError):
    """Total shelf size diverged from last known good."""


class CoverDownloadError(HarvestError):
    """Cover image download failed or size mismatch."""


class AmbiguousMatchError(HarvestError):
    """Fuzzy match could not be resolved uniquely."""


@dataclass
class Book:
    book_id: str
    title: str
    author: str
    review: str = ""
    description: str = ""
    cover_url: str = ""
    goodreads_url: str = ""
    read_at: str = ""
    rating: str = ""
    highlights_url: str = ""
    source: str = "rss"
    slug: str = ""
    cover_path: str = ""
    cover_width: int = 0
    cover_height: int = 0

    def year_read(self) -> Optional[int]:
        if not self.read_at:
            return None
        m = re.search(r"\b(19|20)\d{2}\b", self.read_at)
        return int(m.group(0)) if m else None


@dataclass
class MatchRecord:
    highlight_text: str
    matched_title: str
    book_id: str
    score: float
    method: str
    url: str


@dataclass
class HarvestReport:
    page_counts: list[int] = field(default_factory=list)
    libraryviz_ids: list[str] = field(default_factory=list)
    libraryviz_matched: list[str] = field(default_factory=list)
    libraryviz_missing: list[str] = field(default_factory=list)
    highlight_matches: list[MatchRecord] = field(default_factory=list)
    highlight_unmatched: list[str] = field(default_factory=list)
    duplicate_highlights: list[str] = field(default_factory=list)
    core_ids: set[str] = field(default_factory=set)
    orphan_titles: list[str] = field(default_factory=list)
    cover_failures: list[str] = field(default_factory=list)


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", title)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:80] or "book"


def norm_title(text: str) -> str:
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    for old, new in [
        ("\u2019", "'"),
        ("\u2018", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2014", "-"),
        ("\u2013", "-"),
        ("&", "and"),
    ]:
        s = s.replace(old, new)
    return re.sub(r"\s+", " ", s).strip().lower()


def strip_author(text: str) -> str:
    return re.sub(
        r"\s+(?:—|-)?\s*(?:by|edited by|with)\s+[^:]*$",
        "",
        text,
        flags=re.IGNORECASE,
    )


def title_key(text: str) -> str:
    t = norm_title(strip_author(text))
    t = re.split(r"[:\-–—(]", t)[0]
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def parse_rss_item(item_xml: str) -> Book:
    def field(tag: str) -> str:
        m = re.search(
            rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>",
            item_xml,
            re.S,
        )
        return html.unescape(m.group(1)).strip() if m else ""

    book_id = field("book_id")
    title = field("title")
    return Book(
        book_id=book_id,
        title=title,
        author=field("author_name"),
        review=field("user_review"),
        description=field("book_description"),
        cover_url=field("book_large_image_url"),
        goodreads_url=field("link"),
        read_at=field("user_read_at"),
        rating=field("user_rating"),
        slug=slugify(title),
    )


def fetch_rss_pages(session: requests.Session, report: HarvestReport) -> dict[str, Book]:
    feed: dict[str, Book] = {}
    page = 1
    while True:
        url = f"{RSS_BASE}&page={page}"
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        items = re.findall(r"<item>.*?</item>", resp.text, re.S)
        count = len(items)
        report.page_counts.append(count)

        if count == 0:
            break

        if count < PAGE_SIZE and page > 1:
            # Last page can be partial; page 1 must be full if more pages follow.
            pass
        elif page == 1 and count < PAGE_SIZE:
            raise TruncatedPageError(
                f"Page 1 returned {count} items; expected {PAGE_SIZE} or explicit end."
            )
        elif count < PAGE_SIZE:
            # Final page: OK
            pass
        elif count != PAGE_SIZE:
            raise TruncatedPageError(
                f"Page {page} returned {count} items; expected exactly {PAGE_SIZE}."
            )

        for item_xml in items:
            book = parse_rss_item(item_xml)
            if book.book_id:
                feed[book.book_id] = book

        if count < PAGE_SIZE:
            break

        page += 1
        time.sleep(INTER_PAGE_DELAY)

    low = int(EXPECTED_SHELF_SIZE * (1 - SHELF_DRIFT_TOLERANCE))
    high = int(EXPECTED_SHELF_SIZE * (1 + SHELF_DRIFT_TOLERANCE))
    total = len(feed)
    if not (low <= total <= high):
        raise ShelfSizeDriftError(
            f"Shelf size {total} outside expected range [{low}, {high}] "
            f"(baseline {EXPECTED_SHELF_SIZE})."
        )
    return feed


def parse_libraryviz(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        raise HarvestError(f"Missing libraryviz file: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    pairs = re.findall(
        r'href="https://www\.goodreads\.com/book/show/(\d+)[^"]*"[^>]*>\s*<img[^>]*alt="([^"]*)"',
        text,
    )
    if not pairs:
        pairs = []
        ids = re.findall(r'href="https://www\.goodreads\.com/book/show/(\d+)', text)
        alts = [html.unescape(a) for a in re.findall(r'alt="([^"]*)"', text)]
        for i, bid in enumerate(ids):
            alt = alts[i] if i < len(alts) else ""
            pairs.append((bid, alt))
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for bid, alt in pairs:
        if bid in seen:
            continue
        seen.add(bid)
        out.append((bid, html.unescape(alt)))
    return out


def parse_highlights(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    entries = re.findall(
        r"\*\s+\[([^\]]+)\]\((https?://[^)\s]+)\s*\)",
        text,
        re.S,
    )
    return [(t.strip(), u.strip()) for t, u in entries]


def fuzzy_match_highlights(
    entries: list[tuple[str, str]],
    feed: dict[str, Book],
    report: HarvestReport,
) -> dict[str, str]:
    by_key: dict[str, Book] = {}
    for book in feed.values():
        by_key.setdefault(title_key(book.title), book)
    keys = list(by_key.keys())
    mapping: dict[str, str] = {}
    seen_titles: dict[str, str] = {}

    for text, url in entries:
        k = title_key(text)
        if text in seen_titles:
            report.duplicate_highlights.append(
                f"Duplicate: {text!r} (kept {seen_titles[text]})"
            )
            continue
        seen_titles[text] = url

        book: Optional[Book] = by_key.get(k)
        method = "exact"
        score = 1.0

        if not book:
            close = difflib.get_close_matches(k, keys, n=3, cutoff=0.75)
            if len(close) > 1:
                scores = [
                    (c, difflib.SequenceMatcher(None, k, c).ratio()) for c in close
                ]
                scores.sort(key=lambda x: -x[1])
                if scores[0][1] - scores[1][1] < 0.05:
                    report.highlight_unmatched.append(
                        f"Ambiguous: {text!r} -> {close}"
                    )
                    continue
                book = by_key[scores[0][0]]
                method = "fuzzy"
                score = scores[0][1]
            elif len(close) == 1:
                book = by_key[close[0]]
                method = "fuzzy"
                score = difflib.SequenceMatcher(None, k, close[0]).ratio()
            else:
                report.highlight_unmatched.append(text)
                continue

        mapping[book.book_id] = url
        report.highlight_matches.append(
            MatchRecord(
                highlight_text=text,
                matched_title=book.title,
                book_id=book.book_id,
                score=score,
                method=method,
                url=url,
            )
        )
    return mapping


def fetch_open_library(session: requests.Session, title: str, author: str) -> dict:
    params = {"title": title, "author": author, "limit": 3}
    resp = session.get("https://openlibrary.org/search.json", params=params, timeout=30)
    resp.raise_for_status()
    docs = resp.json().get("docs", [])
    if not docs:
        params = {"q": f"{title} {author}", "limit": 3}
        resp = session.get("https://openlibrary.org/search.json", params=params, timeout=30)
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
    if not docs:
        raise HarvestError(f"Open Library: no match for {title!r} by {author!r}")
    doc = docs[0]
    cover_i = doc.get("cover_i")
    cover_url = (
        f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg" if cover_i else ""
    )
    return {
        "title": doc.get("title", title),
        "author": (doc.get("author_name") or [author])[0],
        "description": "",
        "cover_url": cover_url,
        "goodreads_url": "",
        "book_id": f"ol-{doc.get('key', '').replace('/works/', '')}",
    }


def build_orphan_books(session: requests.Session, report: HarvestReport) -> list[Book]:
    books: list[Book] = []
    for spec in ORPHANS:
        report.orphan_titles.append(spec["title"])
        if spec.get("goodreads_id"):
            book = Book(
                book_id=spec["goodreads_id"],
                title=spec["title"],
                author=spec["author"],
                goodreads_url=spec["goodreads"],
                source="orphan-goodreads",
                slug=slugify(spec["title"]),
            )
        else:
            ol = fetch_open_library(session, spec["title"], spec["author"])
            book = Book(
                book_id=ol["book_id"],
                title=ol["title"],
                author=ol["author"],
                description=ol.get("description", ""),
                cover_url=ol.get("cover_url", ""),
                goodreads_url=ol.get("goodreads_url", ""),
                source="orphan-openlibrary",
                slug=slugify(spec["title"]),
            )
        books.append(book)
    return books


def download_cover(session: requests.Session, url: str) -> bytes:
    if not url:
        raise CoverDownloadError("Empty cover URL")
    expected_len: Optional[int] = None
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            data = resp.content
            cl = resp.headers.get("Content-Length")
            if cl is not None:
                expected_len = int(cl)
                if len(data) != expected_len:
                    raise CoverDownloadError(
                        f"Content-Length mismatch: expected {expected_len}, got {len(data)}"
                    )
            if len(data) < 500:
                raise CoverDownloadError(f"Cover too small ({len(data)} bytes)")
            return data
        except (requests.RequestException, CoverDownloadError) as exc:
            if attempt == 2:
                raise CoverDownloadError(str(exc)) from exc
            time.sleep(2**attempt)
    raise CoverDownloadError("Unreachable")


def encode_cover_webp(image_bytes: bytes) -> tuple[bytes, int, int]:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    if w > COVER_DISPLAY_WIDTH:
        ratio = COVER_DISPLAY_WIDTH / w
        new_h = max(1, int(h * ratio))
        img = img.resize((COVER_DISPLAY_WIDTH, new_h), Image.Resampling.LANCZOS)
        w, h = img.size
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=COVER_WEBP_QUALITY, method=6)
    return buf.getvalue(), w, h


def pick_summary(book: Book) -> str:
    if book.review.strip():
        return book.review.strip()
    if book.description.strip():
        return book.description.strip()
    return ""


def yaml_escape(value: str) -> str:
    if not value:
        return '""'
    if any(c in value for c in ':"\'\n#'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def write_book_md(book: Book) -> None:
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    path = BOOKS_DIR / f"{book.slug}.md"
    fm = [
        "---",
        f"title: {yaml_escape(book.title)}",
        f"author: {yaml_escape(book.author)}",
        f"cover: {yaml_escape(book.cover_path)}",
    ]
    if book.goodreads_url:
        fm.append(f"goodreads: {yaml_escape(book.goodreads_url)}")
    if book.highlights_url:
        fm.append(f"highlights: {yaml_escape(book.highlights_url)}")
    if book.rating and book.rating not in ("0", ""):
        fm.append(f"rating: {book.rating}")
    yr = book.year_read()
    if yr:
        fm.append(f"year_read: {yr}")
    if book.cover_width and book.cover_height:
        fm.append(f"cover_width: {book.cover_width}")
        fm.append(f"cover_height: {book.cover_height}")
    fm.append("---")
    fm.append("")
    body = pick_summary(book)
    if body:
        fm.append(body)
        fm.append("")
    path.write_text("\n".join(fm), encoding="utf-8")


def write_report(report: HarvestReport, total_written: int) -> None:
    lines = [
        "Harvest report",
        "==============",
        "",
        f"RSS pages: {report.page_counts}",
        f"Total shelf (RSS): {sum(report.page_counts) and 'see feed'}",
        f"Core books: {len(report.core_ids)}",
        f"Total written: {total_written}",
        "",
        "Libraryviz",
        "----------",
        f"IDs found: {len(report.libraryviz_ids)}",
        f"Matched to shelf: {len(report.libraryviz_matched)}",
        f"Not on shelf: {report.libraryviz_missing}",
        "",
        "Highlights fuzzy matches",
        "------------------------",
    ]
    for m in sorted(report.highlight_matches, key=lambda x: -x.score):
        lines.append(
            f"  [{m.method} {m.score:.3f}] {m.highlight_text[:60]!r} -> {m.matched_title!r}"
        )
    lines.extend(["", "Unmatched highlights", "--------------------"])
    for u in report.highlight_unmatched:
        lines.append(f"  {u}")
    lines.extend(["", "Duplicate highlights (skipped)", "-----------------------------"])
    for d in report.duplicate_highlights:
        lines.append(f"  {d}")
    lines.extend(["", "Orphans hand-authored", "---------------------"])
    for o in report.orphan_titles:
        lines.append(f"  {o}")
    if report.cover_failures:
        lines.extend(["", "Cover failures", "--------------"])
        for c in report.cover_failures:
            lines.append(f"  {c}")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    report = HarvestReport()
    session = make_session()

    print("Fetching Goodreads RSS...")
    feed = fetch_rss_pages(session, report)
    print(f"  {len(feed)} books on shelf")

    print("Parsing libraryviz...")
    lv_pairs = parse_libraryviz(LIBRARYVIZ_PATH)
    report.libraryviz_ids = [p[0] for p in lv_pairs]
    lv_ids: set[str] = set()
    for bid, alt in lv_pairs:
        if bid in feed:
            lv_ids.add(bid)
            report.libraryviz_matched.append(f"{bid}: {alt}")
        else:
            report.libraryviz_missing.append(f"{bid}: {alt}")

    print("Parsing highlights.md...")
    hl_entries = parse_highlights(HIGHLIGHTS_PATH)
    hl_map = fuzzy_match_highlights(hl_entries, feed, report)

    core_ids = lv_ids | set(hl_map.keys())

    report.core_ids = core_ids

    # Orphans for libraryviz/highlight misses not in feed
    orphan_books = build_orphan_books(session, report)
    for ob in orphan_books:
        if ob.book_id not in feed and ob.book_id not in {b.book_id for b in orphan_books if b.book_id in core_ids}:
            core_ids.add(ob.book_id)

    books_to_write: dict[str, Book] = {}

    for bid in core_ids:
        if bid in feed:
            b = feed[bid]
        else:
            b = next((o for o in orphan_books if o.book_id == bid), None)
            if not b:
                continue
        b.highlights_url = hl_map.get(bid, b.highlights_url)
        books_to_write[bid] = b

    for ob in orphan_books:
        if ob.book_id not in feed:
            ob.highlights_url = hl_map.get(ob.book_id, "")
            if ob.book_id in core_ids:
                books_to_write[ob.book_id] = ob

    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    if BOOKS_DIR.exists():
        for old in BOOKS_DIR.glob("*.md"):
            old.unlink()

    print(f"Writing {len(books_to_write)} books...")
    for bid, book in books_to_write.items():
        cover_rel = f"/assets/images/books/{book.slug}.webp"
        book.cover_path = cover_rel
        if book.cover_url:
            try:
                raw = download_cover(session, book.cover_url)
                webp, w, h = encode_cover_webp(raw)
                out = COVERS_DIR / f"{book.slug}.webp"
                out.write_bytes(webp)
                book.cover_width = w
                book.cover_height = h
            except CoverDownloadError as exc:
                report.cover_failures.append(f"{book.title}: {exc}")
                raise
        write_book_md(book)

    write_report(report, len(books_to_write))
    print(f"Done. Report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarvestError as exc:
        print(f"HARVEST FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
