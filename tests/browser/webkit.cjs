// Run the browser journeys in WebKit, Safari's engine, without changing them:
//   SPARROW_PLAYWRIGHT=$PWD/tests/browser/webkit.cjs tests/browser/ci.sh
// Chrome's executable path and flags don't apply, so launch options are dropped.
const playwright = require(
  require.resolve("playwright", { paths: [process.cwd() + "/frontend"] }),
);
const webkit = {
  name: () => "webkit",
  launch: (options = {}) =>
    playwright.webkit.launch({ headless: options.headless !== false }),
};
module.exports = { ...playwright, chromium: webkit };
