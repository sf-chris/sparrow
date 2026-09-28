/* Every theme passes the same accessibility and layout checks as the official
   theme, and a person's choice applies at once, follows their account and
   paints before the app loads on the next visit. */
const { chromium } = require(process.env.SPARROW_PLAYWRIGHT || require.resolve('playwright', { paths: [process.cwd() + '/frontend'] }));
const AxeBuilder = require(require.resolve('@axe-core/playwright', { paths: [process.cwd() + '/frontend'] })).default;
const assert = require('node:assert/strict');
const fs = require('node:fs');

const out = process.env.SPARROW_VISUAL_OUT || 'tests/browser/artifacts/themes';
const base = process.env.SPARROW_BROWSER_URL || 'http://127.0.0.1:8891';
const headers = { 'X-Sparrow-Request': '1' };
const themes = ['', 'cinema', 'clear', 'saturday'];
const routes = ['/', '/discover', '/activity', '/settings', '/settings/people', '/settings/storage', '/title/tv/101'];
fs.mkdirSync(out, { recursive: true });

const summarise = audit => audit.violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) }));
const frame = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.SPARROW_CHROME || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  const login = await context.request.post(base + '/api/v1/auth/login', { headers, data: { username: 'owner', password: 'fixture-password-123' } });
  assert.equal(login.status(), 200, 'Fixture login failed');
  const page = await context.newPage();
  const results = [];
  try {
    for (const theme of themes) {
      const saved = await context.request.put(base + '/api/v1/appearance', { headers, data: { theme } });
      assert.equal(saved.status(), 200, await saved.text());
      for (const route of routes) {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto(base + route);
        await page.locator('h1').first().waitFor();
        await page.evaluate(() => document.fonts.ready);
        assert.equal(await page.evaluate(() => document.documentElement.dataset.theme || ''), theme, `${route} did not apply "${theme}"`);
        const violations = [];
        const overflow = [];
        for (const width of [360, 390, 768, 1440]) {
          await page.setViewportSize({ width, height: 844 });
          await frame(page);
          await page.waitForTimeout(100);
          if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) overflow.push(width);
          if (width === 390 || width === 1440) violations.push(...summarise(await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()));
        }
        results.push({ theme: theme || 'guide', route, violations, overflow });
      }
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(base + '/title/tv/101');
      await page.getByRole('button', { name: /Request episodes/ }).click();
      await page.getByRole('dialog').waitFor();
      await page.getByRole('checkbox').first().check();
      results.push({ theme: theme || 'guide', route: 'episode-dialog', violations: summarise(await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()), overflow: [] });
      await page.screenshot({ path: `${out}/${theme || 'guide'}-request-mobile.png` });
    }

    // Choosing a theme applies it at once and follows the account after a reload.
    await context.request.put(base + '/api/v1/appearance', { headers, data: { theme: '' } });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(base + '/settings');
    await page.locator('.theme-card', { hasText: 'Saturday' }).click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'saturday');
    await page.waitForLoadState('networkidle');
    await page.reload();
    await page.locator('h1').first().waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'saturday');
    assert(await page.getByRole('radio', { name: /^Saturday/ }).isChecked(), 'The chosen theme is not shown as chosen');
    const status = await context.request.get(base + '/api/v1/auth/status');
    assert.equal((await status.json()).user.theme, 'saturday');

    // The next visit on this device paints the theme before the app has loaded.
    const guest = await browser.newContext();
    await guest.addInitScript(() => localStorage.setItem('sparrow-theme', 'cinema'));
    const cover = await guest.newPage();
    let painted = '';
    cover.on('domcontentloaded', async () => { painted = await cover.evaluate(() => document.documentElement.dataset.theme || '').catch(() => ''); });
    await cover.goto(base + '/login');
    await cover.locator('h1').waitFor();
    assert.equal(painted, 'cinema', 'The saved theme was not applied before the app loaded');
    await guest.close();

    await page.locator('.theme-card', { hasText: 'Guide' }).click();
    await page.waitForFunction(() => !document.documentElement.dataset.theme);
    await page.waitForLoadState('networkidle');
  } finally {
    await context.request.put(base + '/api/v1/appearance', { headers, data: { theme: '' } }).catch(() => {});
  }
  fs.writeFileSync(out + '/theme-results.json', JSON.stringify(results, null, 2));
  const failures = results.filter(r => r.violations.length || r.overflow.length);
  console.log(JSON.stringify({ passed: !failures.length, themes: themes.length, audits: results.length, failures }));
  await browser.close();
  if (failures.length) process.exitCode = 1;
})().catch(error => { console.error(error); process.exit(1); });
