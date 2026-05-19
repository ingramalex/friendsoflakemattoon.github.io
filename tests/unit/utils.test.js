'use strict';

const {
  buildNewsPlaceholders,
  sedNoise,
  sedFrac,
  padZ,
  toICSDate,
  makeCalLinks,
  calcInaction,
  classifyEventTag,
  clampCarouselIndex,
  totalPages,
  SED_CAP0,
  SED_RATE,
} = require('../../js/utils');

/* ── padZ ───────────────────────────────────────────────────── */
describe('padZ', () => {
  test('pads single-digit numbers', () => expect(padZ(3)).toBe('03'));
  test('leaves two-digit numbers unchanged', () => expect(padZ(12)).toBe('12'));
  test('handles zero', () => expect(padZ(0)).toBe('00'));
  test('handles large numbers without padding', () => expect(padZ(100)).toBe('100'));
});

/* ── sedNoise ───────────────────────────────────────────────── */
describe('sedNoise', () => {
  test('returns a value in [0, 1)', () => {
    for (let x = 0; x < 10; x++) {
      const v = sedNoise(x * 0.3);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });
  test('is deterministic for the same input', () => {
    expect(sedNoise(1.5)).toBe(sedNoise(1.5));
  });
  test('returns different values for different inputs', () => {
    expect(sedNoise(0)).not.toBe(sedNoise(1));
  });
});

/* ── sedFrac ────────────────────────────────────────────────── */
describe('sedFrac', () => {
  test('returns 0 at baseline year 1958', () => {
    expect(sedFrac(1958)).toBe(0);
  });
  test('returns ~1 at full capacity loss year', () => {
    const fullYear = 1958 + SED_CAP0 / SED_RATE;
    expect(sedFrac(fullYear)).toBeCloseTo(1, 10);
  });
  test('clamps below 0', () => {
    expect(sedFrac(1900)).toBe(0);
  });
  test('clamps above 1', () => {
    expect(sedFrac(3000)).toBe(1);
  });
  test('is between 0 and 1 for years between 1958 and full loss', () => {
    const mid = sedFrac(2025);
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(1);
  });
  test('increases monotonically over time', () => {
    expect(sedFrac(2000)).toBeLessThan(sedFrac(2050));
  });
});

/* ── toICSDate ──────────────────────────────────────────────── */
describe('toICSDate', () => {
  test('formats a known UTC date string correctly', () => {
    // Use a fixed timezone-offset date so the test is locale-independent
    const result = toICSDate('2025-07-04T10:30:00');
    expect(result).toMatch(/^\d{8}T\d{6}$/);
  });
  test('zero-pads month and day', () => {
    const result = toICSDate('2025-01-05T09:05:00');
    // Month and day components should be two digits
    expect(result.slice(4, 6)).toMatch(/^\d{2}$/);
    expect(result.slice(6, 8)).toMatch(/^\d{2}$/);
  });
});

/* ── makeCalLinks ───────────────────────────────────────────── */
describe('makeCalLinks', () => {
  const ev = {
    title: 'Lake Cleanup Day',
    start: '2025-08-15T09:00:00',
    location: 'Lake Mattoon, IL',
  };

  test('returns a string containing a Google Calendar link', () => {
    const html = makeCalLinks(ev);
    expect(html).toContain('calendar.google.com');
  });

  test('includes the event title in the Google URL (encoded)', () => {
    const html = makeCalLinks(ev);
    expect(html).toContain(encodeURIComponent('Lake Cleanup Day'));
  });

  test('includes an ICS data URI', () => {
    const html = makeCalLinks(ev);
    expect(html).toContain('data:text/calendar');
  });

  test('ICS download filename uses hyphens instead of spaces', () => {
    const html = makeCalLinks(ev);
    expect(html).toContain('Lake-Cleanup-Day.ics');
  });

  test('contains both Google Calendar and Apple/Outlook links', () => {
    const html = makeCalLinks(ev);
    expect(html).toContain('Google Calendar');
    expect(html).toContain('Apple / Outlook (.ics)');
  });

  test('ICS content includes VEVENT block', () => {
    const html = makeCalLinks(ev);
    expect(html).toContain(encodeURIComponent('BEGIN:VEVENT'));
  });
});

/* ── buildNewsPlaceholders ──────────────────────────────────── */
describe('buildNewsPlaceholders', () => {
  test('returns a non-empty string', () => {
    expect(buildNewsPlaceholders().length).toBeGreaterThan(0);
  });
  test('returns exactly 3 news-card divs', () => {
    const matches = buildNewsPlaceholders().match(/class="news-card"/g);
    expect(matches).toHaveLength(3);
  });
  test('each card contains a date, title, and text', () => {
    const html = buildNewsPlaceholders();
    expect(html).toContain('news-card-date');
    expect(html).toContain('news-card-title');
    expect(html).toContain('news-card-text');
  });
  test('all cards show "Coming Soon" as date', () => {
    const matches = buildNewsPlaceholders().match(/Coming Soon/g);
    expect(matches).toHaveLength(3);
  });
});

/* ── calcInaction ───────────────────────────────────────────── */
describe('calcInaction', () => {
  test('returns zero econ loss for zero visitors', () => {
    const r = calcInaction(5, 10, 0);
    expect(r.econ).toBe(0);
  });

  test('capLostGal increases with more years', () => {
    const r5  = calcInaction(5, 10, 1000);
    const r10 = calcInaction(10, 10, 1000);
    expect(r10.capLostGal).toBeGreaterThan(r5.capLostGal);
  });

  test('costMore is 3x the input cost', () => {
    expect(calcInaction(5, 8, 1000).costMore).toBe(24);
  });

  test('roi is 3.5x the input cost', () => {
    expect(calcInaction(5, 8, 1000).roi).toBe(28);
  });

  test('all returned values are non-negative', () => {
    const r = calcInaction(10, 15, 5000);
    expect(r.capLostGal).toBeGreaterThanOrEqual(0);
    expect(r.costMore).toBeGreaterThanOrEqual(0);
    expect(r.econ).toBeGreaterThanOrEqual(0);
    expect(r.roi).toBeGreaterThanOrEqual(0);
  });
});

/* ── classifyEventTag ───────────────────────────────────────── */
describe('classifyEventTag', () => {
  test('classifies cleanup events', () => {
    expect(classifyEventTag('Lake Cleanup Day')).toBe('Cleanup');
    expect(classifyEventTag('Spring cleanup 2025')).toBe('Cleanup');
  });
  test('classifies meeting events', () => {
    expect(classifyEventTag('Board Meeting')).toBe('Meeting');
    expect(classifyEventTag('Annual Members Meeting')).toBe('Meeting');
  });
  test('defaults to Community', () => {
    expect(classifyEventTag('Fundraiser Dinner')).toBe('Community');
    expect(classifyEventTag('')).toBe('Community');
  });
  test('is case-insensitive', () => {
    expect(classifyEventTag('CLEANUP CREW')).toBe('Cleanup');
    expect(classifyEventTag('MEET THE BOARD')).toBe('Meeting');
  });
  test('handles null/undefined gracefully', () => {
    expect(classifyEventTag(null)).toBe('Community');
    expect(classifyEventTag(undefined)).toBe('Community');
  });
});

/* ── clampCarouselIndex ─────────────────────────────────────── */
describe('clampCarouselIndex', () => {
  test('clamps negative index to 0', () => {
    expect(clampCarouselIndex(-1, 6, 3)).toBe(0);
  });
  test('clamps index past last page', () => {
    expect(clampCarouselIndex(10, 6, 3)).toBe(1); // 6 items / 3 per view = 2 pages (0,1)
  });
  test('leaves valid index unchanged', () => {
    expect(clampCarouselIndex(1, 9, 3)).toBe(1);
  });
  test('handles single page', () => {
    expect(clampCarouselIndex(5, 2, 3)).toBe(0);
  });
});

/* ── totalPages ─────────────────────────────────────────────── */
describe('totalPages', () => {
  test('exact multiple', () => expect(totalPages(9, 3)).toBe(3));
  test('rounds up partial page', () => expect(totalPages(7, 3)).toBe(3));
  test('single item', () => expect(totalPages(1, 3)).toBe(1));
  test('single per view', () => expect(totalPages(5, 1)).toBe(5));
});
