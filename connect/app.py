"""Sparrow Connect: the shared front door and phone book for Sparrow servers.

Run by the Sparrow project for every installation that opts in. It gives each
home server a stable HTTPS address and sends a person to their own server. No
accounts, libraries, watch history or video pass through it.
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Sparrow Connect", docs_url=None, redoc_url=None, openapi_url=None)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sparrow</title>
<meta name="description" content="Your own film and TV library, on any screen.">
<style>
  :root { color-scheme: dark; --ink: #f4efe6; --muted: #a8a196; --accent: #f2b544; --bg: #11100e; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100dvh; display: grid; place-items: center; background: var(--bg);
         color: var(--ink); font: 17px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 24px 16px; }
  main { max-width: 34rem; }
  h1 { margin: 0 0 0.25em; font-size: clamp(2.5rem, 9vw, 4rem); letter-spacing: -0.03em; line-height: 1; }
  h1 span { color: var(--accent); }
  p { margin: 0.75em 0 0; color: var(--muted); }
  p.lead { color: var(--ink); font-size: 1.25rem; }
</style>
</head>
<body>
<main>
  <h1>sparrow<span>.</span></h1>
  <p class="lead">Your own film and TV library, on any screen.</p>
  <p>To open your library here, turn on Sparrow Connect in your server's Settings.</p>
</main>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE


@app.get("/health")
def health():
    return {"ok": True}
