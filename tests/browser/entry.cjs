/* Public entry, setup handoff, sign-out, and protected-link authentication. */
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
const out = process.env.SPARROW_VISUAL_OUT || "docs/product-validation";
(async () => {
  fs.mkdirSync(out, { recursive: true });
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
    layouts = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const setup = (
    await (await context.request.get(base + "/api/v1/auth/status")).json()
  ).needs_setup;
  const welcome = () =>
    page.getByRole("heading", { name: "Less scrolling. More good stuff." });
  async function audit(name) {
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
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
          path: path.join(
            out,
            `${name}-${width === 390 ? "mobile" : "desktop"}.png`,
          ),
          fullPage: true,
        });
    }
  }
  await page.goto(base);
  await welcome().waitFor();
  assert.equal(await page.locator("form").count(), 0);
  if (setup) {
    await page
      .getByRole("link", { name: "Set up Sparrow", exact: true })
      .click();
    await page.getByLabel("Setup code", { exact: true }).waitFor();
    await audit("administrator-setup");
    await page
      .getByRole("link", { name: "Back to the good stuff", exact: true })
      .click();
    await welcome().waitFor();
    const code = fs
      .readFileSync(
        path.join(
          process.env.SPARROW_BROWSER_STATE || "/tmp/sparrow-browser-check",
          "owner-setup-code",
        ),
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
    assert(
      (await (await context.request.get(base + "/api/v1/auth/status")).json())
        .needs_setup,
      "A setup link must not create an account by itself.",
    );
    await page.getByLabel("Your name", { exact: true }).fill("Chris");
    const password = page.getByLabel("Password", { exact: true });
    await password.fill("seven77");
    assert.equal(await password.evaluate((el) => el.checkValidity()), false);
    await password.fill("eight888");
    assert.equal(await password.evaluate((el) => el.checkValidity()), true);
    await page
      .getByRole("button", { name: "Show password", exact: true })
      .click();
    assert.equal(await password.getAttribute("type"), "text");
    await page
      .getByRole("button", { name: "Hide password", exact: true })
      .click();
  } else {
    await page.getByRole("link", { name: "Sign in", exact: true }).click();
  }
  await page.getByLabel("Username", { exact: true }).fill("owner");
  await page
    .getByLabel("Password", { exact: true })
    .fill("fixture-password-123");
  await page
    .getByRole("button", {
      name: setup ? "Create administrator account" : "Sign in",
      exact: true,
    })
    .click();
  if (setup) {
    await page
      .getByRole("button", { name: "Continue to Sparrow", exact: true })
      .waitFor();
    await audit("first-preferences");
    await page
      .getByRole("button", { name: "Continue to Sparrow", exact: true })
      .click();
  }
  await page
    .getByRole("heading", { name: "What’s on tonight, Chris?", exact: true })
    .waitFor();
  await page.goto(base + "/settings");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await welcome().waitFor();
  assert.equal(new URL(page.url()).pathname, "/");
  await page.reload();
  await welcome().waitFor();
  assert.equal(
    (await (await context.request.get(base + "/api/v1/auth/status")).json())
      .user,
    null,
  );
  await page.goto(base + "/title/movie/102");
  await page.getByLabel("Username", { exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("heading", { name: "The Quiet Planet", exact: true })
      .count(),
    0,
    "Private title details must stay behind sign-in.",
  );
  await page.getByLabel("Username", { exact: true }).fill("owner");
  await page
    .getByLabel("Password", { exact: true })
    .fill("incorrect-fixture-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("alert").waitFor();
  assert.equal(
    await page.getByLabel("Username", { exact: true }).inputValue(),
    "owner",
  );
  await audit("sign-in-error");
  await page
    .getByLabel("Password", { exact: true })
    .fill("fixture-password-123");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page
    .getByRole("heading", { name: "The Quiet Planet", exact: true })
    .waitFor();
  assert.equal(new URL(page.url()).pathname, "/title/movie/102");
  assert.deepEqual(errors, []);
  fs.writeFileSync(
    path.join(out, "entry-results.json"),
    JSON.stringify(
      {
        passed: true,
        setup: !!setup,
        signout: true,
        protected_link: true,
        retained_input: true,
        errors,
        layouts,
      },
      null,
      2,
    ),
  );
  await browser.close();
  console.log(
    JSON.stringify({
      passed: true,
      setup: !!setup,
      layouts: layouts.length,
      signout: true,
      protected_link: true,
    }),
  );
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
