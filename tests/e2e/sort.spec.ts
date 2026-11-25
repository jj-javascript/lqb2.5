// Content-agnostic sort checks: for each sort option, the expected order is
// computed from the cards' own data attributes using the same comparator
// contract as shelf.js, then compared against the actual DOM order.
import { expect, test } from '@playwright/test';
import { CardInfo, gotoBookshelf, unexpectedIssues, visibleCards } from './helpers';

// Mirrors the comparators in assets/js/shelf.js: books with a year come
// first (sorted), books without a year always sort last, stable otherwise.
function byYear(direction: 1 | -1) {
  return (a: CardInfo, b: CardInfo): number => {
    const yearA = parseInt(a.year, 10);
    const yearB = parseInt(b.year, 10);
    const hasA = !isNaN(yearA);
    const hasB = !isNaN(yearB);
    if (hasA && hasB) return (yearA - yearB) * direction;
    if (hasA) return -1;
    if (hasB) return 1;
    return 0;
  };
}

function byField(field: 'title' | 'author') {
  return (a: CardInfo, b: CardInfo): number => {
    const valA = a[field].trim();
    const valB = b[field].trim();
    if (!valA && !valB) return 0;
    if (!valA) return 1;
    if (!valB) return -1;
    return valA.localeCompare(valB);
  };
}

const SORTS: Array<{ value: string; cmp: (a: CardInfo, b: CardInfo) => number }> = [
  { value: 'year-desc', cmp: byYear(-1) },
  { value: 'year-asc', cmp: byYear(1) },
  { value: 'title', cmp: byField('title') },
  { value: 'author', cmp: byField('author') },
];

for (const { value, cmp } of SORTS) {
  test(`sort "${value}" orders the shelf correctly`, async ({ page }) => {
    const issues = await gotoBookshelf(page);
    const before = await visibleCards(page);
    expect(before.length).toBeGreaterThan(1);

    await page.selectOption('#sortSelect', value);
    const after = await visibleCards(page);

    // Same set of books, in the order the comparator contract dictates.
    // The sort is stable, so the expected order derives from the pre-sort
    // DOM order with an identical tie-breaking rule.
    expect([...after].sort((a, b) => a.title.localeCompare(b.title))).toEqual(
      [...before].sort((a, b) => a.title.localeCompare(b.title))
    );
    const expected = [...before].sort(cmp);
    expect(after.map((c) => c.title)).toEqual(expected.map((c) => c.title));

    expect(unexpectedIssues(issues)).toEqual([]);
  });
}

test('books without a year sort last under both year directions', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);

  for (const value of ['year-desc', 'year-asc']) {
    await page.selectOption('#sortSelect', value);
    const ordered = await visibleCards(page);
    const lastYearIndex = ordered.reduce(
      (max, c, i) => (c.year.trim() !== '' ? i : max),
      -1
    );
    for (let i = 0; i < ordered.length; i++) {
      if (ordered[i].year.trim() === '') {
        expect(i).toBeGreaterThan(lastYearIndex);
      }
    }
  }

  expect(unexpectedIssues(issues)).toEqual([]);
});
