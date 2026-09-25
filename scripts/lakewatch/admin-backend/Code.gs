/**
 * Lake Watch publishing backend — a Google Apps Script web app.
 *
 * lake-admin.html signs people in with Google and sends their ID token here.
 * This script checks the token with Google, checks the email against a list
 * kept in Script Properties, and only then touches GitHub, using a token that
 * never leaves Google's servers. The browser never holds a GitHub credential.
 *
 * Nothing secret lives in this file; it is safe in a public repository.
 * Everything secret or personal is in Script Properties (Project Settings):
 *
 *   GITHUB_TOKEN      fine-grained token: this repo only, Contents + Pull
 *                     requests read/write
 *   GOOGLE_CLIENT_ID  the OAuth client ID lake-admin.html signs in with
 *   PUBLISHER_EMAILS  comma-separated; these people's stories go live directly
 *   EDITOR_EMAILS     optional, comma-separated; these people's stories wait
 *                     as a pull request until a publisher merges it
 *   FINDINGS_REPO     optional; where the meeting findings live, if they are
 *                     ever moved to a private repository (owner/name)
 *
 * Deploy: Deploy → New deployment → Web app, Execute as: Me,
 * Who has access: Anyone. "Anyone" is required because the browser calls this
 * without Google cookies; the ID token check below is the lock.
 */

var REPO = 'ingramalex/friendsoflakemattoon.github.io';
var BASE = 'main';
var REVIEW_BRANCH = 'lake-watch/meetings';
var STORIES = 'data/lake-watch/stories.json';
var MEETINGS_DIR = 'data/lake-watch/meetings';
var CATEGORIES = ['', 'water quality', 'watershed', 'infrastructure', 'recreation', 'governance'];
var KINDS = ['video', 'article', 'document'];

function doGet() {
  return ContentService.createTextOutput('Lake Watch publishing backend.');
}

// The page posts text/plain so the browser sends it without a CORS preflight,
// which Apps Script cannot answer.
function doPost(e) {
  var out;
  try {
    var req = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    var user = verify_(req.idToken);
    out = route_(user, req);
    out.ok = true;
  } catch (err) {
    out = { ok: false, code: err.code || 'error', error: err.message };
  }
  return ContentService.createTextOutput(JSON.stringify(out))
    .setMimeType(ContentService.MimeType.JSON);
}

function route_(user, req) {
  switch (req.action) {
    case 'session':
      return { user: user };
    case 'load':
      return { stories: readLive_().doc.stories || [], meetings: readMeetings_() };
    case 'publish':
      var story = clean_(req.story);
      return commit_(user, function (stories) {
        var i = stories.map(function (x) { return x.id; }).indexOf(story.id);
        if (i >= 0) stories[i] = story; else stories.push(story);
        return stories;
      }, (req.existing ? 'Update' : 'Publish') + ' story: ' + story.title);
    case 'remove':
      var id = String(req.id || '');
      var gone = null;
      return commit_(user, function (stories) {
        return stories.filter(function (x) { if (x.id === id) gone = x; return x.id !== id; });
      }, function () { return 'Remove story: ' + (gone ? gone.title : id); });
    default:
      fail_('error', 'Unknown action.');
  }
}

/* ── who is asking ─────────────────────────────────────────────────────── */

function verify_(idToken) {
  if (!idToken) fail_('signin', 'Not signed in.');
  var props = PropertiesService.getScriptProperties();
  // Google's tokeninfo endpoint checks the signature and expiry for us.
  var res = UrlFetchApp.fetch('https://oauth2.googleapis.com/tokeninfo?id_token=' +
    encodeURIComponent(idToken), { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) fail_('signin', 'Your sign-in has expired. Sign in again.');
  var t = JSON.parse(res.getContentText());
  if (!props.getProperty('GOOGLE_CLIENT_ID') || t.aud !== props.getProperty('GOOGLE_CLIENT_ID')) {
    fail_('signin', 'That sign-in was issued for a different site.');
  }
  if (t.iss !== 'accounts.google.com' && t.iss !== 'https://accounts.google.com') fail_('signin', 'Unrecognised sign-in.');
  if (String(t.email_verified) !== 'true') fail_('forbidden', 'This Google account has no verified email address.');
  if (Number(t.exp) * 1000 < Date.now()) fail_('signin', 'Your sign-in has expired. Sign in again.');

  var email = String(t.email || '').toLowerCase();
  var role = inList_(props.getProperty('PUBLISHER_EMAILS'), email) ? 'publisher'
    : inList_(props.getProperty('EDITOR_EMAILS'), email) ? 'editor' : null;
  if (!role) {
    console.warn('Refused sign-in from ' + email);
    fail_('forbidden', email + ' is not on the Lake Watch editor list. Ask the site owner to add you.');
  }
  return { email: email, name: t.name || email, picture: t.picture || '', role: role };
}

function inList_(csv, email) {
  return String(csv || '').toLowerCase().split(/[\s,;]+/).filter(String).indexOf(email) >= 0;
}

/* ── the story itself: never trust what the browser sends ──────────────── */

function clean_(s) {
  s = s || {};
  var src = s.source || {};
  function str(v, max, label) {
    v = String(v == null ? '' : v).trim();
    if (v.length > max) fail_('invalid', label + ' is too long (' + v.length + ' of ' + max + ' characters).');
    return v;
  }
  var story = {
    id: str(s.id, 120, 'ID'),
    date: str(s.date, 10, 'Date'),
    title: str(s.title, 200, 'Headline'),
    summary: str(s.summary, 1200, 'Summary'),
    category: str(s.category, 40, 'Category'),
    featured: s.featured === true,
    source: {
      kind: str(src.kind, 20, 'Link type'),
      label: str(src.label, 60, 'Link label'),
      url: str(src.url, 500, 'Link URL'),
      timestamp: str(src.timestamp, 12, 'Timestamp')
    }
  };
  if (!/^[a-z0-9][a-z0-9-]*$/.test(story.id)) fail_('invalid', 'The story ID is malformed.');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(story.date)) fail_('invalid', 'The date is missing.');
  if (!story.title || !story.summary) fail_('invalid', 'A headline and summary are both needed.');
  if (CATEGORIES.indexOf(story.category) < 0) fail_('invalid', 'Unknown category.');
  if (KINDS.indexOf(story.source.kind) < 0) fail_('invalid', 'Unknown link type.');
  if (!/^https?:\/\/[^\s"'<>]+$/.test(story.source.url)) fail_('invalid', 'The link must be a web address.');
  if (story.source.timestamp && !/^(\d{1,2}:)?\d{1,2}:\d{2}$/.test(story.source.timestamp)) {
    fail_('invalid', 'Timestamps look like 33:24 or 1:02:15.');
  }
  if (s.verified_by_reader !== true) {
    fail_('invalid', 'Tick the box confirming you checked the story against its source.');
  }
  story.published = new Date().toISOString();
  story.verified_by_reader = true;
  if (s.derived_from) story.derived_from = str(s.derived_from, 120, 'Origin');
  return story;
}

/* ── GitHub ────────────────────────────────────────────────────────────── */

function gh_(path, opts) {
  opts = opts || {};
  var token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) fail_('config', 'The backend has no GITHUB_TOKEN yet.');
  var params = {
    method: opts.method || 'get',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: opts.raw ? 'application/vnd.github.raw+json' : 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    muteHttpExceptions: true
  };
  if (opts.body) { params.contentType = 'application/json'; params.payload = JSON.stringify(opts.body); }
  var res = UrlFetchApp.fetch('https://api.github.com' + path, params);
  var code = res.getResponseCode(), text = res.getContentText();
  if (code === 404 && opts.allow404) return null;
  if (code >= 300) {
    var msg = 'GitHub ' + code;
    try { msg = JSON.parse(text).message || msg; } catch (e) {}
    if (code === 401) msg = 'GitHub rejected the backend token — it may have expired. The site owner needs to replace GITHUB_TOKEN.';
    fail_('github', msg);
  }
  if (opts.raw) return text;
  return text ? JSON.parse(text) : null;
}

function readLive_() {
  var f = gh_('/repos/' + REPO + '/contents/' + STORIES + '?ref=' + BASE, { allow404: true });
  if (!f) return { sha: null, doc: { schema: { name: 'stories', version: 1 }, updated: null, stories: [] } };
  var text = Utilities.newBlob(Utilities.base64Decode(f.content.replace(/\n/g, ''))).getDataAsString('UTF-8');
  return { sha: f.sha, doc: JSON.parse(text) };
}

function readMeetings_() {
  var repo = PropertiesService.getScriptProperties().getProperty('FINDINGS_REPO') || REPO;
  var ref = '?ref=' + encodeURIComponent(REVIEW_BRANCH);
  var list = gh_('/repos/' + repo + '/contents/' + MEETINGS_DIR + ref, { allow404: true }) || [];
  var token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  var files = list.filter(function (f) { return /\.json$/.test(f.name); });
  var responses = UrlFetchApp.fetchAll(files.map(function (f) {
    return {
      url: 'https://api.github.com/repos/' + repo + '/contents/' + MEETINGS_DIR + '/' + encodeURIComponent(f.name) + ref,
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github.raw+json',
                 'X-GitHub-Api-Version': '2022-11-28' },
      muteHttpExceptions: true
    };
  }));
  var out = [];
  responses.forEach(function (r) {
    if (r.getResponseCode() !== 200) return;
    try { out.push(JSON.parse(r.getContentText())); } catch (e) {}
  });
  return out;
}

// branch → commit → pull request → merge. main only ever changes through a PR,
// and the lock stops two people's publishes racing each other.
function commit_(user, mutate, message) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) fail_('busy', 'Someone else is publishing. Try again in a moment.');
  var steps = [];
  var branch = 'stories/' + Date.now();
  try {
    var cur = readLive_();                      // re-read now, so nobody's change is overwritten
    var doc = cur.doc;
    doc.stories = mutate(doc.stories || []);
    doc.updated = new Date().toISOString();
    if (typeof message === 'function') message = message();

    var head = gh_('/repos/' + REPO + '/git/ref/heads/' + BASE);
    gh_('/repos/' + REPO + '/git/refs', { method: 'post', body: { ref: 'refs/heads/' + branch, sha: head.object.sha } });
    steps.push('Created branch ' + branch);

    var body = { message: message, branch: branch,
                 content: Utilities.base64Encode(JSON.stringify(doc, null, 2) + '\n', Utilities.Charset.UTF_8) };
    if (cur.sha) body.sha = cur.sha;
    gh_('/repos/' + REPO + '/contents/' + STORIES, { method: 'put', body: body });
    steps.push('Committed the change');

    // The repository is public, so the PR says who in general terms only;
    // the email is in this script's execution log for the owner.
    var pr = gh_('/repos/' + REPO + '/pulls', { method: 'post', body: {
      title: message, head: branch, base: BASE,
      body: 'Published from the Lake Watch admin page by a signed-in ' + user.role + '.\n\n' +
            'They confirmed they checked the source link and that the story matches it.'
    } });
    steps.push('Opened pull request #' + pr.number);
    console.log(user.email + ' → ' + message + ' → ' + pr.html_url);

    if (user.role !== 'publisher') {
      steps.push('Waiting for a publisher to approve it.');
      return { steps: steps, pr: pr.html_url, merged: false };
    }
    try {
      gh_('/repos/' + REPO + '/pulls/' + pr.number + '/merge', { method: 'put', body: { merge_method: 'squash' } });
    } catch (e) {
      steps.push('Could not merge automatically: ' + e.message);
      return { steps: steps, pr: pr.html_url, merged: false };
    }
    try { gh_('/repos/' + REPO + '/git/refs/heads/' + branch, { method: 'delete' }); } catch (e) {}
    steps.push('Merged');
    return { steps: steps, pr: pr.html_url, merged: true, stories: doc.stories };
  } finally {
    lock.releaseLock();
  }
}

function fail_(code, message) {
  var e = new Error(message);
  e.code = code;
  throw e;
}
