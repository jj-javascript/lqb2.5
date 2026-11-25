// Content-agnostic modal interaction checks: open via click and keyboard,
// close via every affordance, and verify action links don't trigger it.
import { expect, test } from '@playwright/test';
import {
  blockExternalRequests,
  gotoBookshelf,
  unexpectedIssues,
} from './helpers';

test('click opens the modal with the card content; close button closes it', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  const card = page.locator('.book-card').first();
  const modal = page.locator('.book-modal');

  await expect(card).toHaveAttribute('aria-expanded', 'false');
  await card.click();
  await expect(modal).toHaveClass(/active/);
  await expect(page.locator('.book-modal-backdrop')).toHaveClass(/active/);
  await expect(card).toHaveAttribute('aria-expanded', 'true');
  await expect(
    page.locator('.book-modal-content .book-back-content')
  ).toBeVisible();

  await page.locator('.book-modal-close').click();
  await expect(modal).not.toHaveClass(/active/);
  await expect(modal).toBeHidden(); // hidden attr re-added after transition
  await expect(card).toHaveAttribute('aria-expanded', 'false');

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('Escape key closes the modal', async ({ page }) => {
  const issues = await gotoBookshelf(page);
  await page.locator('.book-card').first().click();
  await expect(page.locator('.book-modal')).toHaveClass(/active/);

  await page.keyboard.press('Escape');
  await expect(page.locator('.book-modal')).not.toHaveClass(/active/);
  await expect(page.locator('.book-modal')).toBeHidden();

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('clicking the backdrop closes the modal', async ({ page }) => {
  const issues = await gotoBookshelf(page);
  await page.locator('.book-card').first().click();
  await expect(page.locator('.book-modal')).toHaveClass(/active/);

  // Click a corner of the backdrop, away from the centered modal.
  await page
    .locator('.book-modal-backdrop')
    .click({ position: { x: 5, y: 5 } });
  await expect(page.locator('.book-modal')).toBeHidden();

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('clicking outside the card and modal closes the modal', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  await page.locator('.book-card').first().click();
  await expect(page.locator('.book-modal')).toHaveClass(/active/);

  await page.evaluate(() => document.body.click());
  await expect(page.locator('.book-modal')).toBeHidden();

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('Enter and Space keys open the modal from a focused card', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  const card = page.locator('.book-card').first();
  const modal = page.locator('.book-modal');

  await card.focus();
  await page.keyboard.press('Enter');
  await expect(modal).toHaveClass(/active/);
  await page.keyboard.press('Escape');
  await expect(modal).toBeHidden();

  await card.focus();
  await page.keyboard.press(' ');
  await expect(modal).toHaveClass(/active/);
  await page.keyboard.press('Escape');
  await expect(modal).toBeHidden();

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('the Goodreads link inside the modal opens externally; modal stays put', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  const cardWithLink = page
    .locator('.book-card:has(.book-action--goodreads)')
    .first();
  test.skip(
    (await cardWithLink.count()) === 0,
    'no books with a Goodreads link on this page'
  );

  // The action links live on the card back, which the modal clones — the
  // modal is the real place a reader can click them.
  await cardWithLink.click();
  await expect(page.locator('.book-modal')).toHaveClass(/active/);

  await blockExternalRequests(page);
  const [popup] = await Promise.all([
    page
      .context()
      .waitForEvent('page', { timeout: 5000 })
      .catch(() => null),
    page.locator('.book-modal .book-action--goodreads').first().click(),
  ]);
  if (popup) await popup.close();

  // Clicks inside the modal must not bubble into a close or a card flip.
  await expect(page.locator('.book-modal')).toHaveClass(/active/);
  await page.keyboard.press('Escape');
  await expect(page.locator('.book-modal')).toBeHidden();

  expect(unexpectedIssues(issues)).toEqual([]);
});

test('opening a different card replaces the modal content', async ({
  page,
}) => {
  const issues = await gotoBookshelf(page);
  const cards = page.locator('.book-card');
  test.skip((await cards.count()) < 2, 'need at least two cards');

  const firstTitle = (await cards.nth(0).getAttribute('data-title')) || '';
  await cards.nth(0).click();
  await expect(page.locator('.book-modal')).toHaveClass(/active/);
  await expect(page.locator('.book-modal')).toHaveAttribute(
    'aria-label',
    firstTitle
  );
  await page.keyboard.press('Escape');
  await expect(page.locator('.book-modal')).toBeHidden();
  await expect(cards.nth(0)).toHaveAttribute('aria-expanded', 'false');

  const secondTitle = (await cards.nth(1).getAttribute('data-title')) || '';
  await cards.nth(1).click();
  await expect(page.locator('.book-modal')).toHaveClass(/active/);
  await expect(page.locator('.book-modal')).toHaveAttribute(
    'aria-label',
    secondTitle
  );
  await expect(cards.nth(1)).toHaveAttribute('aria-expanded', 'true');
  await expect(cards.nth(0)).toHaveAttribute('aria-expanded', 'false');

  await page.keyboard.press('Escape');
  expect(unexpectedIssues(issues)).toEqual([]);
});
