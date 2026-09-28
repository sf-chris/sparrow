/* Visual acceptance against isolated data. No account is created by this check. */
const { chromium } = require(process.env.SPARROW_PLAYWRIGHT || require.resolve('playwright', { paths: [process.cwd() + '/frontend'] }));
const AxeBuilder = require(require.resolve('@axe-core/playwright', { paths: [process.cwd() + '/frontend'] })).default;
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.SPARROW_CHROME || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const page = await context.newPage(), results = [], errors = [];
  const base = process.env.SPARROW_BROWSER_URL || 'http://127.0.0.1:8891', out = process.env.SPARROW_VISUAL_OUT || 'docs/product-validation';
  page.on('pageerror', error => errors.push(error.message));
  const setup = (await (await context.request.get(base + '/api/v1/auth/status')).json()).needs_setup;
  fs.mkdirSync(out, { recursive: true });
  await page.goto(base); await page.locator('h1').waitFor();
  async function audit(name, widths = [360, 390, 768, 1440]) {
    for (const width of widths) {
      await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
      // Media queries apply on the next frame after an emulated resize.
      await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
      await page.waitForTimeout(150);
      await page.evaluate(() => Promise.all([document.fonts.load('400 16px "Archivo"'), document.fonts.load('900 48px "Archivo"')]));
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => Promise.all([...document.images].map(image => image.decode().catch(() => {}))));
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth,
        offenders: [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => `${el.tagName.toLowerCase()}.${el.getAttribute('class') || ''}`).slice(0, 6),
        fonts: [...document.fonts].some(f => f.family.replace(/"/g, '') === 'Archivo' && f.status === 'loaded'),
        brokenImages: [...document.images].filter(i => !i.complete || !i.naturalWidth).length,
        clippedControls: [...document.querySelectorAll('button, input, select, textarea')].filter(el => {
          const r = el.getBoundingClientRect();
          return r.width && r.height && (r.left < -1 || r.right > innerWidth + 1);
        }).map(el => el.getAttribute('aria-label') || el.textContent.trim()),
      }));
      const a = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      results.push({ name, width, ...layout, violations: a.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) })) });
      if (width === 390 || width === 1440) await page.screenshot({ path: path.join(out, `${name}-${width === 390 ? 'mobile' : 'desktop'}.png`), fullPage: true });
    }
  }
  await audit('landing');
  await page.getByRole('link', { name: setup ? 'Set up Sparrow' : 'Sign in', exact: true }).click();
  await page.getByLabel('Username', { exact: true }).waitFor();
  await audit(setup ? 'administrator-setup' : 'sign-in');
  if (setup) {
    const code = fs.readFileSync(path.join(process.env.SPARROW_BROWSER_STATE || '/tmp/sparrow-browser-check', 'owner-setup-code'), 'utf8').trim();
    await page.goto(base + '/#setup_code=' + encodeURIComponent(code));
    await page.waitForFunction(() => !location.hash);
    if (await page.getByLabel('Setup code', { exact: true }).inputValue() !== code) throw new Error('Setup link did not prefill');
  } else {
    const login = await context.request.post(base + '/api/v1/auth/login', { headers: { 'X-Sparrow-Request': '1' }, data: { username: 'owner', password: 'fixture-password-123' } });
    if (login.status() !== 200) throw new Error('Fixture login failed');
    await page.goto(base + '/discover');
    await page.getByRole('button', { name: /A mystery for tonight/ }).click();
    const prompt = page.getByRole('searchbox', { name: 'Find a title or describe a mood', exact: true });
    if (await prompt.inputValue() !== 'A mystery for tonight') throw new Error('Discovery suggestion did not fill the prompt');
    if (!new URL(page.url()).searchParams.get('q')) throw new Error('Discovery draft not preserved in URL');
    if (await page.getByRole('button', { name: 'Ask Sparrow', exact: true }).isDisabled()) throw new Error('A filled draft should offer Ask Sparrow');
    await audit('discovery-prompt', [390, 1440]);
    await page.reload(); await prompt.waitFor();
    if (await prompt.inputValue() !== 'A mystery for tonight') throw new Error('Discovery prompt lost after reload');
    // Controlled UI states only; the server's collection stays intact.
    await page.route('**/api/v1/catalogue', route => route.fulfill({ json: [] }));
    await page.goto(base + '/library'); await page.getByRole('heading', { name: 'Nothing listed yet.' }).waitFor();
    await audit('empty-library', [390, 1440]);
    await page.unroute('**/api/v1/catalogue');
    await page.route('**/api/v1/catalogue', route => route.fulfill({ status: 503, json: { detail: 'The collection is temporarily unavailable. Try again.' } }));
    await page.reload(); await page.getByRole('alert').waitFor();
    if (await page.getByRole('heading', { name: /^(No match|Nothing listed yet)\./ }).count()) throw new Error('Load failure was presented as an empty result');
    await audit('library-error', [390, 1440]);
    await page.unroute('**/api/v1/catalogue');
    await page.getByRole('button', { name: 'Try again', exact: true }).click();
    await page.getByRole('link', { name: 'Open The Quiet Planet' }).waitFor();
    if (await page.getByRole('alert').count()) throw new Error('Retry did not recover');
  }
  fs.writeFileSync(path.join(out, setup ? 'administrator-setup-results.json' : 'redesign-results.json'), JSON.stringify({ errors, results }, null, 2));
  await browser.close();
  const failures = results.filter(r => r.overflow || !r.fonts || r.brokenImages || r.clippedControls.length || r.violations.length);
  if (errors.length || failures.length) throw new Error(JSON.stringify({ errors, failures }));
  console.log(JSON.stringify({ passed: true, screens: results.length, setup }));
})().catch(error => { console.error(error); process.exit(1); });
