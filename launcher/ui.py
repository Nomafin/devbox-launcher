"""Presentation for the launcher: CSS, markup, and the client script.

Kept apart from app.py so the routes stay readable, and kept *inline* rather
than served as static files: the service is a single uvicorn app with no static
mount, and a phone on the tailnet should not need a second round trip (or a
packaging change) to style a page this small.

The server owns the row markup. /api/* hands the client the same rendered HTML
it would have received from a full page load, so there is no second copy of the
template in JavaScript to drift out of step.
"""

from html import escape

# Signal colors carry state and nothing else — no button, link, or border
# borrows them — so a green row always means "a session is live", never
# "here is a button".
_CSS = """
:root {
  color-scheme: dark;
  --body: #0f171d;
  --surface: #17222b;
  --edge: #24333f;
  --ink: #e4edf4;
  --ink-dim: #8ba0af;
  --steel: #2b4557;
  --steel-ink: #d7e6f0;
  --live: #3ddc97;
  --wait: #d9a13b;
  --dead: #ff6f61;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 1.25rem max(1rem, env(safe-area-inset-left))
           calc(2rem + env(safe-area-inset-bottom)) max(1rem, env(safe-area-inset-right));
  background: var(--body);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 1rem;
  line-height: 1.45;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 33rem; margin: 0 auto; }
header { display: flex; align-items: baseline; justify-content: space-between;
         gap: 1rem; margin-bottom: 1.25rem; }
h1 { font-size: 1.375rem; font-weight: 600; letter-spacing: -0.01em; margin: 0; }
.count { color: var(--ink-dim); font-size: .8125rem; font-variant-numeric: tabular-nums; }
/* Its own line: on a phone this and the count together overflow the header. */
.usage { margin: -.75rem 0 1.25rem; color: var(--ink-dim); font-size: .75rem;
         font-variant-numeric: tabular-nums; }
.usage:empty { display: none; }

/* The rail is the state indicator: a 3px edge reads from arm's length, and it
   sits outside the text so a long path can never push it out of view. */
.row {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--edge);
  border-left: 3px solid var(--edge);
  border-radius: .5rem;
  padding: .875rem 1rem;
  margin-bottom: .625rem;
}
.row[data-state="connected"] { border-left-color: var(--live); }
.row[data-state="ready"], .row[data-state="starting"] { border-left-color: var(--wait); }
.row[data-state="failed"] { border-left-color: var(--dead); }

.head { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
.name { font-size: 1.0625rem; font-weight: 600; min-width: 0; overflow-wrap: anywhere; }
.meta { margin-top: .25rem; font-size: .8125rem; display: flex; flex-wrap: wrap;
        gap: .5rem .75rem; align-items: baseline; }
.state { font-weight: 600; }
.row[data-state="connected"] .state { color: var(--live); }
.row[data-state="ready"] .state, .row[data-state="starting"] .state { color: var(--wait); }
.row[data-state="failed"] .state { color: var(--dead); }
/* One line, always: a project parked somewhere with a very long path must not
   be able to push the row to four lines and bury the rest of the list. */
.id { font-family: var(--mono); font-size: .75rem; color: var(--ink-dim);
      min-width: 0; max-width: 100%; overflow: hidden;
      white-space: nowrap; text-overflow: ellipsis; }
.why { margin-top: .375rem; font-size: .8125rem; color: var(--dead);
       overflow-wrap: anywhere; }
.age { color: var(--ink-dim); font-size: .8125rem; font-variant-numeric: tabular-nums; }
.row[data-state="stuck"] { border-left-color: var(--dead); }
.row[data-state="stuck"] .state { color: var(--dead); }

/* Amber, not coral: a repo that needs its remote renamed is not a failure, it
   is something to fix before you tap Start and wait seven seconds for nothing. */
.warn { margin-top: .625rem; padding: .625rem .75rem; border-radius: .375rem;
        background: #241e12; border: 1px solid #5c4a24; font-size: .8125rem;
        color: #e8d3a8; }
.warn form { margin: .5rem 0 0; }
.btn.ghost { width: 100%; min-width: 0; background: transparent; color: #e8d3a8;
             border-color: #5c4a24; font-size: .875rem; }

/* Ghosted and full-width: recovery is a deliberate second choice, not a peer
   of the Stop button it sits beneath. */
form.fix, form.fresh { margin: .625rem 0 0; }
.note { margin-top: .375rem; font-size: .75rem; color: var(--ink-dim); }
/* Not `.link` — that name is taken by the login page's authorize URL, which is
   monospace, and .btn.link would inherit its family. */
.btn.quiet { width: 100%; background: transparent; border-color: var(--edge);
             color: var(--ink-dim); font-weight: 500; font-size: .875rem; }

.out { margin-top: .625rem; }
.out summary { font-size: .8125rem; color: var(--ink-dim); cursor: pointer;
               padding: .25rem 0; list-style: none; }
.out summary::-webkit-details-marker { display: none; }
.out summary::before { content: "▸ "; }
.out[open] summary::before { content: "▾ "; }
.tail { margin: .375rem 0 0; padding: .625rem .75rem; border-radius: .375rem;
        background: #0b1319; border: 1px solid var(--edge);
        font-family: var(--mono); font-size: .6875rem; line-height: 1.4;
        color: var(--ink-dim); white-space: pre; overflow-x: auto; }

/* A live listener's whole point is the link, so it gets a row of its own and a
   full-width target rather than competing with the button for space. */
.open {
  display: block; margin-top: .75rem; padding: .75rem .875rem;
  border: 1px solid var(--edge); border-radius: .375rem;
  background: #101b23; color: var(--ink); text-decoration: none;
  font-weight: 600; font-size: .9375rem;
}
.open:active { background: #0c1419; }

form.act { margin: 0; flex: none; }
.btn {
  min-width: 5.5rem; min-height: 2.75rem; padding: .5rem 1rem;
  font: inherit; font-size: .9375rem; font-weight: 600;
  border-radius: .375rem; border: 1px solid var(--steel);
  background: var(--steel); color: var(--steel-ink); cursor: pointer;
}
/* Ghosted, not red: stopping a listener is routine and keeps the
   conversation, and coral is spoken for — it means a state, not a control.
   Four red Stop buttons shouted louder than the one row that had failed. */
.btn.stop { background: transparent; color: var(--ink-dim); }
.btn:active { transform: translateY(1px); }
.btn[disabled] { opacity: .65; cursor: default; }
:focus-visible { outline: 2px solid var(--live); outline-offset: 2px; }

/* The only motion on the page, and it answers a tap: it marks the seconds a
   listener takes to settle, which is otherwise indistinguishable from a
   button that did nothing. */
.row[data-pending] { border-left-color: var(--ink-dim); animation: breathe 1.4s ease-in-out infinite; }
@keyframes breathe { 50% { border-left-color: var(--edge); } }
@media (prefers-reduced-motion: reduce) {
  .row[data-pending] { animation: none; }
  .btn:active { transform: none; }
}

.empty, .hint { color: var(--ink-dim); font-size: .8125rem; }
.empty { background: var(--surface); border: 1px solid var(--edge);
         border-radius: .5rem; padding: 1rem; }
.hint { margin: 1.25rem 0 0; }
.hint code { font-family: var(--mono); font-size: .8125rem; color: var(--ink); }

.banner { background: #25171a; border: 1px solid #6b3430; border-left: 3px solid var(--dead);
          border-radius: .5rem; padding: .875rem 1rem; margin-bottom: 1rem; }
.banner .name { font-size: 1rem; }
.banner p { margin: .25rem 0 .75rem; font-size: .8125rem; color: #e8bdb6; }
.banner .btn { width: 100%; }

.card { background: var(--surface); border: 1px solid var(--edge);
        border-radius: .5rem; padding: 1rem; }
.card p { margin: 0 0 .75rem; }
.card p:last-child { margin-bottom: 0; }
.step { font-size: .9375rem; }
.link { display: block; margin: .5rem 0 1rem; font-family: var(--mono);
        font-size: .8125rem; color: #8fd3ff; overflow-wrap: anywhere; }
.err { color: var(--dead); font-size: .875rem; }
input {
  width: 100%; min-height: 2.75rem; padding: .625rem .75rem; margin-bottom: .625rem;
  font: inherit; font-family: var(--mono); font-size: 1rem;
  color: var(--ink); background: #101b23;
  border: 1px solid var(--edge); border-radius: .375rem;
}
.card .btn { width: 100%; }
.back { display: inline-block; margin-top: 1.25rem; color: var(--ink-dim);
        font-size: .8125rem; text-decoration: none; }
"""

# Progressive enhancement: every control is a real form that works without
# this. The script only trades the full-page POST-redirect-GET for a fetch, so
# a tap gives feedback on the row it happened on instead of a white reload.
#
# RAW string, deliberately: this is JavaScript source, and Python must not
# interpret its escapes. A plain string turned the `'\n'` on the join() below
# into a real newline, which left an unterminated JS string literal — and a
# parse error there kills the WHOLE block, so the page silently stopped
# auto-refreshing and every control fell back to a full page reload.
_JS = r"""
(function () {
  var list = document.getElementById('list');
  if (!list) return;
  var timer = null;

  function rows() { return list.querySelectorAll('.row[data-slug]'); }
  function find(slug) { return list.querySelector('.row[data-slug="' + slug + '"]'); }

  function apply(projects) {
    var seen = {};
    projects.forEach(function (p) {
      seen[p.slug] = true;
      var row = find(p.slug);
      // Never overwrite a row mid-request: its own response is authoritative
      // and is seconds away, and replacing it would drop the pending state.
      if (row && row.hasAttribute('data-pending')) return;
      // Nor while its output is open: a poll every 20s would otherwise shut the
      // pane you are in the middle of reading.
      if (row && row.querySelector('details.out[open]')) return;
      if (row) { row.outerHTML = p.html; } else { list.insertAdjacentHTML('beforeend', p.html); }
    });
    rows().forEach(function (row) {
      if (!seen[row.dataset.slug] && !row.hasAttribute('data-pending')) row.remove();
    });
    reorder(projects);
  }

  // The payload is ordered running-first. Patching rows in place keeps their
  // old positions, so the order is reapplied by moving the existing nodes —
  // appendChild moves rather than copies, which preserves an open disclosure.
  // Never while something is pending: a row must not jump out from under the
  // finger that just tapped it.
  function reorder(projects) {
    if (list.querySelector('.row[data-pending]')) return;
    projects.forEach(function (p) {
      var row = find(p.slug);
      if (row) list.appendChild(row);
    });
  }

  function applyPayload(data) {
    apply(data.projects || []);
    var count = document.getElementById('count');
    if (count) count.textContent = data.count;
    var usage = document.getElementById('usage');
    if (usage) usage.textContent = data.usage || '';
    // The sign-out banner and the empty state change what the page means, not
    // just a row, so hand those to a reload rather than patching them here.
    if (data.signedIn === false || (data.projects || []).length === 0) location.reload();
    schedule();
  }

  function act(form) {
    var row = form.closest('.row');
    var button = form.querySelector('button');
    row.setAttribute('data-pending', '');
    button.disabled = true;
    button.textContent = form.dataset.pendingLabel || 'Starting…';
    var why = row.querySelector('.why');
    if (why) why.remove();
    fetch(form.dataset.api, { method: 'POST', headers: { 'Accept': 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(applyPayload)
      .catch(function () {
        // Say what is known — the request failed — and leave the row's real
        // state to the next poll rather than guessing it here.
        row.removeAttribute('data-pending');
        button.disabled = false;
        if (form.dataset.label) button.textContent = form.dataset.label;
        var note = document.createElement('div');
        note.className = 'why';
        note.textContent = 'The devbox did not answer. Check that it is up, then try again.';
        row.appendChild(note);
      });
  }

  list.addEventListener('submit', function (event) {
    var form = event.target.closest('form.act, form.fix, form.fresh');
    if (!form || !form.dataset.api) return;
    event.preventDefault();
    // Retry and Start fresh both launch a listener and take the same ~7s as
    // Start, so they show the same pending state; the remote rename returns
    // in milliseconds and needs none.
    if (form.classList.contains('fix')) { fix(form); } else { act(form); }
  });

  // The remote rename and the parallel toggle. Same shape as act(), but there
  // is no pending state to show on the row: both return in milliseconds and
  // the reply redraws it.
  function fix(form) {
    var button = form.querySelector('button');
    button.disabled = true;
    fetch(form.dataset.api, { method: 'POST', headers: { 'Accept': 'application/json' },
                              body: new URLSearchParams(new FormData(form)) })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(applyPayload)
      .catch(function () { button.disabled = false; });
  }

  // The pane tail, fetched when the disclosure is opened and not before: it
  // costs a capture per session, which is what the snapshot deliberately avoids
  // paying on every poll. Re-fetched on each open so it is never stale.
  list.addEventListener('toggle', function (event) {
    var details = event.target;
    if (!details.matches || !details.matches('details.out') || !details.open) return;
    var pre = details.querySelector('.tail');
    pre.textContent = 'Loading…';
    fetch('/api/pane/' + encodeURIComponent(details.dataset.slug),
          { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        pre.textContent = (data.lines || []).join('\n') || 'Nothing on the pane yet.';
      })
      .catch(function () { pre.textContent = 'Could not read the pane.'; });
  }, true);  // details' toggle event does not bubble

  function poll() {
    fetch('/api/projects', { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(applyPayload)
      .catch(schedule);
  }

  // Poll briskly only while something is actually in motion, and not at all
  // behind a hidden tab. "ready" is NOT motion: a registered listener waiting
  // for the app rests there indefinitely, and treating it as busy meant one
  // idle project kept the box answering every four seconds forever.
  function schedule() {
    clearTimeout(timer);
    if (document.hidden) return;
    var busy = list.querySelector('.row[data-pending], .row[data-state="starting"]');
    timer = setTimeout(poll, busy ? 4000 : 20000);
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { clearTimeout(timer); } else { poll(); }
  });
  schedule();
})();
"""

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>devbox sessions</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>devbox sessions</h1>
  <span class="count" id="count">{count}</span>
</header>
<p class="usage" id="usage">{usage}</p>
{banner}
<div id="list">{rows}</div>
<p class="hint">A running project appears in the Claude app as
<code>devbox-&lt;name&gt;</code>.</p>
</div>
<script>{js}</script>
</body>
</html>
"""

_ROW = """<div class="row" data-slug="{slug}" data-state="{state}">
  <div class="head">
    <div class="name">{name}</div>
    <form class="act" method="post" action="/{action}/{slug}" data-slug="{slug}"
          data-api="/api/{action}/{slug}" data-label="{label}"
          data-pending-label="{pending}">
      <button class="btn {cls}" type="submit">{label}</button>
    </form>
  </div>
  <div class="meta">{status}{age}{mode}<span class="id">{ident}</span></div>{why}{retry}{fresh}{parallel}{warn}{link}{output}
</div>"""

# Stop-then-Start is what you always did next to a broken listener, so one
# control does both. It sits under the reason rather than in the head, where
# Stop keeps its single unambiguous meaning.
_RETRY = """
  <form class="fix" method="post" action="/retry/{slug}"
        data-api="/api/retry/{slug}" data-slug="{slug}">
    <button class="btn ghost" type="submit">Retry</button>
  </form>"""

# Only rendered where a recorded conversation exists to discard — otherwise
# Start already starts fresh and this would be a control that does nothing.
_FRESH = """
  <form class="fresh" method="post" action="/start-fresh/{slug}"
        data-api="/api/start-fresh/{slug}" data-slug="{slug}">
    <button class="btn quiet" type="submit">Start fresh — new conversation</button>
  </form>"""

# The spawn mode the next Start uses. Worktree mode gives every session the
# app opens its own checkout, so two can edit code at once; it never resumes,
# which the note says because Start otherwise does.
_PARALLEL = """
  <form class="fix" method="post" action="/parallel/{slug}"
        data-api="/api/parallel/{slug}" data-slug="{slug}" data-parallel="{state}">
    <input type="hidden" name="on" value="{next}">
    <button class="btn quiet" type="submit">Parallel sessions: {state}</button>
  </form>{note}"""

_PARALLEL_NOTE = """
  <div class="note">Each session opened from the app gets its own git worktree.
  Starts a new conversation rather than resuming.</div>"""

# A session you forgot nine days ago should not read like one you started a
# minute ago, and the number is only useful next to the state it qualifies.
_AGE = """<span class="age">{age}</span>"""

# The origin gotcha, said where it bites rather than in a runbook you are not
# holding. The button is the launcher's only write into a repo, so it says
# exactly which command it runs.
_WARN = """
  <div class="warn">
    <div>{text}</div>
    <form class="fix" method="post" action="/fix-remote/{slug}"
          data-api="/api/fix-remote/{slug}" data-slug="{slug}">
      <button class="btn ghost" type="submit">Rename origin &rarr; github</button>
    </form>
  </div>"""

# Collapsed, and fetched only when opened: the pane costs a capture per session,
# which is the whole cost this design took off the polling path.
_OUTPUT = """
  <details class="out" data-slug="{slug}">
    <summary>Show output</summary>
    <pre class="tail">Loading…</pre>
  </details>"""

# The label says what the link does; the URL itself was unreadable on a phone
# and only crowded the tap target. It is still the href, so long-press copies it.
_LINK = """
  <a class="open" href="{url}">Open in the Claude app</a>"""

_WHY = """
  <div class="why">{reason}</div>"""

_EMPTY = """<div class="empty">No projects here yet. Clone a repo into
<code>~/projects</code> on the devbox and it shows up on this page.</div>"""

_BANNER = """<div class="banner">
  <div class="name">This box is signed out of Claude</div>
  <p>No listener can start until it is signed in again.</p>
  <form method="post" action="/login/start"><button class="btn" type="submit">Sign in</button></form>
</div>"""


def row(*, slug, name, state, ident, label, action, reason=None, url=None,
        status=None, age=None, warning=None, retry=False, fresh=False,
        parallel=None, mode=None) -> str:
    """One project row. `state` doubles as the rail's colour and as the
    machine-readable marker the client script and the tests read."""
    running = action == "stop"
    return _ROW.format(
        slug=escape(slug), state=escape(state), name=escape(name),
        action=escape(action), label=escape(label),
        pending="Starting…" if action == "start" else "Stopping…",
        cls="stop" if running else "start",
        status=f'<span class="state">{escape(status)}</span>' if status else "",
        age=_AGE.format(age=escape(age)) if age else "",
        ident=escape(ident),
        why=_WHY.format(reason=escape(reason)) if reason else "",
        retry=_RETRY.format(slug=escape(slug)) if retry else "",
        fresh=_FRESH.format(slug=escape(slug)) if fresh else "",
        # None: no toggle on this row (a running listener's mode is fixed)
        parallel="" if parallel is None else _PARALLEL.format(
            slug=escape(slug), state="on" if parallel else "off",
            next="0" if parallel else "1",
            note=_PARALLEL_NOTE if parallel else ""),
        mode=_AGE.format(age=escape(mode)) if mode else "",
        warn=_WARN.format(text=escape(warning), slug=escape(slug)) if warning else "",
        link=_LINK.format(url=escape(url)) if url else "",
        # Nothing to read on a listener that is not running.
        output=_OUTPUT.format(slug=escape(slug)) if running else "")


def page(*, rows: list[str], count: str, usage: str = "", signed_in: bool) -> str:
    return _PAGE.format(css=_CSS, js=_JS, count=escape(count), usage=escape(usage),
                        banner="" if signed_in else _BANNER,
                        rows="\n".join(rows) or _EMPTY)


_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>devbox sign-in</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header><h1>devbox sign-in</h1></header>
{body}
<a class="back" href="/">Back to sessions</a>
</div>
</body>
</html>
"""

_LOGIN_STEPS = """<div class="card">
  {note}
  <p class="step">1. Open this link and approve the sign-in.</p>
  <a class="link" href="{url}">{url}</a>
  <p class="step">2. Paste the code it gives you.</p>
  <form method="post" action="/login/code">
    <input name="code" autocapitalize="off" autocorrect="off" spellcheck="false"
           autocomplete="off" placeholder="paste the code here">
    <button class="btn" type="submit">Sign in</button>
  </form>
</div>"""

_LOGIN_WAITING = """<div class="card">
  <p>Starting the sign-in.</p>
  <p class="hint">The link takes a few seconds to appear.</p>
  <form method="post" action="/login/start"><button class="btn" type="submit">Retry</button></form>
</div>"""

_LOGIN_DONE = """<div class="card">
  <p>This box is signed in.</p>
  <p class="hint">The listeners you had running are coming back.</p>
</div>"""

_LOGIN_ERROR = """<div class="card">
  <p class="err">{error}</p>
  <p class="hint">The code may have been mistyped, or the sign-in may have timed
  out. Starting again gives you a fresh link.</p>
  <form method="post" action="/login/start"><button class="btn" type="submit">Start again</button></form>
</div>"""


def login_page(body: str) -> str:
    return _LOGIN_PAGE.format(css=_CSS, body=body)


def login_steps(*, url: str, error: str | None = None) -> str:
    note = f'<p class="err">{escape(error)}</p>' if error else ""
    return _LOGIN_STEPS.format(url=escape(url), note=note)


def login_waiting() -> str:
    return _LOGIN_WAITING


def login_done() -> str:
    return _LOGIN_DONE


def login_error(error: str) -> str:
    return _LOGIN_ERROR.format(error=escape(error))
