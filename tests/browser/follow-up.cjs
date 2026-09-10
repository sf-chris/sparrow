/* Design follow-up journey. Use the isolated fixture server after check.cjs. */
const { chromium } = require(
  process.env.SPARROW_PLAYWRIGHT ||
    require.resolve("playwright", { paths: [process.cwd() + "/frontend"] }),
);
const AxeBuilder = require(
  require.resolve("@axe-core/playwright", {
    paths: [process.cwd() + "/frontend"],
  }),
).default;
const fs = require("node:fs");
const assert = require("node:assert/strict");
const base = process.env.SPARROW_BROWSER_URL || "http://127.0.0.1:8891";
const { expect } = require(require.resolve("@playwright/test", { paths: [process.cwd() + "/frontend"] }));
const out = process.env.SPARROW_VISUAL_OUT || "docs/follow-up-validation";
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.SPARROW_CHROME || "/usr/bin/google-chrome",
    headless: true,
    args: ["--no-sandbox"],
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  const login = await context.request.post(base + "/api/v1/auth/login", {
    headers: { "X-Sparrow-Request": "1" },
    data: { username: "owner", password: "fixture-password-123" },
  });
  assert.equal(login.status(), 200);
  const page = await context.newPage();
  const errors = [],
    results = [];
  page.on("pageerror", (error) => errors.push(error.message));
  async function go(path) {
    await page.goto(base + path);
    await page.locator("h1").waitFor();
    await page.waitForTimeout(150);
  }
  async function audit(name) {
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: width < 700 ? 844 : 1000 });
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() =>
        Promise.all(
          [...document.images].map((image) => image.decode().catch(() => {})),
        ),
      );
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      );
      const violations = (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations.map((v) => ({
        id: v.id,
        nodes: v.nodes.map((n) => n.target),
      }));
      results.push({ name, width, overflow, violations });
      assert.equal(overflow, false, JSON.stringify(results.at(-1)));
      assert.equal(violations.length, 0, JSON.stringify(results.at(-1)));
      if (name === "security" && width > 900) {
        const heading = await page.locator(".sp-page-heading").boundingBox();
        const navigation = await page.locator(".sp-settings-nav").boundingBox();
        assert(
          Math.abs(heading.y - navigation.y) < 4,
          "Settings content must start beside its navigation",
        );
      }
      if ([390, 1440].includes(width))
        await page.screenshot({
          path: `${out}/${name}-${width}.png`,
          fullPage: true,
        });
    }
  }
  await go("/");
  await page.locator(".sp-continue").first().waitFor();
  assert.equal(await page.locator(".sp-cinema-feature").count(), 0);
  assert.equal(await page.locator(".sp-curiosity-card").count(), 0);
  await audit("home");
  await page.setViewportSize({ width: 360, height: 844 });
  assert.equal(await page.locator(".sp-header-search").count(), 0);
  await page
    .locator(".sp-bottom-nav")
    .getByRole("link", { name: "Discover", exact: true })
    .click();
  assert.equal(new URL(page.url()).pathname, "/discover");
  await audit("discover");
  await go("/library");
  await audit("library");
  await go("/settings");
  assert.equal(
    await page.getByText("Manage your server", { exact: true }).count(),
    0,
  );
  assert.equal(
    await page.getByText("Signed-in browsers", { exact: true }).count(),
    0,
  );
  const signout = await page
    .getByRole("button", { name: "Sign out", exact: true })
    .boundingBox();
  assert(
    signout && signout.y >= 0 && signout.y + signout.height <= 844,
    "Sign out must be visible without scrolling preferences",
  );
  for (const name of [
    "Storage & import",
    "People",
    "Server settings",
    "Household defaults",
    "Logs",
    "Account & security",
  ]) {
    assert.equal(
      await page
        .getByRole("navigation", { name: "Settings navigation" })
        .getByRole("link", { name, exact: true })
        .count(),
      1,
    );
  }
  await audit("preferences");
  await go("/settings/security");
  await page.getByRole("heading", { name: "This browser" }).waitFor();
  await audit("security");
  const changePassword = page.getByRole("button", {
    name: "Change your password",
    exact: true,
  });
  await changePassword.click();
  await page
    .getByRole("dialog", { name: "Change password", exact: true })
    .waitFor();
  await audit("password-dialog");
  await page.keyboard.press("Escape");
  assert.equal(
    await changePassword.evaluate(
      (button) => button === document.activeElement,
    ),
    true,
  );
  const other = await browser.newContext();
  await other.request.post(base + "/api/v1/auth/login", {
    headers: { "X-Sparrow-Request": "1" },
    data: { username: "owner", password: "fixture-password-123" },
  });
  await page.reload();
  await page.getByRole("heading", { name: "This browser" }).waitFor();
  const sessionsBefore = await page
    .getByRole("button", { name: "End session", exact: true })
    .count();
  await page
    .getByRole("button", { name: "End session", exact: true })
    .first()
    .click();
  await page.waitForFunction(
    (count) => document.querySelectorAll(".sp-row button").length < count,
    sessionsBefore,
  );
  await other.close();
  await go("/settings/logs?limit=1");
  await page.locator(".sp-log-entry").first().waitFor();
  await audit("logs");
  const firstEntry = page.locator(".sp-log-entry").first();
  const pageStatus = page.getByRole("navigation", { name: "Log pages" }).getByRole("status");
  // Different events can share a summary and displayed second. Include the
  // collapsed context, and wait for the response's page number to be rendered.
  const first = await firstEntry.textContent();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  assert.equal(new URL(page.url()).searchParams.get("page"), "2");
  assert(new URL(page.url()).searchParams.has("snapshot"));
  await expect(pageStatus).toContainText("Page 2 of");
  await expect.poll(() => firstEntry.textContent()).not.toBe(first);
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await expect(pageStatus).toContainText("Page 1 of");
  await expect.poll(() => firstEntry.textContent()).toBe(first);
  await page.getByLabel("Category", { exact: true }).selectOption("request");
  await page.getByLabel("Title or request", { exact: true }).fill("Harbour");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(firstEntry.locator("h2")).toHaveText("Harbour Lights");
  assert(!new URL(page.url()).searchParams.has("page"));
  await page.getByRole("button", { name: "Next", exact: true }).click();
  const parameters = new URL(page.url()).searchParams;
  assert.equal(parameters.get("category"), "request");
  assert.equal(parameters.get("q"), "Harbour");
  await page.reload();
  await page.locator(".sp-log-entry").first().waitFor();
  assert.equal(
    await page.getByLabel("Title or request", { exact: true }).inputValue(),
    "Harbour",
  );
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  assert(!new URL(page.url()).searchParams.has("snapshot"));
  await page.getByText("Time range", { exact: true }).click();
  const year = new Date().getFullYear();
  await page
    .getByLabel("From", { exact: true })
    .fill(`${year - 1}-01-01T00:00`);
  await page
    .getByLabel("Until", { exact: true })
    .fill(`${year + 1}-01-01T00:00`);
  await page.locator(".sp-log-entry").first().waitFor();
  const from = new URL(page.url()).searchParams.get("since");
  const until = new URL(page.url()).searchParams.get("until");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  assert.equal(new URL(page.url()).searchParams.get("since"), from);
  assert.equal(new URL(page.url()).searchParams.get("until"), until);
  await page.locator(".sp-log-entry").first().waitFor();
  await audit("logs-time-range");
  await page
    .getByLabel("Title or request", { exact: true })
    .fill("No such film in this fixture");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page
    .getByRole("heading", { name: "No events match these filters." })
    .waitFor();
  await audit("logs-empty");
  await page.route("**/api/v1/logs*", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Log history is temporarily unavailable. Try again.",
      }),
    }),
  );
  await go("/settings/logs");
  await page.getByRole("alert").waitFor();
  assert.equal(
    await page.getByRole("heading", { name: /No .*events/ }).count(),
    0,
  );
  await audit("logs-error");
  await page.unroute("**/api/v1/logs*");
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await page.locator(".sp-log-entry").first().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await go("/settings/logs");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page
    .getByRole("heading", { name: "Less scrolling. More good stuff." })
    .waitFor();
  assert.equal(new URL(page.url()).pathname, "/");
  assert.equal(await page.getByLabel("Username", { exact: true }).count(), 0);
  assert.deepEqual(errors, []);
  fs.writeFileSync(
    `${out}/results.json`,
    JSON.stringify({ results, errors }, null, 2),
  );
  await browser.close();
  console.log(JSON.stringify({ auditedLayouts: results.length, errors }));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
