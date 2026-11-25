// Content-agnostic responsive checks: shelf rows reflow across viewport
// widths without errors, and active filters survive a resize.
import { expect, test } from '@playwright/test';
import { gotoBookshelf, unexpectedIssues, visibleCards } from './helpers';

interface RowStats {
  rows: number;
  visible: number;
  inRows: number;
  perRow: number[];
}

async function rowStats(page: import('@playwright/test').Page): Promise<RowStats> {
  return page.evaluate(() => {
    const rows = Array.from(
      document.querySelectorAll('.bookshelf-unit .shelf-row')
    );
    const visible = document.querySelectorAll('.book-card:not(.hidden)').length;
    const perRow = rows.map(
      (row) => row.querySelectorAll('.book-card:not(.hidden)').length
    );
    return {
      rows: rows.length,
      visible,
      inRows: perRow.reduce((n, c) => n + c, 0),
      perRow,
    };
  });
}

test('rows reflow across mobile, tablet, and desktop widths', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  const viewports = [
    { name: 'mobile', width: 375, height: 700 },
    { name: 'tablet', width: 768, height: 800 },
    { name: 'desktop', width: 1440, height: 900 },
  ];

  const rowCounts: number[] = [];
  for (const vp of viewports) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.waitForTimeout(400); // shelf.js debounces resize by 150ms
    const stats = await rowStats(page);
    // Every visible card lives in exactly one row; no empty rows.
    expect(stats.inRows, vp.name).toBe(stats.visible);
    expect(stats.rows, vp.name).toBeGreaterThan(0);
    expect(Math.min(...stats.perRow), vp.name).toBeGreaterThan(0);
    rowCounts.push(stats.rows);
  }

  // Narrower viewport fits fewer books per row, so needs at least as many.
  expect(rowCounts[0]).toBeGreaterThanOrEqual(rowCounts[2]);

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('an active filter survives a viewport resize', async ({ page }) => {
  const issues = await gotoBookshelf(page);
  const cards = await visibleCards(page);
  const target = cards.find((c) => c.title.length >= 6);
  expect(target).toBeTruthy();

  await page.fill('#bookSearch', target!.title.slice(0, 6));
  const beforeResize = await visibleCards(page);
  expect(beforeResize.length).toBeGreaterThan(0);

  await page.setViewportSize({ width: 480, height: 800 });
  await page.waitForTimeout(400);
  const afterResize = await visibleCards(page);

  expect(afterResize.map((c) => c.title)).toEqual(
    beforeResize.map((c) => c.title)
  );

  const stats = await rowStats(page);
  expect(stats.inRows).toBe(stats.visible);

  expect(unexpectedIssues(issues)).toEqual([]);
});
