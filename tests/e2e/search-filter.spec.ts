// Content-agnostic search and category-filter checks. Queries and category
// expectations are derived from the cards actually on the page, so these
// run against any library content.
import { expect, test } from '@playwright/test';
import {
  allCards,
  CardInfo,
  gotoBookshelf,
  unexpectedIssues,
  visibleCards,
} from './helpers';

function titles(cards: CardInfo[]): string[] {
  return cards.map((c) => c.title).sort();
}

test('search filters by title and author, case-insensitively', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  const cards = await allCards(page);
  const target = cards.find((c) => c.title.length >= 6);
  expect(target).toBeTruthy();

  const query = target!.title.slice(0, 6);
  await page.fill('#bookSearch', query);
  const visible = await visibleCards(page);
  const expected = cards.filter(
    (c) => c.title.includes(query) || c.author.includes(query)
  );
  expect(visible.length).toBeGreaterThan(0);
  expect(titles(visible)).toEqual(titles(expected));

  // Uppercase query must behave identically (the input is lowercased).
  await page.fill('#bookSearch', query.toUpperCase());
  expect(titles(await visibleCards(page))).toEqual(titles(expected));

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('no-match search shows the empty state; clearing restores all books', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  const total = (await allCards(page)).length;

  await page.fill('#bookSearch', 'zzz-no-such-book-zzz');
  await expect(page.locator('#bookshelfEmpty')).toBeVisible();
  expect(await visibleCards(page)).toEqual([]);

  await page.fill('#bookSearch', '');
  await expect(page.locator('#bookshelfEmpty')).toBeHidden();
  expect((await visibleCards(page)).length).toBe(total);

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('every category in the dropdown filters correctly', async ({ page }) => {
  const issues = await gotoBookshelf(page);
  const cards = await allCards(page);
  const options = await page.$$eval('#categoryFilter option', (els) =>
    els.map((el) => (el as HTMLOptionElement).value).filter(Boolean)
  );
  expect(options.length).toBeGreaterThan(0);

  for (const slug of options) {
    await page.selectOption('#categoryFilter', slug);
    const visible = await visibleCards(page);
    const expected = cards.filter((c) => c.categories.includes(slug));
    expect(visible.length).toBeGreaterThan(0); // dropdown only lists used shelves
    expect(titles(visible)).toEqual(titles(expected));
    for (const card of visible) {
      expect(card.categories).toContain(slug);
    }
  }

  // Reset to "All categories" restores everything.
  await page.selectOption('#categoryFilter', '');
  expect((await visibleCards(page)).length).toBe(cards.length);

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('search and category filter compose', async ({ page }) => {
  const issues = await gotoBookshelf(page);
  const cards = await allCards(page);
  const options = await page.$$eval('#categoryFilter option', (els) =>
    els.map((el) => (el as HTMLOptionElement).value).filter(Boolean)
  );

  // Find a category + query combination that matches at least one book.
  const slug = options[0];
  const inCategory = cards.filter((c) => c.categories.includes(slug));
  expect(inCategory.length).toBeGreaterThan(0);
  const query = inCategory[0].title.slice(0, 4);

  await page.selectOption('#categoryFilter', slug);
  await page.fill('#bookSearch', query);

  const visible = await visibleCards(page);
  const expected = cards.filter(
    (c) =>
      c.categories.includes(slug) &&
      (c.title.includes(query) || c.author.includes(query))
  );
  expect(titles(visible)).toEqual(titles(expected));

  expect(unexpectedIssues(issues)).toEqual([]);
});
