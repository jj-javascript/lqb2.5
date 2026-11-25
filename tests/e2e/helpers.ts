// Shared helpers for the bookshelf E2E specs. All specs are content-agnostic
// unless tagged @fixtures: they read the rendered cards' data-* attributes
// and derive expectations from whatever books are on the page, so the same
// specs run against the local fixture build and the deployed site.
import { Page } from '@playwright/test';

export interface PageIssue {
  kind: 'pageerror' | 'console';
  text: string;
  url: string;
}

export interface CardInfo {
  title: string;
  author: string;
  year: string;
  categories: string[];
}

/** Install pageerror/console listeners. Call BEFORE navigating. */
export function watchForIssues(page: Page): PageIssue[] {
  const issues: PageIssue[] = [];
  page.on('pageerror', (err) =>
    issues.push({ kind: 'pageerror', text: String(err), url: page.url() })
  );
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      issues.push({ kind: 'console', text: msg.text(), url: msg.location()?.url || '' });
    }
  });
  return issues;
}

/**
 * Issues that should fail a test. The zz-fixture-missing-cover fixture book
 * intentionally 404s its cover image (simulating a failed cover download);
 * that resource error is expected and filtered out. Everything else —
 * uncaught exceptions and any other console error — is a failure.
 */
export function unexpectedIssues(issues: PageIssue[]): string[] {
  return issues
    .filter(
      (i) => !(i.kind === 'console' && i.url.includes('zz-fixture-missing-cover'))
    )
    .map((i) => `${i.kind}: ${i.text} [${i.url}]`);
}

/** Navigate to the bookshelf page (local build or remote site). */
export async function gotoBookshelf(page: Page): Promise<PageIssue[]> {
  const issues = watchForIssues(page);
  await page.goto(process.env.E2E_BASE_URL || '/bookshelf.html');
  await page.waitForSelector('.book-card');
  return issues;
}

/**
 * Cards currently visible (not .hidden). Note: shelf.js DETACHES
 * non-matching cards from the DOM while a filter is active, so both of
 * these readers only reflect the full library when no filter is applied.
 */
export async function visibleCards(page: Page): Promise<CardInfo[]> {
  return page.$$eval('.book-card:not(.hidden)', (els) =>
    els.map((el) => {
      let categories: string[] = [];
      try {
        const parsed = JSON.parse(el.getAttribute('data-categories') || '[]');
        if (Array.isArray(parsed)) categories = parsed.map(String);
      } catch {
        categories = [];
      }
      return {
        title: el.getAttribute('data-title') || '',
        author: el.getAttribute('data-author') || '',
        year: el.getAttribute('data-year') || '',
        categories,
      };
    })
  );
}

/** All cards in the DOM (only equals the full library with no filter active). */
export async function allCards(page: Page): Promise<CardInfo[]> {
  return page.$$eval('.book-card', (els) =>
    els.map((el) => {
      let categories: string[] = [];
      try {
        const parsed = JSON.parse(el.getAttribute('data-categories') || '[]');
        if (Array.isArray(parsed)) categories = parsed.map(String);
      } catch {
        categories = [];
      }
      return {
        title: el.getAttribute('data-title') || '',
        author: el.getAttribute('data-author') || '',
        year: el.getAttribute('data-year') || '',
        categories,
      };
    })
  );
}

/** Abort requests to external origins (e.g. Goodreads links in popups). */
export async function blockExternalRequests(page: Page): Promise<void> {
  const pageOrigin = new URL(page.url()).origin;
  await page.context().route('**/*', (route) => {
    try {
      const reqOrigin = new URL(route.request().url()).origin;
      return reqOrigin === pageOrigin ? route.continue() : route.abort();
    } catch {
      return route.abort();
    }
  });
}
