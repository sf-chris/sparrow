/* Photograph every screen from a fresh fictional fixture, for before/after design review.
   Seeds the same household state through the API so any frontend can be compared fairly. */
const { chromium } = require(require.resolve('playwright', { paths: [process.cwd() + '/frontend'] }));
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.SPARROW_BROWSER_URL || 'http://127.0.0.1:8891';
const state = process.env.SPARROW_BROWSER_STATE || '/tmp/sparrow-browser-check';
const out = path.resolve(process.env.SPARROW_GALLERY_OUT || 'docs/redesign/after');
const only = process.env.SPARROW_GALLERY_ONLY ? new Set(process.env.SPARROW_GALLERY_ONLY.split(',')) : null;
const headers = { 'X-Sparrow-Request': '1' };
const sizes = [['desktop', 1440, 900], ['mobile', 390, 844]];
const owner = { username: 'owner', password: 'fixture-password-123' };

async function call(context, method, route, data) {
  const response = await context.request.fetch(base + '/api/v1' + route, { method, headers, data });
  assert(response.ok(), `${method} ${route} → ${response.status()} ${await response.text()}`);
  return response.json();
}

async function settle(page) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.locator('h1, dialog[open]').first().waitFor({ timeout: 20000 });
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => Promise.all([...document.images].map(image => image.decode().catch(() => {}))));
  await page.waitForTimeout(500);
}

async function capture(context, name, route, prepare) {
  if (only && !only.has(name)) return;
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const [size, width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await page.goto(base + route);
    await settle(page);
    if (prepare) {
      await prepare(page);
      await page.waitForTimeout(400);
    }
    // A full-page capture would paint fixed bottom navigation mid-page; pin it to the end.
    await page.evaluate(() => {
      const end = document.documentElement.scrollHeight;
      for (const element of document.querySelectorAll('body *')) {
        const style = getComputedStyle(element);
        if (style.position !== 'fixed' || element.closest('dialog') || style.bottom !== '0px') continue;
        element.style.position = 'absolute';
        element.style.top = `${end - element.offsetHeight}px`;
        element.style.bottom = 'auto';
      }
    });
    await page.screenshot({ path: path.join(out, `${name}-${size}.png`), fullPage: true });
  }
  await page.close();
  assert.deepEqual(errors, [], `${name} raised page errors`);
  console.log('captured', name);
}

const open = pattern => async page => {
  await page.getByRole('button', { name: pattern }).first().click();
  await page.locator('dialog[open]').waitFor();
  await page.waitForLoadState('networkidle').catch(() => {});
};

async function playFrame(page) {
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2, null, { timeout: 20000 });
  await page.locator('video').evaluate(video => { video.pause(); video.currentTime = 8; });
  await page.waitForFunction(() => { const video = document.querySelector('video'); return video && !video.seeking && video.readyState >= 2; });
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.SPARROW_CHROME || '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
  });
  const options = { reducedMotion: 'reduce', serviceWorkers: 'block', locale: 'en-NZ', timezoneId: 'Pacific/Auckland' };
  try {
    const guest = await browser.newContext(options);
    const status = await call(guest, 'GET', '/auth/status');
    assert(status.needs_setup, 'The gallery needs a fresh fixture state.');
    await capture(guest, 'landing-first-run', '/');
    await capture(guest, 'account-setup', '/setup');

    const household = await browser.newContext(options);
    const code = fs.readFileSync(path.join(state, 'owner-setup-code'), 'utf8').trim();
    await call(household, 'POST', '/auth/bootstrap', { name: 'Chris', ...owner, setup_code: code, invitation: '' });
    const catalogue = await call(household, 'GET', '/catalogue');
    assert.equal(catalogue.length, 6, 'Screens require the fictional fixture catalogue.');

    await capture(guest, 'landing', '/');
    await capture(guest, 'sign-in', '/login');
    await capture(household, 'welcome', '/');
    await call(household, 'PATCH', '/preferences', { values: {} });
    await capture(household, 'server-setup', '/setup');
    await call(household, 'PATCH', '/admin/onboarding', { mode: 'library', step: 'review' });
    await capture(household, 'server-setup-review', '/setup');
    await call(household, 'PATCH', '/admin/onboarding', { deferred: true });

    // Two titles in progress, two requests, one followed show and a second person.
    const asset = title => catalogue.find(item => item.title === title).assets[0].id;
    for (const [title, position] of [['Harbour Lights', 9], ['The Quiet Planet', 15]]) {
      const session = await call(household, 'POST', '/playback', { asset_id: asset(title), supported_video: ['h264'] });
      await call(household, 'PUT', `/playback/${session.id}/progress`, { position, sequence: 1, ended: false });
      await call(household, 'DELETE', `/playback/${session.id}`);
    }
    await call(household, 'POST', '/jobs', { tmdb_id: 104, media_type: 'tv', wanted_episodes: { 2: [1, 2, 3] }, node_id: 'local', monitoring: 'exact', preferences: {} });
    const paused = await call(household, 'POST', '/jobs', { tmdb_id: 101, media_type: 'tv', wanted_episodes: { 2: [1] }, node_id: 'local', monitoring: 'keep_current', preferences: {} });
    await call(household, 'POST', `/jobs/${paused.id || paused.job?.id}/pause`).catch(() => {});
    await call(household, 'PUT', '/subscriptions', { tmdb_id: 101, media_type: 'tv', node_id: 'local', mode: 'keep_current', enabled: true, upgrades: false, seasons: [] });
    const invitation = await call(household, 'POST', '/admin/invitations', { role: 'requester', library_scope: null });
    const member = await browser.newContext(options);
    await call(member, 'POST', '/auth/join', { name: 'Alex', username: 'alex', password: 'fixture-password-456', setup_code: '', invitation: invitation.path.split('#')[1] });
    const spare = await call(household, 'POST', '/admin/invitations', { role: 'viewer', library_scope: null });

    await capture(guest, 'join', spare.path);
    await capture(household, 'home', '/');
    await capture(household, 'library', '/library');
    await capture(household, 'discover', '/discover');
    await capture(household, 'discover-results', '/discover?q=the');
    await capture(household, 'title-show', '/title/tv/101');
    await capture(household, 'title-movie', '/title/movie/102');
    await capture(household, 'request', '/title/tv/104', open(/^(Choose episodes|Get episodes|Request)/));
    await capture(household, 'player', `/watch/${asset('The Quiet Planet')}`, playFrame);
    await capture(household, 'activity', '/activity');
    await capture(household, 'preferences', '/settings');
    await capture(household, 'account', '/settings/security');
    await capture(household, 'logs', '/settings/logs');
    await capture(household, 'people', '/settings/people');
    await capture(household, 'invite', '/settings/people', open(/^Invite/));
    await capture(household, 'storage', '/settings/storage');
    await capture(household, 'defaults', '/settings/defaults');
    await capture(household, 'server', '/settings/server');
    console.log(`Gallery written to ${out}`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
