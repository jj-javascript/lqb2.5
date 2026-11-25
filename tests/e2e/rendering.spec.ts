// Content-agnostic rendering checks: the bookshelf must load without any
// uncaught JS errors and every rendered card must be structurally valid.
// Runs against the local fixture build and the deployed site.
import { expect, test } from '@playwright/test';
import { allCards, gotoBookshelf, unexpectedIssues } from './helpers';

test('bookshelf loads with zero console errors and zero pageerrors', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  expect(await page.locator('.book-card').count()).toBeGreaterThan(0);
  expect(unexpectedIssues(issues)).toEqual([]);
});

test('every card has the required structure and attributes', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);

  const cards = page.locator('.book-card');
  const count = await cards.count();
  expect(count).toBeGreaterThan(0);

  // Required data attributes (search/sort break silently without them).
  const infos = await allCards(page);
  expect(infos.length).toBe(count);
  for (const card of infos) {
    expect(card.title.trim()).not.toBe('');
    expect(card.author.trim()).not.toBe('');
  }

  // Every card shows a cover image with usable dimensions.
  const imgs = page.locator('.book-card img.book-cover');
  expect(await imgs.count()).toBe(count);
  const dims = await page.$$eval('img.book-cover', (els) =>
    els.map((el) => [el.getAttribute('width'), el.getAttribute('height')])
  );
  for (const [w, h] of dims) {
    expect(Number(w)).toBeGreaterThan(0);
    expect(Number(h)).toBeGreaterThan(0);
  }

  // Every card back has either a review heading or the empty-state note.
  const backs = await page.$$eval('.book-card .book-back-scroll', (els) =>
    els.map((el) => ({
      heading:
        el.querySelector('.book-review-heading')?.textContent?.trim() || '',
      hasEmpty: !!el.querySelector('.book-review-empty'),
    }))
  );
  expect(backs.length).toBe(count);
  for (const back of backs) {
    if (back.hasEmpty) continue;
    expect(['My review', 'GoodReads Description']).toContain(back.heading);
  }

  // Action links, when present, point where they claim to.
  const goodreadsHrefs = await page.$$eval('.book-action--goodreads', (els) =>
    els.map((el) => el.getAttribute('href') || '')
  );
  for (const href of goodreadsHrefs) {
    expect(href).toContain('goodreads.com/book/show/');
  }
  const notesHrefs = await page.$$eval('.book-action--notes', (els) =>
    els.map((el) => el.getAttribute('href') || '')
  );
  for (const href of notesHrefs) {
    expect(href.trim()).not.toBe('');
  }

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('filter dropdown offers only shelves used by at least one book', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  const infos = await allCards(page);
  const usedSlugs = new Set(infos.flatMap((c) => c.categories));
  const options = await page.$$eval('#categoryFilter option', (els) =>
    els.map((el) => (el as HTMLOptionElement).value).filter(Boolean)
  );
  expect(options.length).toBeGreaterThan(0);
  for (const slug of options) {
    expect(usedSlugs.has(slug)).toBe(true);
  }
  expect(unexpectedIssues(issues)).toEqual([]);
});
