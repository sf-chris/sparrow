// Run the browser journeys in WebKit, Safari's engine, without changing them:
//   SPARROW_PLAYWRIGHT=$PWD/tests/browser/webkit.cjs tests/browser/ci.sh
// Chrome's executable path and flags don't apply, so launch options are dropped.
const playwright = require(
  require.resolve("playwright", { paths: [process.cwd() + "/frontend"] }),
);

// WebKit reports requests cancelled by a page navigation as page errors
// ("… due to access control checks"); the app catches them. Other errors pass.
function quiet(page) {
  const on = page.on.bind(page);
  page.on = (event, handler) =>
    on(
      event,
      event === "pageerror"
        ? (error) =>
            /due to access control checks\.$/.test(error.message) ||
            handler(error)
        : handler,
    );
  return page;
}

const webkit = {
  name: () => "webkit",
  async launch(options = {}) {
    const browser = await playwright.webkit.launch({
      headless: options.headless !== false,
    });
    // WebKit can't route requests a service worker makes (Chromium can), so
    // pages run without one unless a journey asks for it.
    const newContext = browser.newContext.bind(browser);
    browser.newContext = async (options = {}) => {
      const context = await newContext({ serviceWorkers: "block", ...options });
      const newPage = context.newPage.bind(context);
      context.newPage = async () => quiet(await newPage());
      return context;
    };
    const newPage = browser.newPage.bind(browser);
    browser.newPage = async (options = {}) =>
      quiet(await newPage({ serviceWorkers: "block", ...options }));
    return browser;
  },
};
module.exports = { ...playwright, chromium: webkit };
