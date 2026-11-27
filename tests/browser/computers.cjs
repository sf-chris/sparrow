/* The Computers page: every computer with its jobs, a storage-only computer,
   one that can't download yet, and the dialogs that change them. Photographs
   each at phone, laptop and TV widths and fails on page errors. */
const { chromium } = require(require.resolve('playwright', { paths: [process.cwd() + '/frontend'] }));
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.SPARROW_BROWSER_URL || 'http://127.0.0.1:8891';
const state = process.env.SPARROW_BROWSER_STATE || '/tmp/sparrow-browser-check';
const out = path.resolve(process.env.SPARROW_VISUAL_OUT || 'tests/browser/artifacts/computers');
const headers = { 'X-Sparrow-Request': '1' };
const sizes = [['tv', 1920, 1080], ['laptop', 1440, 900], ['phone', 390, 844], ['small', 360, 740]];
const GB = 1024 ** 3;

async function call(context, method, route, data, extra = {}) {
  const response = await context.request.fetch(base + '/api/v1' + route, { method, headers: { ...headers, ...extra }, data });
  assert(response.ok(), `${method} ${route} → ${response.status()} ${await response.text()}`);
  return response.json();
}

function capabilities(name, { download, roots, speech, release }) {
  return {
    protocol: 1, platform: 'Linux', hostname: name, folders: true, probe: true, transcode: true,
    subtitle_model: false, download, downloader_state: download ? 'running' : 'none', roots,
    ...(speech ? { speech } : {}),
    // Set up before updates existed: no version. Otherwise it updates itself.
    ...(release ? { version: release.version, release: { commit: release.commit, date: release.date }, self_update: true, update: { state: 'idle' } } : {}),
  };
}

const disk = (id, label, free, total, key) => ({ id, available: true, writable: true, free_bytes: free, total_bytes: total, disk: key, label });

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.SPARROW_CHROME || '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox'],
  });
  const options = { reducedMotion: 'reduce', serviceWorkers: 'block', locale: 'en-NZ', timezoneId: 'Pacific/Auckland' };
  const keepAlive = [];
  let stop = false;
  try {
    const owner = await browser.newContext(options);
    const status = await call(owner, 'GET', '/auth/status');
    if (status.needs_setup) {
      const code = fs.readFileSync(path.join(state, 'owner-setup-code'), 'utf8').trim();
      await call(owner, 'POST', '/auth/bootstrap', { name: 'Chris', username: 'owner', password: 'fixture-password-123', setup_code: code, invitation: '' });
    } else {
      await call(owner, 'POST', '/auth/login', { username: 'owner', password: 'fixture-password-123' });
    }
    await call(owner, 'PATCH', '/preferences', { values: {} });
    await call(owner, 'PATCH', '/admin/onboarding', { mode: 'library', step: 'review' }).catch(() => {});
    await call(owner, 'PATCH', '/admin/onboarding', { deferred: true }).catch(() => {});

    // The server's own version: computers that run it are up to date.
    const latest = (await call(owner, 'GET', '/nodes')).find((n) => n.id === 'local').health.update.latest;
    const older = { version: 'older1', commit: '7cda240', date: '2026-11-20T10:17:08+13:00' };
    // Paired computers, kept online by checking in like Sparrow Node does.
    const computers = [
      ['antivac', capabilities('antivac', {
        download: false,
        roots: [disk('library', '/home/oem/Sparrow/Library', 63 * GB, 471 * GB, '1'), disk('staging', '/home/oem/Sparrow/Incoming', 63 * GB, 471 * GB, '1')],
      })],
      ['nas', capabilities('nas', {
        download: false,
        roots: [disk('library', '/volume1/Films and series', 5.1e12, 8e12, '2')],
        release: older,
      })],
      ['gaming-pc', capabilities('gaming-pc', {
        download: false, roots: [],
        speech: { ready: true, device: 'cuda', gpu: 'GeForce RTX 4080' },
        release: latest,
      })],
    ];
    const ids = {};
    for (const [name, caps] of computers) {
      const enrollment = await call(owner, 'POST', '/admin/nodes/enroll', { name });
      const credential = crypto.randomBytes(32).toString('base64url');
      await call(owner, 'POST', '/node/pair', { code: enrollment.code, credential, capabilities: caps });
      ids[name] = enrollment.node_id;
      const node = await browser.newContext(options);
      const loop = (async () => {
        while (!stop) {
          await node.request.fetch(base + '/api/v1/node/poll', {
            method: 'POST', headers: { Authorization: 'Bearer ' + credential }, data: { capabilities: caps }, timeout: 30000,
          }).catch(() => {});
        }
      })();
      keepAlive.push(loop);
    }
    await call(owner, 'PUT', `/admin/nodes/${ids.nas}/jobs`, { storage: true, downloads: false, subtitles: false });
    await call(owner, 'PUT', `/admin/nodes/${ids['gaming-pc']}/jobs`, { storage: false, downloads: false, subtitles: true });
    await call(owner, 'PUT', `/admin/nodes/${ids.nas}/settings`, { auto_update: false });
    const listed = await call(owner, 'GET', '/nodes');
    const anti = listed.find((n) => n.name === 'antivac');
    assert.equal(anti.health.rows.find((r) => r.job === 'downloads').line, 'No download app');
    assert.equal(listed.find((n) => n.name === 'nas').health.attention, 0);
    const updates = Object.fromEntries(listed.map((n) => [n.name, n.health.update.state]));
    assert.deepEqual(updates, { 'This server': 'current', antivac: 'unsupported', nas: 'available', 'gaming-pc': 'current' });

    const page = await owner.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const shoot = async (name, prepare) => {
      for (const [size, width, height] of sizes) {
        await page.setViewportSize({ width, height });
        await page.goto(base + '/settings/computers');
        await page.getByRole('heading', { name: 'antivac' }).waitFor({ timeout: 20000 });
        await page.evaluate(() => document.fonts.ready);
        if (prepare) await prepare(page);
        await page.waitForTimeout(300);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        assert(overflow <= 0, `${name} at ${size} scrolls sideways by ${overflow}px`);
        await page.screenshot({ path: path.join(out, `${name}-${size}.png`), fullPage: !prepare });
      }
      console.log('captured', name);
    };
    await shoot('computers');
    await shoot('jobs', async (p) => {
      await p.locator('#device-' + ids.antivac).getByRole('button', { name: 'Change jobs' }).click();
      await p.locator('dialog[open]').waitFor();
    });
    await shoot('fix', async (p) => {
      await p.locator('#device-' + ids.antivac).getByRole('button', { name: 'Fix' }).first().click();
      await p.locator('dialog[open]').waitFor();
      await p.getByText(/curl -fsSL/).first().waitFor().catch(() => {});
    });
    await shoot('add', async (p) => {
      await p.getByRole('button', { name: 'Add a computer' }).click();
      await p.locator('dialog[open]').waitFor();
    });
    // Storage can't be turned off on every computer at once.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(base + '/settings/computers');
    await page.getByRole('heading', { name: 'antivac' }).waitFor();
    assert.deepEqual(errors, [], 'the Computers page raised page errors');
    console.log(`Computers written to ${out}`);
  } finally {
    stop = true;
    await browser.close();
    await Promise.race([Promise.allSettled(keepAlive), new Promise((r) => setTimeout(r, 1000))]);
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
