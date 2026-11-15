"""Minimal HTML login form (no external assets)."""
from __future__ import annotations

from html import escape

_TEMPLATE = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вхід — моніторинг PostgreSQL</title>
<style>
  :root {{ color-scheme: light dark; --bg:#f4f5f7; --card:#fff; --fg:#1d2330; --muted:#6b7280;
          --accent:#2f6fec; --err-bg:#fdecec; --err:#b42318; --border:#d7dbe2; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#111318; --card:#1b1f27; --fg:#e6e8ec; --muted:#9aa3b2; --accent:#5b8cff;
            --err-bg:#3a1d1d; --err:#ff8a80; --border:#2c323d; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:16px;
         background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }}
  main {{ width:100%; max-width:360px; background:var(--card); border:1px solid var(--border);
         border-radius:10px; padding:28px 24px; }}
  h1 {{ font-size:19px; margin:0 0 4px; }}
  p.sub {{ margin:0 0 20px; color:var(--muted); font-size:13px; }}
  label {{ display:block; font-size:13px; margin:12px 0 4px; }}
  input {{ width:100%; padding:9px 10px; border:1px solid var(--border); border-radius:6px;
          background:transparent; color:inherit; font:inherit; }}
  input:focus {{ outline:2px solid var(--accent); outline-offset:1px; }}
  button {{ width:100%; margin-top:20px; padding:10px; border:0; border-radius:6px;
           background:var(--accent); color:#fff; font:600 15px system-ui, sans-serif; cursor:pointer; }}
  .error {{ background:var(--err-bg); color:var(--err); padding:8px 10px; border-radius:6px;
           font-size:13px; margin-bottom:8px; }}
</style>
</head>
<body>
<main>
  <h1>Моніторинг PostgreSQL</h1>
  <p class="sub">Сервіс обробки замовлень інтернет-магазину</p>
  {error}
  <form method="post" action="/login">
    <input type="hidden" name="next" value="{next}">
    <label for="username">Користувач</label>
    <input id="username" name="username" autocomplete="username" required autofocus value="{username}">
    <label for="password">Пароль</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Увійти</button>
  </form>
</main>
</body>
</html>
"""


def render_login(next_url: str, error: str | None = None, username: str = "") -> str:
    error_html = f'<div class="error" role="alert">{escape(error)}</div>' if error else ""
    return _TEMPLATE.format(next=escape(next_url, quote=True), error=error_html,
                            username=escape(username, quote=True))
