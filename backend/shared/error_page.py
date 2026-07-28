# A minimal, self-contained 404 page for browser navigations to unknown paths. Only requests that
# Accept text/html get this - API clients (the UI's fetch, the CLI, agents) still get JSON, so their
# error handling is unaffected. Dependency-free so both the FastAPI app and the Lambda catch-all
# handler can reuse it.
NOT_FOUND_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not found · reach</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0f172a; color:#e2e8f0;
         font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  .card { text-align:center; padding:2.5rem 2rem; }
  .code { font-size:3.5rem; font-weight:700; color:#818cf8; margin:0; line-height:1; }
  h1 { font-size:1.25rem; font-weight:600; margin:.75rem 0 .25rem; }
  p { color:#94a3b8; margin:.25rem 0 1.5rem; }
  a { display:inline-block; background:#4f46e5; color:#fff; text-decoration:none; font-weight:600;
      font-size:.9rem; padding:.6rem 1.2rem; border-radius:.5rem; transition:background .15s; }
  a:hover { background:#4338ca; }
</style>
</head>
<body>
  <div class="card">
    <p class="code">404</p>
    <h1>Page not found</h1>
    <p>This page doesn&#39;t exist.</p>
    <a href="/ui/">Go to the console</a>
  </div>
</body>
</html>"""
