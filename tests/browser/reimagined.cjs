/* The redesigned navigation, filters, artwork fallbacks and supporting dialogs. */
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
const path = require("node:path");
const assert = require("node:assert/strict");
const base = process.env.SPARROW_BROWSER_URL || "http://127.0.0.1:8891";
const out = process.env.SPARROW_VISUAL_OUT || "docs/reimagined-validation";
const { expect } = require(require.resolve("@playwright/test", { paths: [process.cwd() + "/frontend"] }));
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
  const page = await context.newPage(),
    errors = [],
    results = [];
  page.on("pageerror", (error) => errors.push(error.message));
  async function snapshot(name, audit = false, widths = [390, 1440]) {
    for (const width of widths) {
      await page.setViewportSize({ width, height: width < 700 ? 844 : 1000 });
      // Media queries apply on the next frame after an emulated resize.
      await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
      await page.waitForTimeout(150);
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() =>
        Promise.all(
          [...document.images].map((image) => image.decode().catch(() => {})),
        ),
      );
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth,
        // Broken: loaded with nothing to show, or still pending on screen.
        // Lazy images further down aren't requested yet (WebKit defers more).
        brokenImages: [...document.images].filter((image) => {
          const box = image.getBoundingClientRect();
          const onScreen = box.bottom > 0 && box.top < innerHeight;
          return image.complete ? !image.naturalWidth : onScreen;
        }).length,
        clipped: [
          ...document.querySelectorAll("input, select, textarea, button"),
        ]
          .filter((element) => {
            const rect = element.getBoundingClientRect();
            return (
              rect.width &&
              rect.height &&
              (rect.left < -1 || rect.right > innerWidth + 1)
            );
          })
          .map(
            (element) =>
              element.getAttribute("aria-label") || element.textContent.trim(),
          ),
      }));
      const violations = audit
        ? (
            await new AxeBuilder({ page })
              .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
              .analyze()
          ).violations.map((v) => ({
            id: v.id,
            targets: v.nodes.map((n) => n.target),
          }))
        : [];
      results.push({ name, width, ...layout, violations });
      assert(
        !layout.overflow &&
          !layout.brokenImages &&
          !layout.clipped.length &&
          !violations.length,
        JSON.stringify(results.at(-1)),
      );
      if (width === 390 || width === 1440)
        await page.screenshot({
          path: path.join(
            out,
            `tour-${name}-${width < 700 ? "mobile" : "desktop"}.png`,
          ),
          fullPage: width >= 700 && !(await page.getByRole("dialog").count()),
        });
    }
  }
  async function go(route) {
    await page.goto(base + route);
    await page.locator("h1").waitFor();
    await page.waitForTimeout(180);
  }
  async function closeDialog() {
    await page
      .getByRole("button", { name: "Close", exact: true })
      .last()
      .click();
  }
  await go("/");
  assert.equal(
    await page.getByLabel("Username", { exact: true }).count(),
    0,
    "The public home must introduce Sparrow before asking for credentials.",
  );
  await snapshot("landing", true, [360, 390, 768, 1440]);
  await page.getByRole("link", { name: "Sign in", exact: true }).click();
  await page.getByLabel("Username", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Back", exact: true }).click();
  await page
    .getByRole("heading", { name: "Say what you want to watch." })
    .waitFor();
  await page.reload();
  assert.equal(await page.getByLabel("Username", { exact: true }).count(), 0);
  await page.getByRole("link", { name: "Sign in", exact: true }).click();
  await page.getByLabel("Username", { exact: true }).waitFor();
  await snapshot("sign-in", true);
  await page.getByLabel("Username", { exact: true }).fill("owner");
  await page
    .getByLabel("Password", { exact: true })
    .fill("fixture-password-123");
  const [login] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/auth/login") &&
        response.request().method() === "POST",
    ),
    page.getByRole("button", { name: "Sign in", exact: true }).click(),
  ]);
  assert.equal(
    login.status(),
    200,
    "Run the existing viewing check first to create the isolated fixture account.",
  );
  for (const [route, name] of [
    ["/", "home"],
    ["/library", "library"],
    ["/discover", "discover"],
    ["/settings/following", "following"],
    ["/title/movie/102", "movie"],
    ["/title/tv/101", "series"],
    ["/settings", "preferences"],
    ["/settings/computers", "computers"],
    ["/settings/people", "people"],
    ["/settings/defaults", "defaults"],
    ["/settings/server", "server"],
  ]) {
    await go(route);
    await snapshot(name, true);
  }

  await go("/library");
  // A slow TV computer must show a choice at once and never drop typed letters.
  // Chromium only: other engines (WebKit for Safari) run the same steps unthrottled.
  const cpu = await page.context().newCDPSession(page).catch(() => null);
  await cpu?.send("Emulation.setCPUThrottlingRate", { rate: 6 });
  const types = page.getByRole("group", { name: "Media type", exact: true });
  await types.getByLabel("Films", { exact: true }).check();
  await page.getByLabel("Sort titles", { exact: true }).selectOption("year");
  await page
    .getByLabel("Search the collection", { exact: true })
    .pressSequentially("quiet", { delay: 20 });
  assert.equal(new URL(page.url()).searchParams.get("sort"), "year");
  assert.equal(new URL(page.url()).searchParams.get("type"), "movie");
  await cpu?.send("Emulation.setCPUThrottlingRate", { rate: 1 });
  assert.equal(
    await page.getByLabel("Search the collection").inputValue(),
    "quiet",
  );
  await expect(page.locator(".entry")).toHaveCount(1);
  await page
    .getByRole("link", { name: "Open The Quiet Planet", exact: true })
    .click();
  await page.getByRole("link", { name: "Back to Guide", exact: true }).click();
  assert.equal(
    await page.getByLabel("Search the collection").inputValue(),
    "quiet",
  );
  await expect(types.getByLabel("Films", { exact: true })).toBeChecked();
  assert.equal(await page.getByLabel("Sort titles").inputValue(), "year");
  await page.reload();
  assert.equal(
    await page.getByLabel("Search the collection").inputValue(),
    "quiet",
  );
  await snapshot("library-filtered", true, [360, 390, 768, 1440]);
  await page.getByLabel("Search the collection").fill("not in this collection");
  await page.getByRole("heading", { name: "Nothing matches." }).waitFor();
  await page
    .getByRole("button", { name: "Clear filters", exact: true })
    .click();
  await expect(page.locator(".entry")).toHaveCount(6);
  const key = (title) => title.replace(/^(the|a|an)\s+/i, "");
  const names = await page.locator(".entry-title").allTextContents();
  await expect(page.locator(".entry-title")).toHaveText(
    [...names].sort((a, b) => key(a).localeCompare(key(b))),
  );
  // Remote-style arrow keys move between listed titles.
  await page.locator(".entry").first().focus();
  await page.keyboard.press("ArrowDown");
  assert.equal(
    await page.evaluate(() => document.activeElement.getAttribute("aria-label")),
    `Open ${names[1]}`,
  );
  await page.getByRole("radio", { name: "Covers", exact: true }).check();
  await expect(page.locator(".cover-card")).toHaveCount(6);
  await snapshot("library-covers", true, [390, 1440]);

  await page.keyboard.press("Control+k");
  const finder = page.getByRole("searchbox", {
    name: "Find a film or series",
    exact: true,
  });
  await finder.waitFor();
  await finder.fill("A funny film under two hours");
  await page.reload();
  assert.equal(await finder.inputValue(), "A funny film under two hours");
  // Find is a title search only: no Ask Sparrow, and a description is never
  // handed to the agent while typing.
  await expect(page.getByRole("button", { name: "Ask Sparrow" })).toHaveCount(0);
  await snapshot("search-draft", true);
  await finder.fill("quiet");
  await page.locator(".results .entry").first().waitFor();
  await page.getByRole("heading", { name: "Titles", exact: true }).waitFor();
  await snapshot("search-results");
  await page.locator(".results .entry").first().click();
  await page.getByRole("link", { name: "Back to Find", exact: true }).click();
  assert.equal(await finder.inputValue(), "quiet");
  await finder.fill("");
  await expect(page.getByRole("status")).toHaveCount(0);

  await go("/title/tv/101");
  // The whole season shows; the one episode here is playable.
  await expect(page.locator(".ep")).toHaveCount(3);
  await expect(page.locator(".ep.ready")).toHaveCount(1);
  await page
    .getByRole("button", { name: "Request episodes", exact: true })
    .click();
  await page.getByRole("dialog").waitFor();
  // Episode 1 is here already, so it comes ticked and can't be asked for again.
  assert.equal(await page.getByRole("checkbox").first().isDisabled(), true);
  await page.getByRole("checkbox", { disabled: false }).first().check();
  await snapshot("episode-request", true);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  assert.equal(
    await page
      .getByRole("button", { name: "Request episodes", exact: true })
      .evaluate((element) => document.activeElement === element),
    true,
  );

  await go("/settings/computers");
  await page.getByRole("button", { name: "Add a computer", exact: true }).click();
  await snapshot("storage-pair", true);
  await closeDialog();
  await page
    .getByRole("button", { name: /Change where this server keeps titles/ })
    .click();
  await page.locator("dialog[open] .picker-list li.selected").waitFor();
  await snapshot("storage-folders", true);
  await closeDialog();
  // The fixture's own sample video isn't a title Sparrow knows: Found asks about it.
  await page
    .locator("#device-local")
    .getByRole("button", { name: "Review", exact: true })
    .click({ timeout: 30000 });
  await page
    .getByRole("dialog", { name: "Found on this server", exact: true })
    .waitFor();
  await page.getByText("It's under a minute long.", { exact: false }).waitFor();
  await snapshot("found-review", true);
  await page
    .getByRole("button", { name: "Which title is this?", exact: true })
    .first()
    .click();
  await page.getByLabel("Film or series", { exact: true }).fill("quiet");
  await page
    .getByRole("button", { name: "Search", exact: true })
    .click();
  await page.getByRole("button", { name: /The Quiet Planet/ }).waitFor();
  await snapshot("found-match", true);
  await page.getByRole("button", { name: /The Quiet Planet/ }).click();
  await page
    .getByRole("button", { name: "Add 1 title to the collection", exact: true })
    .waitFor();
  await page.keyboard.press("Escape");

  await go("/settings/people");
  await page
    .getByRole("button", { name: "Invite someone", exact: true })
    .click();
  await snapshot("invite-person", true);
  await closeDialog();
  if (
    await page.getByRole("button", { name: "Edit access", exact: true }).count()
  ) {
    await page
      .getByRole("button", { name: "Edit access", exact: true })
      .first()
      .click();
    await snapshot("person-access", true);
    await closeDialog();
  }
  await go("/settings/security");
  await page.getByRole("button", { name: "Change password", exact: true }).click();
  await page
    .getByLabel("Current password", { exact: true })
    .scrollIntoViewIfNeeded();
  await snapshot("password-change", true, [390]);

  await page.route("**/api/v1/catalogue", (route) =>
    route.fulfill({ json: [] }),
  );
  await go("/");
  await snapshot("first-collection", true);
  await page.unroute("**/api/v1/catalogue");
  await page.route("**/art/**", (route) => route.abort());
  await go("/library");
  await page.waitForTimeout(300);
  await snapshot("artwork-fallback", true);
  await page.unroute("**/art/**");

  const result = {
    passed: true,
    errors,
    new_behaviors: [
      "public landing, optional sign-in and back navigation",
      "persistent library filters and sorting",
      "return to filtered collection",
      "return to discovery",
      "keyboard search shortcut and one search box",
      "arrow-key movement between listed titles",
      "list and covers views",
      "persistent search draft",
      "cleared search recovery",
      "season filtering",
      "dialog escape and focus restoration",
      "storage/import/matching/access forms",
      "missing artwork fallback",
    ],
    results,
  };
  fs.writeFileSync(
    path.join(out, "reimagined-results.json"),
    JSON.stringify(result, null, 2),
  );
  await browser.close();
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      passed: true,
      layouts: results.length,
      behaviors: result.new_behaviors.length,
    }),
  );
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
