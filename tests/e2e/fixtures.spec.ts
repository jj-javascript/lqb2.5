// @fixtures — assertions that require the synthetic fixture books injected
// by the global setup. These run in local mode only; remote mode
// (E2E_BASE_URL) excludes them via grepInvert, since a deployed site has no
// fixture books.
//
// data-title values are the Liquid-downcased fixture titles.
import { expect, test } from '@playwright/test';
import {
  allCards,
  CardInfo,
  gotoBookshelf,
  unexpectedIssues,
  visibleCards,
} from './helpers';

const FIXTURE_TITLES = [
  'zz fixture review full',
  'zz fixture description only',
  'zz fixture no review',
  'zz fixture no year',
  'zz fixture no tags',
  'zz fixture no goodreads',
  'zz fixture no cover dims',
  'zz fixture missing cover',
  'zz fixture wide cover',
  'zz fixture tall cover',
  'zz fixture: the "quoted" word\'s café — résumé',
];

const SPECIAL_TITLE = 'zz fixture: the "quoted" word\'s café — résumé';

function cardByTitle(page: import('@playwright/test').Page, title: string) {
  return page.locator(`.book-card[data-title="${title}"]`);
}

test.describe('fixture matrix @fixtures', { tag: '@fixtures' }, () => {
  test('all fixture books render; the sample book is excluded', async ({
    page,
  }) => {
    const issues = await gotoBookshelf(page);
    const cards = await allCards(page);
    const titles = new Set(cards.map((c) => c.title));

    for (const title of FIXTURE_TITLES) {
      expect(titles.has(title), `fixture card ${title}`).toBe(true);
    }
    expect(titles.has('zz fixture sample hidden')).toBe(false);

    // Smoke: the ~70 real books render alongside the 11 visible fixtures.
    expect(cards.length).toBeGreaterThanOrEqual(70 + FIXTURE_TITLES.length);

    expect(unexpectedIssues(issues)).toEqual([]);
  });

  test('review heading follows the review flag; empty body gets a note', async ({
    page,
  }) => {
    const issues = await gotoBookshelf(page);

    await expect(
      cardByTitle(page, 'zz fixture review full').locator('.book-review-heading')
    ).toHaveText('My review');
    await expect(
      cardByTitle(page, 'zz fixture description only').locator(
        '.book-review-heading'
      )
    ).toHaveText('GoodReads Description');

    const noReview = cardByTitle(page, 'zz fixture no review');
    await expect(noReview.locator('.book-review-heading')).toHaveCount(0);
    await expect(noReview.locator('.book-review-empty')).toHaveText(
      'No review yet.'
    );

    expect(unexpectedIssues(issues)).toEqual([]);
  });

  test('action buttons match field presence', async ({ page }) => {
    const issues = await gotoBookshelf(page);

    const full = cardByTitle(page, 'zz fixture review full');
    await expect(full.locator('.book-action--goodreads')).toHaveCount(1);
    await expect(full.locator('.book-action--notes')).toHaveCount(1);

    const noLinks = cardByTitle(page, 'zz fixture no goodreads');
    await expect(noLinks.locator('.book-action--goodreads')).toHaveCount(0);
    await expect(noLinks.locator('.book-action--notes')).toHaveCount(0);

    const descOnly = cardByTitle(page, 'zz fixture description only');
    await expect(descOnly.locator('.book-action--goodreads')).toHaveCount(1);
    await expect(descOnly.locator('.book-action--notes')).toHaveCount(0);

    expect(unexpectedIssues(issues)).toEqual([]);
  });

  test('missing cover dimensions fall back to 200x300', async ({ page }) => {
    const issues = await gotoBookshelf(page);
    const img = cardByTitle(page, 'zz fixture no cover dims').locator(
      'img.book-cover'
    );
    await expect(img).toHaveAttribute('width', '200');
    await expect(img).toHaveAttribute('height', '300');
    expect(unexpectedIssues(issues)).toEqual([]);
  });

  test('a missing cover image file does not break the page', async ({
    page,
  }) => {
    const issues = await gotoBookshelf(page);
    const card = cardByTitle(page, 'zz fixture missing cover');
    await expect(card).toBeVisible();

    // The card still flips and its modal works despite the broken image.
    await card.click();
    await expect(page.locator('.book-modal')).toHaveClass(/active/);
    await page.keyboard.press('Escape');
    await expect(page.locator('.book-modal')).toBeHidden();

    // The only tolerated issue is this fixture's own cover 404; the helper
    // filters it, so anything left is a real failure.
    expect(unexpectedIssues(issues)).toEqual([]);
  });

  test('updated covers with unusual aspect ratios do not break layout', async ({
    page,
  }) => {
    const issues = await gotoBookshelf(page);

    const wide = cardByTitle(page, 'zz fixture wide cover');
    await expect(wide.locator('img.book-cover')).toHaveAttribute('width', '400');
    await expect(wide.locator('img.book-cover')).toHaveAttribute('height', '300');
    const tall = cardByTitle(page, 'zz fixture tall cover');
    await expect(tall.locator('img.book-cover')).toHaveAttribute('width', '150');
    await expect(tall.locator('img.book-cover')).toHaveAttribute('height', '400');

    for (const card of [wide, tall]) {
      const box = await card.boundingBox();
      expect(box, 'card has a layout box').toBeTruthy();
      expect(box!.width).toBeGreaterThan(0);
      expect(box!.height).toBeGreaterThan(0);
    }

    // Shelf rows stay consistent: every visible card in exactly one row,
    // no empty rows.
    const stats = await page.evaluate(() => {
      const rows = Array.from(
        document.querySelectorAll('.bookshelf-unit .shelf-row')
      );
      const visible = document.querySelectorAll(
        '.book-card:not(.hidden)'
      ).length;
      const perRow = rows.map(
        (row) => row.querySelectorAll('.book-card:not(.hidden)').length
      );
      return { visible, perRow, inRows: perRow.reduce((n, c) => n + c, 0) };
    });
    expect(stats.inRows).toBe(stats.visible);
    expect(Math.min(...stats.perRow)).toBeGreaterThan(0);

    expect(unexpectedIssues(issues)).toEqual([]);
  });

  test('special-character title and author survive rendering and search', async ({
    page,
  }) => {
    const issues = await gotoBookshelf(page);

    // The card's attributes must parse intact (no mangled HTML attribute).
    const cards = await allCards(page);
    const special = cards.find((c) => c.title.includes('café'));
    expect(special, 'special-chars fixture card found').toBeTruthy();
    expect(special!.title).toBe(SPECIAL_TITLE);
    expect(special!.author).toBe("josé o'brien, jr.");
    expect(special!.categories).toContain('fiction');

    for (const query of ['café', 'quoted', 'résumé']) {
      await page.fill('#bookSearch', query);
      const visible = await visibleCards(page);
      expect(
        visible.some((c) => c.title === SPECIAL_TITLE),
        `search "${query}" finds the special-chars book`
      ).toBe(true);
    }
    await page.fill('#bookSearch', '');

    expect(unexpectedIssues(issues)).toEqual([]);
  });

  test('the no-year fixture sorts last under both year directions', async ({
    page,
  }) => {
    const issues = await gotoBookshelf(page);

    for (const value of ['year-desc', 'year-asc']) {
      await page.selectOption('#sortSelect', value);
      const ordered = await visibleCards(page);
      const noYearIndex = ordered.findIndex(
        (c) => c.title === 'zz fixture no year'
      );
      expect(noYearIndex).toBeGreaterThanOrEqual(0);
      const lastYearIndex = ordered.reduce(
        (max: number, c: CardInfo, i: number) =>
          c.year.trim() !== '' ? i : max,
        -1
      );
      expect(noYearIndex).toBeGreaterThan(lastYearIndex);
    }

    expect(unexpectedIssues(issues)).toEqual([]);
  });

  test('the no-tags fixture is hidden under every category filter', async ({
    page,
  }) => {
    const issues = await gotoBookshelf(page);
    const options = await page.$$eval('#categoryFilter option', (els) =>
      els.map((el) => (el as HTMLOptionElement).value).filter(Boolean)
    );
    expect(options.length).toBeGreaterThan(0);

    for (const slug of options) {
      await page.selectOption('#categoryFilter', slug);
      // Filtered-out cards are detached from the DOM by shelf.js.
      await expect(
        cardByTitle(page, 'zz fixture no tags'),
        `category ${slug}`
      ).toHaveCount(0);
    }

    await page.selectOption('#categoryFilter', '');
    await expect(cardByTitle(page, 'zz fixture no tags')).toHaveCount(1);

    expect(unexpectedIssues(issues)).toEqual([]);
  });
});
