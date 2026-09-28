/* Capture the real application with the isolated, fictional browser fixtures. */
const { chromium } = require(require.resolve('playwright', { paths: [process.cwd() + '/frontend'] }));
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.SPARROW_BROWSER_URL || 'http://127.0.0.1:8891';
const out = process.env.SPARROW_SCREENSHOT_OUT || 'docs/screenshots';

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.SPARROW_CHROME || '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox'],
  });
  try {
    const options = { reducedMotion: 'reduce', serviceWorkers: 'block', locale: 'en-NZ', timezoneId: 'Pacific/Auckland' };
    const guest = await browser.newContext(options);
    const owner = await browser.newContext(options);
    const login = await owner.request.post(base + '/api/v1/auth/login', {
      headers: { 'X-Sparrow-Request': '1' },
      data: { username: 'owner', password: 'fixture-password-123' },
    });
    assert.equal(login.status(), 200, 'Start the isolated fixture and run the viewing journey first.');
    // Guard against accidentally photographing a personal collection.
    const catalogue = await (await owner.request.get(base + '/api/v1/catalogue')).json();
    const fixtures = new Set(['Harbour Lights', 'The Quiet Planet', 'A Long Way Home', 'Northern Signal', 'Tomorrow Bay', 'The Last Evening']);
    assert.equal(catalogue.length, fixtures.size);
    assert(catalogue.every(item => fixtures.has(item.title)), 'Screenshots require the fictional fixture catalogue.');
    const errors = [];
    for (const [name, route, context, ready] of [
      ['landing', '/', guest, '.sp-front-hero'],
      ['home', '/', owner, '.sp-hero'],
      ['library', '/library', owner, '.sp-poster'],
      ['discover', '/discover', owner, '.sp-find'],
      ['title', '/title/tv/101', owner, '.sp-marquee'],
      ['preferences', '/settings', owner, '.sp-preference-groups'],
    ]) {
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      for (const [size, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
        await page.setViewportSize({ width, height });
        await page.goto(base + route);
        await page.locator(ready).first().waitFor();
        await page.evaluate(() => document.fonts.ready);
        await page.evaluate(() => Promise.all([...document.images].map(image => image.decode().catch(() => {}))));
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, name + ' overflows');
        assert.equal(await page.locator('img').evaluateAll(images => images.some(image => !image.naturalWidth)), false, name + ' has broken images');
        // Viewport captures preserve a natural phone frame and its fixed navigation.
        await page.screenshot({ path: path.join(out, `${name}-${size}.png`) });
      }
      await page.close();
    }
    assert.deepEqual(errors, []);
    console.log('Captured 12 documentation screenshots from the real application and fictional fixtures.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
