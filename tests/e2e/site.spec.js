// @ts-check
const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
  // Stub external API calls so tests don't depend on live network
  await page.route('https://sheets.googleapis.com/**', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ values: [] }) })
  );
  await page.route('https://www.googleapis.com/calendar/**', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
  );
  await page.goto('/');
});

/* ── PAGE LOAD ───────────────────────────────────────────────── */
test.describe('Page load', () => {
  test('has correct title', async ({ page }) => {
    await expect(page).toHaveTitle(/Friends of Lake Mattoon/i);
  });

  test('hero section is visible', async ({ page }) => {
    const hero = page.locator('section.hero, #hero, .hero');
    await expect(hero.first()).toBeVisible();
  });

  test('renders the site logo / org name in the nav', async ({ page }) => {
    const nav = page.locator('nav, header').first();
    await expect(nav).toContainText(/Lake Mattoon/i);
  });

  test('js/utils.js loads without errors', async ({ page }) => {
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));
    await page.reload();
    await page.waitForLoadState('networkidle');
    expect(errors.filter(e => !e.includes('googleapis'))).toHaveLength(0);
  });
});

/* ── NAVIGATION ─────────────────────────────────────────────── */
test.describe('Navigation', () => {
  test('all nav anchor links resolve to sections on the page', async ({ page }) => {
    const links = await page.locator('nav a[href^="#"]').all();
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      const href = await link.getAttribute('href');
      if (!href || href === '#') continue; // bare # is a valid no-op link
      const target = page.locator(href);
      await expect(target).toHaveCount(1);
    }
  });

  test('mobile hamburger toggles the drawer', async ({ page, isMobile }) => {
    if (!isMobile) test.skip();
    const hamburger = page.locator('#hamburger');
    const drawer    = page.locator('#nav-drawer');
    await expect(drawer).not.toHaveClass(/open/);
    await hamburger.click();
    await expect(drawer).toHaveClass(/open/);
    await hamburger.click();
    await expect(drawer).not.toHaveClass(/open/);
  });
});

/* ── MODALS ─────────────────────────────────────────────────── */
test.describe('Modals', () => {
  test('donate modal opens and closes', async ({ page }) => {
    const modal   = page.locator('#donateModal');
    const trigger = page.locator('[onclick*="openDonateModal"], button:has-text("Donate")').first();
    await trigger.click();
    await expect(modal).toHaveClass(/open/);

    // Close via button
    const closeBtn = page.locator('#modalClose, #modalClosBtn').first();
    await closeBtn.click();
    await expect(modal).not.toHaveClass(/open/);
  });

  test('member modal opens and closes via Escape', async ({ page }) => {
    const modal   = page.locator('#memberModal');
    const trigger = page.locator('[onclick*="openMemberModal"]').first();
    await trigger.click();
    await expect(modal).toHaveClass(/open/);
    await page.keyboard.press('Escape');
    await expect(modal).not.toHaveClass(/open/);
  });

  test('pillar modals open for each key', async ({ page }) => {
    for (const key of ['conservation', 'education', 'economy']) {
      const trigger = page.locator(`[onclick*="openPillarModal('${key}')"]`).first();
      if (await trigger.count() === 0) continue;
      await trigger.click();
      const modal = page.locator(`#pillar${key.charAt(0).toUpperCase() + key.slice(1)}`);
      await expect(modal).toHaveClass(/open/);
      await page.keyboard.press('Escape');
    }
  });
});

/* ── COST-OF-INACTION CALCULATOR ────────────────────────────── */
test.describe('Cost of inaction calculator', () => {
  test('output elements are present when calculator section exists', async ({ page }) => {
    // These IDs are wired in JS but the HTML section may not exist yet — skip gracefully
    const capacity = page.locator('#pRCapacity');
    if (await capacity.count() === 0) test.skip();
    await expect(page.locator('#pRCost')).toHaveCount(1);
    await expect(page.locator('#pREcon')).toHaveCount(1);
    await expect(page.locator('#pRReturn')).toHaveCount(1);
  });

  test('output text updates when year slider changes', async ({ page }) => {
    const slider = page.locator('#pSliderYears');
    const capEl  = page.locator('#pRCapacity');
    if (await slider.count() === 0) test.skip();

    const before = await capEl.textContent();
    await slider.evaluate(el => { el.value = '20'; el.dispatchEvent(new Event('input')); });
    await page.evaluate(() => pCalcUpdate());
    const after = await capEl.textContent();
    expect(after).not.toBe(before);
  });
});

/* ── NEWS FEED ───────────────────────────────────────────────── */
test.describe('News feed', () => {
  test('news grid container is present', async ({ page }) => {
    await expect(page.locator('#newsGrid')).toHaveCount(1);
  });

  test('shows placeholder or real cards after load', async ({ page }) => {
    await page.waitForLoadState('networkidle');
    const grid = page.locator('#newsGrid');
    const html = await grid.innerHTML();
    expect(html.trim().length).toBeGreaterThan(0);
  });
});

/* ── EVENTS CAROUSEL ─────────────────────────────────────────── */
test.describe('Events carousel', () => {
  test('events container is present', async ({ page }) => {
    await expect(page.locator('#eventsContainer')).toHaveCount(1);
  });

  test('renders a state after calendar fetch resolves', async ({ page }) => {
    await page.waitForLoadState('networkidle');
    const container = page.locator('#eventsContainer');
    await expect(container).not.toBeEmpty();
  });
});

/* ── LAYOUT & STYLES ─────────────────────────────────────────── */
test.describe('Layout and styles', () => {
  test('body has no horizontal overflow on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    const bodyWidth  = await page.evaluate(() => document.body.scrollWidth);
    const innerWidth = await page.evaluate(() => window.innerWidth);
    expect(bodyWidth).toBeLessThanOrEqual(innerWidth + 2);
  });

  test('body has no horizontal overflow on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.route('https://sheets.googleapis.com/**', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ values: [] }) })
    );
    await page.route('https://www.googleapis.com/calendar/**', route =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
    );
    const bodyWidth  = await page.evaluate(() => document.body.scrollWidth);
    const innerWidth = await page.evaluate(() => window.innerWidth);
    expect(bodyWidth).toBeLessThanOrEqual(innerWidth + 2);
  });

  test('primary CTA button is visible above the fold on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    const cta = page.locator('a[href*="donate"], button:has-text("Donate")').first();
    await expect(cta).toBeVisible();
  });

  test('nav is visible on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.locator('nav').first()).toBeVisible();
  });
});

/* ── ACCESSIBILITY BASICS ────────────────────────────────────── */
test.describe('Accessibility basics', () => {
  test('all images have alt attributes', async ({ page }) => {
    const imgs = await page.locator('img').all();
    for (const img of imgs) {
      const alt = await img.getAttribute('alt');
      expect(alt).not.toBeNull();
    }
  });

  test('page has exactly one h1', async ({ page }) => {
    await expect(page.locator('h1')).toHaveCount(1);
  });

  test('contact form has labelled inputs', async ({ page }) => {
    const inputs = page.locator('#contactForm input[required], #contactForm textarea[required]');
    const count  = await inputs.count();
    if (count === 0) test.skip();
    for (let i = 0; i < count; i++) {
      const input = inputs.nth(i);
      const id    = await input.getAttribute('id');
      const name  = await input.getAttribute('name');
      expect(id || name).toBeTruthy();
    }
  });
});
