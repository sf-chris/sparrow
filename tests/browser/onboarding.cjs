/* Full owner setup against disposable state; no live provider calls. */
const { chromium } = require(
  require.resolve("playwright", { paths: [process.cwd() + "/frontend"] }),
);
const AxeBuilder = require(
  require.resolve("@axe-core/playwright", {
    paths: [process.cwd() + "/frontend"],
  }),
).default;
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const base = process.env.SPARROW_BROWSER_URL || "http://127.0.0.1:8891";
const out =
  process.env.SPARROW_VISUAL_OUT || "tests/browser/artifacts/onboarding";
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.SPARROW_CHROME || "/usr/bin/google-chrome",
    headless: true,
    args: ["--no-sandbox"],
  });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  const errors = [],
    layouts = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const headers = { "X-Sparrow-Request": "1" };
  async function state() {
    return (
      await context.request.get(base + "/api/v1/admin/onboarding")
    ).json();
  }
  async function audit(name) {
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.evaluate(() => document.fonts.ready);
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
      layouts.push({ name, width, overflow, violations });
      assert(!overflow && !violations.length, JSON.stringify(layouts.at(-1)));
      if ([390, 1440].includes(width))
        await page.screenshot({
          path: path.join(out, name + "-" + width + ".png"),
          fullPage: true,
        });
    }
  }
  try {
    const code = fs
      .readFileSync(
        path.join(process.env.SPARROW_BROWSER_STATE, "owner-setup-code"),
        "utf8",
      )
      .trim();
    await page.goto(base + "/#setup_code=" + encodeURIComponent(code));
    await page.getByLabel("Setup code", { exact: true }).waitFor();
    await page.waitForFunction(() => !location.hash);
    assert.equal(
      await page.getByLabel("Setup code", { exact: true }).inputValue(),
      code,
    );
    await page.getByLabel("Your name", { exact: true }).fill("Chris");
    await page.getByLabel("Username", { exact: true }).fill("owner");
    await page
      .getByLabel("Password", { exact: true })
      .fill("fixture-password-123");
    await page
      .getByRole("button", {
        name: "Create administrator account",
        exact: true,
      })
      .click();
    await page
      .getByRole("button", { name: "Continue setup", exact: true })
      .waitFor();
    await page
      .getByLabel("Preferred audio", { exact: true })
      .selectOption("en");
    await page
      .getByRole("button", { name: "Continue setup", exact: true })
      .click();
    await page
      .getByRole("heading", {
        name: "How would you like to start?",
        exact: true,
      })
      .waitFor();
    assert.equal(new URL(page.url()).pathname, "/setup");
    await audit("setup-choice");
    await page
      .getByRole("button", { name: "Find and download for me", exact: true })
      .click();
    await page.getByLabel("Anthropic API key", { exact: true }).waitFor();
    await page
      .getByRole("button", { name: "Save and continue", exact: true })
      .click();
    await page
      .getByRole("alert")
      .filter({ hasText: "Add both API keys" })
      .waitFor();
    await audit("setup-provider-error");
    await page
      .getByLabel("Anthropic API key", { exact: true })
      .fill("fixture-only-no-provider-calls");
    await page
      .getByRole("button", { name: "Save and continue", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Storage & import", exact: true })
      .waitFor();
    await page.reload();
    await page
      .getByRole("heading", { name: "Storage & import", exact: true })
      .waitFor();
    assert.equal((await state()).step, "storage");
    await page
      .getByRole("button", { name: "Choose folders", exact: true })
      .click();
    await page
      .getByLabel("Library folder", { exact: true })
      .fill(path.join(process.env.SPARROW_BROWSER_STATE, "library"));
    await page
      .getByLabel("Staging folder", { exact: true })
      .fill(path.join(process.env.SPARROW_BROWSER_STATE, "library", "nested"));
    await page
      .getByRole("button", { name: "Save folders", exact: true })
      .click();
    await page.getByRole("dialog").getByRole("alert").waitFor();
    await audit("setup-folder-error");
    await page
      .getByLabel("Staging folder", { exact: true })
      .fill(path.join(process.env.SPARROW_BROWSER_STATE, "incoming"));
    await page
      .getByRole("button", { name: "Save folders", exact: true })
      .click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page.getByLabel("Download app", { exact: true }).waitFor();
    await audit("setup-downloads");
    await page
      .getByRole("button", { name: "Save and continue", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Check your setup", exact: true })
      .waitFor();
    assert(
      await page
        .getByRole("button", {
          name: "Save setup and find a title",
          exact: true,
        })
        .isDisabled(),
    );
    assert.equal(
      (
        await context.request.post(base + "/api/v1/admin/onboarding/finish", {
          headers,
        })
      ).status(),
      422,
    );
    await audit("setup-incomplete");
    await page
      .getByRole("button", { name: "Finish later", exact: true })
      .click();
    await page
      .getByRole("heading", { name: /^(Good (morning|afternoon|evening), Chris\.|Still up, Chris\?)$/ })
      .waitFor();
    assert.equal((await state()).complete, false);
    await page.reload();
    await page
      .getByRole("link", { name: "Continue setup", exact: true })
      .waitFor();
    await page.goto(base + "/settings");
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page.getByRole("link", { name: "Sign in", exact: true }).click();
    await page.getByLabel("Username", { exact: true }).fill("owner");
    await page
      .getByLabel("Password", { exact: true })
      .fill("fixture-password-123");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page
      .getByRole("link", { name: "Continue setup", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Check your setup", exact: true })
      .waitFor();
    await page
      .getByRole("button", { name: "1. How to start", exact: true })
      .click();
    await page
      .getByRole("button", {
        name: "Watch my existing collection",
        exact: true,
      })
      .click();
    // Import mode remains usable with neither provider nor a download client.
    assert.equal(
      (
        await context.request.patch(base + "/api/v1/admin/config", {
          headers,
          data: { clear_tmdb_api_key: true, clear_anthropic_api_key: true },
        })
      ).status(),
      200,
    );
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page
      .getByRole("button", { name: "Save setup and import media", exact: true })
      .waitFor();
    await audit("setup-import-ready");
    await page
      .getByRole("button", { name: "Save setup and import media", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Storage & import", exact: true })
      .waitFor();
    assert.equal(new URL(page.url()).pathname, "/settings/storage");
    assert.equal((await state()).complete, true);
    // Restore only the catalogue fixture setting for the remaining browser journeys.
    await context.request.patch(base + "/api/v1/admin/config", {
      headers,
      data: { tmdb_api_key: "fixture-only" },
    });
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(out, "results.json"),
      JSON.stringify(
        {
          passed: true,
          layouts,
          errors,
          resume: true,
          deferred_is_incomplete: true,
          import_without_providers: true,
        },
        null,
        2,
      ),
    );
    console.log(
      JSON.stringify({
        passed: true,
        layouts: layouts.length,
        resume: true,
        import_without_providers: true,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
