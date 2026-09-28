/* Before/after tour: every page at desktop and phone width, against a fresh fictional fixture.
   BASE=http://127.0.0.1:8891 OUT=docs/redesign/after STATE=$SPARROW_BROWSER_STATE node tests/browser/tour.cjs */
const { chromium } = require(require.resolve('playwright', { paths: [process.cwd() + '/frontend'] }));
const fs = require('fs'), path = require('path');
const base = process.env.BASE, out = process.env.OUT, state = process.env.STATE;
const only = process.env.ONLY ? process.env.ONLY.split(',') : null;
fs.mkdirSync(out, { recursive: true });
const H = { 'X-Sparrow-Request': '1' };
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.SPARROW_CHROME || '/usr/bin/google-chrome', args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] });
  const opts = { reducedMotion: 'reduce', serviceWorkers: 'block', locale: 'en-NZ', timezoneId: 'Pacific/Auckland', colorScheme: process.env.SCHEME || 'light' };
  const guest = await browser.newContext(opts);
  async function shoot(ctx, name, route, prep) {
    if (only && !only.includes(name)) return;
    const page = await ctx.newPage();
    for (const [size, w, h] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
      await page.setViewportSize({ width: w, height: h });
      await page.goto(base + route);
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.locator('h1, h2').first().waitFor({ timeout: 10000 }).catch(() => {});
      if (prep) await prep(page);
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(500);
      const dialog = await page.getByRole('dialog').count();
      await page.screenshot({ path: path.join(out, `${name}-${size}.png`), fullPage: !dialog });
    }
    await page.close();
  }
  const status = await (await guest.request.get(base + '/api/v1/auth/status')).json();
  if (status.needs_setup) {
    await shoot(guest, 'landing', '/');
    await shoot(guest, 'setup-account', '/setup');
    const code = fs.readFileSync(path.join(state, 'owner-setup-code'), 'utf8').trim();
    const owner0 = await browser.newContext(opts);
    const r = await owner0.request.post(base + '/api/v1/auth/bootstrap', { headers: H, data: { name: 'Chris', username: 'owner', password: 'fixture-password-123', setup_code: code, invitation: '' } });
    if (r.status() !== 200) throw new Error('bootstrap ' + r.status() + await r.text());
    await shoot(owner0, 'welcome-defaults', '/');
    await owner0.close();
  }
  const owner = await browser.newContext(opts);
  const login = await owner.request.post(base + '/api/v1/auth/login', { headers: H, data: { username: 'owner', password: 'fixture-password-123' } });
  if (login.status() !== 200) throw new Error('login failed');
  const me = (await (await owner.request.get(base + '/api/v1/auth/status')).json()).user;
  if (!me.welcomed) await owner.request.patch(base + '/api/v1/preferences', { headers: H, data: { values: {} } });
  await shoot(owner, 'onboarding', '/setup');
  await owner.request.patch(base + '/api/v1/admin/onboarding', { headers: H, data: { deferred: true } });
  // Seed watch progress and a request.
  const catalogue = await (await owner.request.get(base + '/api/v1/catalogue')).json();
  for (const t of ['The Quiet Planet', 'Harbour Lights']) {
    const a = catalogue.find(i => i.title === t).assets[0];
    if (a.watch && a.watch.position > 5) continue;
    const s = await (await owner.request.post(base + '/api/v1/playback', { headers: H, data: { asset_id: a.id, supported_video: ['h264'] } })).json();
    await owner.request.put(base + '/api/v1/playback/' + s.id + '/progress', { headers: H, data: { position: t === 'Harbour Lights' ? 14 : 22, sequence: 1, ended: false } });
    await owner.request.delete(base + '/api/v1/playback/' + s.id, { headers: H });
  }
  const jobs = await (await owner.request.get(base + '/api/v1/jobs')).json();
  if (!jobs.length) await owner.request.post(base + '/api/v1/jobs', { headers: H, data: { tmdb_id: 104, media_type: 'tv', wanted_episodes: { 1: [1, 2, 3] }, node_id: 'local', monitoring: 'exact', preferences: {} } });
  const movie = catalogue.find(i => i.title === 'The Quiet Planet').assets[0].id;
  await shoot(guest, 'login', '/login');
  const inv = await (await owner.request.post(base + '/api/v1/admin/invitations', { headers: H, data: { role: 'viewer', library_scope: null } })).json();
  await shoot(guest, 'join', inv.path);
  await shoot(owner, 'home', '/');
  await shoot(owner, 'library', '/library');
  await shoot(owner, 'title-series', '/title/tv/101');
  await shoot(owner, 'title-film', '/title/movie/102');
  await shoot(owner, 'request-dialog', '/title/tv/101', async p => { await p.getByRole('button', { name: /Choose episodes/ }).click(); await p.getByRole('dialog').waitFor(); await p.waitForTimeout(800); const c = p.getByRole('dialog').getByRole('checkbox').first(); if (await c.count()) await c.check(); });
  await shoot(owner, 'player', '/watch/' + movie, async p => { await p.locator('video').waitFor(); await p.waitForFunction(() => document.querySelector('video')?.readyState >= 2, null, { timeout: 15000 }).catch(() => {}); await p.locator('video').evaluate(v => { v.pause(); v.currentTime = 12; }); });
  await shoot(owner, 'discover', '/discover');
  await shoot(owner, 'discover-results', '/discover?q=Harbour', async p => { await p.waitForTimeout(1200); });
  await shoot(owner, 'activity', '/activity');
  await shoot(owner, 'preferences', '/settings');
  await shoot(owner, 'security', '/settings/security');
  await shoot(owner, 'logs', '/settings/logs');
  await shoot(owner, 'people', '/settings/people');
  await shoot(owner, 'storage', '/settings/storage');
  await shoot(owner, 'defaults', '/settings/defaults');
  await shoot(owner, 'server', '/settings/server');
  await browser.close();
  console.log('captured', fs.readdirSync(out).length);
})().catch(e => { console.error(e); process.exit(1); });
