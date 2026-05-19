/* ── Pure utility functions — shared by index.html and test suite ── */

/* ── NEWS FEED ─────────────────────────────────────────────── */
var NEWS_MAX_POSTS = 3;

function buildNewsPlaceholders() {
  var posts = [
    { date: 'Coming Soon', title: 'News feed coming soon', text: 'Once the Google Sheet is connected, your latest Facebook posts will appear here automatically.' },
    { date: 'Coming Soon', title: 'Updated by officers', text: 'Officers add a new row to a shared Google Sheet and the site updates instantly — no coding needed.' },
    { date: 'Coming Soon', title: 'Links back to Facebook', text: 'Each card links directly to the full post on Facebook so visitors can like, comment, and share.' }
  ];
  return posts.map(function(p) {
    return '<div class="news-card" style="cursor:default;opacity:.6">'
      + '<span class="news-card-date">' + p.date + '</span>'
      + '<h3 class="news-card-title">' + p.title + '</h3>'
      + '<p class="news-card-text">' + p.text + '</p>'
      + '</div>';
  }).join('');
}

/* ── SEDIMENTATION MATH ─────────────────────────────────────── */
var SED_CAP0 = 13293;
var SED_RATE = 39.7;

function sedNoise(x) {
  var s = Math.sin(x * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
}

function sedFrac(y) {
  return Math.min(1, Math.max(0, (y - 1958) * SED_RATE / SED_CAP0));
}

/* ── CALENDAR / ICS HELPERS ─────────────────────────────────── */
var MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function padZ(n) {
  return String(n).padStart(2, '0');
}

function toICSDate(dateStr) {
  var d = new Date(dateStr);
  return d.getFullYear()
    + padZ(d.getMonth() + 1)
    + padZ(d.getDate())
    + 'T'
    + padZ(d.getHours())
    + padZ(d.getMinutes())
    + '00';
}

function makeCalLinks(ev) {
  var d     = new Date(ev.start);
  var end   = new Date(d.getTime() + 60 * 60 * 1000);
  var title = encodeURIComponent(ev.title);
  var loc   = encodeURIComponent(ev.location);

  var gStart = d.toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
  var gEnd   = end.toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
  var googleUrl = 'https://calendar.google.com/calendar/render?action=TEMPLATE'
    + '&text=' + title + '&dates=' + gStart + '/' + gEnd + '&location=' + loc;

  var icsStart = toICSDate(ev.start);
  var icsEnd   = toICSDate(end.toISOString());
  var icsContent = [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'BEGIN:VEVENT',
    'DTSTART:' + icsStart,
    'DTEND:'   + icsEnd,
    'SUMMARY:' + ev.title,
    'LOCATION:' + ev.location,
    'DESCRIPTION:Friends of Lake Mattoon Event',
    'END:VEVENT', 'END:VCALENDAR'
  ].join('\r\n');
  var icsUrl = 'data:text/calendar;charset=utf-8,' + encodeURIComponent(icsContent);

  return '<a href="' + googleUrl + '" target="_blank" rel="noopener">'
    + '<svg width="13" height="13" viewBox="0 0 14 14" fill="none"><rect x="1" y="1" width="12" height="12" rx="1.5" stroke="#4285f4" stroke-width="1.2"/><path d="M1 5h12" stroke="#4285f4" stroke-width="1.2"/><circle cx="7" cy="9" r="1.5" fill="#4285f4"/></svg>'
    + 'Google Calendar</a>'
    + '<a href="' + icsUrl + '" download="' + ev.title.replace(/\s+/g, '-') + '.ics">'
    + '<svg width="13" height="13" viewBox="0 0 14 14" fill="none"><rect x="1" y="1" width="12" height="12" rx="1.5" stroke="#555" stroke-width="1.2"/><path d="M1 5h12" stroke="#555" stroke-width="1.2"/><path d="M5 8l2 2 2-2" stroke="#555" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    + 'Apple / Outlook (.ics)</a>';
}

/* ── COST OF INACTION CALCULATOR ────────────────────────────── */
function calcInaction(yrs, cost, vis) {
  var capLost    = Math.round(yrs * SED_RATE);
  var capLostGal = Math.round(capLost * 325851 / 1000000);
  var costMore   = Math.round(cost * 3);
  var econ = 0;
  for (var i = 1; i <= yrs; i++) { econ += vis * Math.pow(0.9, i) * 30 * 0.3; }
  econ = Math.round(econ / 1000000);
  var roi = Math.round(cost * 3.5);
  return { capLostGal: capLostGal, costMore: costMore, econ: econ, roi: roi };
}

/* ── EVENT TAG CLASSIFIER ───────────────────────────────────── */
function classifyEventTag(title) {
  var tl = (title || '').toLowerCase();
  return tl.includes('clean') ? 'Cleanup' : tl.includes('meet') ? 'Meeting' : 'Community';
}

/* ── CAROUSEL HELPERS ───────────────────────────────────────── */
function clampCarouselIndex(index, itemCount, perView) {
  var total = Math.ceil(itemCount / perView);
  return Math.max(0, Math.min(index, total - 1));
}

function totalPages(itemCount, perView) {
  return Math.ceil(itemCount / perView);
}

/* CommonJS export for Jest; harmless no-op in browser */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
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
    MONTHS
  };
}
