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

/* An instance belongs to the row above it: indented, and a step quieter, so
   the list still reads as one project per block. */
.row.instance { margin-left: 1.25rem; }
.row.instance .name { font-size: .9375rem; }

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
form.fix, form.fresh, form.gap { margin: .625rem 0 0; }
.note { margin-top: .375rem; font-size: .75rem; color: var(--ink-dim); }
/* Not `.link` — that name is taken by the login page's authorize URL, which is
   monospace, and .btn.link would inherit its family. */
.btn.quiet { width: 100%; background: transparent; border-color: var(--edge);
             color: var(--ink-dim); font-weight: 500; font-size: .875rem; }

.new { margin-top: .625rem; }
.new summary { font-size: .875rem; color: var(--ink-dim); cursor: pointer;
               padding: .5rem 0; list-style: none; }
.new summary::-webkit-details-marker { display: none; }
.new summary::before { content: "＋ "; }
.new input { margin-top: .5rem; }

/* The permission picker. Four chips on one line at phone width, wrapping to
   two if the labels grow; the current one is filled so it reads at a glance
   from the closed summary line above it. */
.modes { margin-top: .625rem; }
.modes summary { font-size: .8125rem; color: var(--ink-dim); cursor: pointer;
                 padding: .25rem 0; list-style: none; }
.modes summary::-webkit-details-marker { display: none; }
.modes summary::before { content: "▸ "; }
.modes[open] summary::before { content: "▾ "; }
.chips { display: flex; flex-wrap: wrap; gap: .375rem; margin-top: .375rem; }
.chips form { margin: 0; flex: 1 1 auto; }
.btn.chip { width: 100%; min-width: 0; padding: .375rem .625rem;
            background: transparent; border-color: var(--edge);
            color: var(--ink-dim); font-weight: 500; font-size: .8125rem; }
.btn.chip.on { background: var(--steel); border-color: var(--steel);
               color: var(--steel-ink); font-weight: 600; }

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

/* The run page. The output is the point, so it gets the width and a height
   that leaves the input box on screen beneath it. */
label { display: block; margin: 0 0 .375rem; font-size: .8125rem; color: var(--ink-dim); }
select, textarea {
  width: 100%; margin-bottom: .75rem; padding: .625rem .75rem;
  font: inherit; font-size: 1rem; color: var(--ink); background: #101b23;
  border: 1px solid var(--edge); border-radius: .375rem;
}
select { min-height: 2.75rem; }
textarea { min-height: 7rem; font-family: var(--mono); resize: vertical; }
.cmd { font-family: var(--mono); font-size: .8125rem; white-space: pre-wrap;
       overflow-wrap: anywhere; margin: 0 0 .25rem; }
.where { font-family: var(--mono); font-size: .75rem; color: var(--ink-dim); margin: 0 0 .75rem; }
.term { margin: 0 0 .75rem; padding: .625rem .75rem; border-radius: .375rem;
        background: #0b1319; border: 1px solid var(--edge);
        font-family: var(--mono); font-size: .75rem; line-height: 1.4;
        white-space: pre-wrap; overflow-wrap: anywhere;
        max-height: 55vh; overflow-y: auto; }
.exit { font-weight: 600; margin: 0 0 .75rem; }
.exit.ok { color: var(--live); }
.exit.bad { color: var(--dead); }
.exit.busy { color: var(--wait); }
.pair { display: flex; gap: .5rem; }
.pair > * { flex: 1; }
.pair form { margin: 0; }
.pair .btn { width: 100%; }
.panel .btn { width: 100%; }
.panel form + form, .panel .pair { margin-top: .5rem; }
.panel .term { max-height: 50vh; }
a.btn { display: inline-flex; align-items: center; justify-content: center;
        text-decoration: none; }

/* The per-row control. Quiet, like the other secondary controls: running a
   command is occasional, and it must not compete with Start/Stop. */
.runrow { margin-top: .625rem; display: flex; gap: .75rem; align-items: center; }
.runrow .btn { flex: 1; }
.runstate { flex: none; font-size: .8125rem; font-weight: 600; text-decoration: none; }
.runstate[data-tone="busy"] { color: var(--wait); }
.runstate[data-tone="ok"] { color: var(--live); }
.runstate[data-tone="bad"] { color: var(--dead); }

dialog.runbox {
  width: min(40rem, calc(100vw - 1rem)); max-width: none;
  max-height: calc(100dvh - 1rem); margin: auto; padding: 1rem;
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--edge); border-radius: .625rem; overflow-y: auto;
}
dialog.runbox::backdrop { background: rgba(0, 0, 0, .6); }
.dlg-head { display: flex; justify-content: space-between; align-items: center;
            gap: .75rem; margin-bottom: .75rem; }
.dlg-head form { margin: 0; }
.dlg-head .btn { min-width: 0; }
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
      // Nor while its "New instance" disclosure is open: the same poll would
      // otherwise snap it shut and, worse, wipe a name half-typed on a phone.
      // Belt and braces below — either alone would do, but a background poll
      // eating someone's typing is exactly the failure this guards against.
      if (row && row.querySelector('details.new[open]')) return;
      // Nor while the permission picker is open: a poll landing between
      // reading the modes and tapping one would shut it under the finger.
      if (row && row.querySelector('details.modes[open]')) return;
      if (row && row.contains(document.activeElement)) return;
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
    // Sent as a body, not just a slug in the URL: Create carries the typed
    // name as a form field, and an empty body costs nothing for the forms
    // that have none (Start, Stop, Remove).
    fetch(form.dataset.api, { method: 'POST', headers: { 'Accept': 'application/json' },
                              body: new URLSearchParams(new FormData(form)) })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        // Clear pending before apply(), not after: apply() skips any row
        // still carrying data-pending, on the assumption its own response is
        // seconds away and authoritative. This *is* that response, so the
        // row must stop looking in-flight before it is asked to re-render —
        // otherwise a successful Remove leaves a disabled ghost row behind,
        // a successful Create freezes its parent row forever, and schedule()
        // never sees the busy row clear, polling at 4s instead of 20s for
        // good.
        row.removeAttribute('data-pending');
        applyPayload(data);
      })
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
    var form = event.target.closest('form.act, form.fix, form.fresh, form.gap');
    if (!form || !form.dataset.api) return;
    event.preventDefault();
    // Start fresh, Create and Remove all take seconds — a worktree add, or the
    // full launch() path, or tmux settling — so they show the same pending
    // state as Start/Stop; the remote rename and the parallel toggle return in
    // milliseconds and need none. ".gap" is a styling class shared with
    // ".fix" (same amount of breathing room above the button); it is not the
    // same behaviour, which is why it is not just called "fix" too.
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

  // Run command: a modal over the list rather than a page of its own. The
  // links still lead to /run/<slug>, which is where a browser without
  // <dialog> (or without this script) ends up.
  var box = document.getElementById('runbox');
  var panel = null;
  if (box && typeof box.showModal === 'function') {
    list.addEventListener('click', function (event) {
      var link = event.target.closest('a[data-run]');
      if (!link) return;
      event.preventDefault();
      document.getElementById('runbox-title').textContent = link.dataset.name;
      var body = document.getElementById('runbox-body');
      body.innerHTML = '<p class="hint">Loading…</p>';
      panel = window.runPanel(body, link.dataset.run);
      box.showModal();
    });
    // A tap on the backdrop closes it, as a phone user expects.
    box.addEventListener('click', function (event) { if (event.target === box) box.close(); });
    // The command keeps running; the row's label is refreshed to say so.
    box.addEventListener('close', function () {
      if (panel) panel.stop();
      panel = null;
      poll();
    });
  }
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
{runbox}
<script>{js}</script>
</body>
</html>
"""

_ROW = """<div class="row{cls_row}" data-slug="{slug}" data-state="{state}">
  <div class="head">
    <div class="name">{name}</div>
    <form class="act" method="post" action="/{action}/{slug}" data-slug="{slug}"
          data-api="/api/{action}/{slug}" data-label="{label}"
          data-pending-label="{pending}">
      <button class="btn {cls}" type="submit">{label}</button>
    </form>
  </div>
  <div class="meta">{status}{age}{mode}<span class="id">{ident}</span></div>{why}{retry}{fresh}{remove}{permission}{parallel}{new}{warn}{link}{output}{run}
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

# Removal takes the worktree with it, so it sits apart from Stop and refuses
# rather than forcing — see worktrees.blockers. It stops the listener and
# runs `git worktree remove`, seconds of work, so it goes through act() (the
# ".gap" class), not fix(), and gets its own pending label.
_REMOVE = """
  <form class="gap" method="post" action="/instances/{slug}/remove"
        data-api="/api/instances/{slug}/remove" data-slug="{slug}"
        data-label="Remove instance" data-pending-label="Removing…">
    <button class="btn quiet" type="submit">Remove instance</button>
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

# The permission mode the listener's sessions run in. The Claude app has no
# control over it and the CLI fixes it at startup, so a session stuck asking
# for permission on a phone could only be fixed from a terminal — this is that
# control. Summary-first so the current mode reads without opening anything:
# the row says which mode it is in whether or not you came here to change it.
_MODE = """
  <details class="modes">
    <summary>Permission mode: {current}</summary>
    <div class="chips">{chips}</div>
    <div class="note">{note}</div>
  </details>"""

# One form per mode rather than a <select>: a select on a phone is a spinner
# and two more taps, and these are four fixed choices.
_MODE_CHIP = """<form class="{cls}" method="post" action="/mode/{slug}"
      data-api="/api/mode/{slug}" data-slug="{slug}" data-mode="{mode}"
      data-label="{label}" data-pending-label="Switching…">
      <input type="hidden" name="mode" value="{mode}">
      <button class="btn chip{on}" type="submit">{label}</button>
    </form>"""

# The CLI's mode names are not what anyone calls them out loud.
_MODE_LABELS = {
    "default": "Ask",
    "acceptEdits": "Accept edits",
    "auto": "Auto",
    "plan": "Plan",
}

# Said on a running row only, because that is the only place it costs anything:
# the listener has to come back up before the new mode is in force.
_MODE_RESTART_NOTE = ("Changing this restarts the listener and resumes the "
                      "same conversation.")
_MODE_NOTE = "Used by every session this listener opens."

# Its own disclosure: an instance is a new worktree and a new listener, which
# is not something to create by brushing a button while scrolling.
# `git worktree add` plus the full launch() path takes several seconds — the
# same order as Start — so this goes through act() (the ".gap" class), not
# fix(), and gets its own pending label rather than looking merely stuck.
_NEW_INSTANCE = """
  <details class="new">
    <summary>New instance</summary>
    <form class="gap" method="post" action="/instances/{slug}"
          data-api="/api/instances/{slug}" data-slug="{slug}"
          data-label="Create" data-pending-label="Creating…">
      <input name="name" autocapitalize="off" autocorrect="off" spellcheck="false"
             autocomplete="off" placeholder="name, e.g. fix-auth">
      <button class="btn quiet" type="submit">Create</button>
    </form>
  </details>"""

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

# The same amber block without the rename button: a refused name or git's own
# error has nothing for that button to fix.
_NOTE = """
  <div class="warn"><div>{text}</div></div>"""

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

# The label is only there once a command has been started from this row, and
# it opens the same modal: closing the modal does not stop the command, so the
# row is where you find it again.
_RUN_ROW = """
  <div class="runrow">{state}<a class="btn quiet" href="/run/{slug}" data-run="{slug}"
     data-name="{name}">Run command</a></div>"""

_RUN_STATE = """<a class="runstate" href="/run/{slug}" data-run="{slug}" data-name="{name}"
     data-tone="{tone}">{label}</a>"""

_WHY = """
  <div class="why">{reason}</div>"""

_EMPTY = """<div class="empty">No projects here yet. Clone a repo into
<code>~/projects</code> on the devbox and it shows up on this page.</div>"""

_BANNER = """<div class="banner">
  <div class="name">This box is signed out of Claude</div>
  <p>No listener can start until it is signed in again.</p>
  <form method="post" action="/login/start"><button class="btn" type="submit">Sign in</button></form>
</div>"""


def permission_label(mode: str | None) -> str:
    """What a mode is called on the page. An unrecorded mode reads as the one
    the CLI would pick for itself, which is what the listener actually gets."""
    return _MODE_LABELS.get(mode or "default", mode or "default")


def _permission(slug: str, current: str | None, running: bool) -> str:
    # ".gap" on a running row, ".fix" on a stopped one: the first restarts the
    # listener and takes seconds, so it goes through the client's pending path
    # (see ui._JS); the second only writes a line of state and comes straight
    # back. Both are ordinary forms without JavaScript.
    cls = "gap" if running else "fix"
    selected = current or "default"
    chips = "".join(
        _MODE_CHIP.format(slug=escape(slug), mode=escape(mode), cls=cls,
                          label=escape(label), on=" on" if mode == selected else "")
        for mode, label in _MODE_LABELS.items())
    return _MODE.format(current=escape(permission_label(current)), chips=chips,
                        note=_MODE_RESTART_NOTE if running else _MODE_NOTE)


def row(*, slug, name, state, ident, label, action, reason=None, url=None,
        status=None, age=None, warning=None, retry=False, fresh=False,
        parallel=None, mode=None, run=None, run_tone="", instance=False,
        remove=False, new_instance=False, fix_remote=True,
        origin_slug=None, permission=None) -> str:
    """One project row. `state` doubles as the rail's colour and as the
    machine-readable marker the client script and the tests read."""
    running = action == "stop"
    return _ROW.format(
        slug=escape(slug), state=escape(state), name=escape(name),
        action=escape(action), label=escape(label),
        pending="Starting…" if action == "start" else "Stopping…",
        cls="stop" if running else "start",
        cls_row=" instance" if instance else "",
        status=f'<span class="state">{escape(status)}</span>' if status else "",
        age=_AGE.format(age=escape(age)) if age else "",
        ident=escape(ident),
        why=_WHY.format(reason=escape(reason)) if reason else "",
        retry=_RETRY.format(slug=escape(slug)) if retry else "",
        fresh=_FRESH.format(slug=escape(slug)) if fresh else "",
        remove=_REMOVE.format(slug=escape(slug)) if remove else "",
        # On every row, running or not: unlike the spawn mode, this one can be
        # changed under a live listener — that is the whole point of it, since
        # nothing else on a phone can.
        permission=_permission(slug, permission, running),
        # None: no toggle on this row (a running listener's mode is fixed)
        parallel="" if parallel is None else _PARALLEL.format(
            slug=escape(slug), state="on" if parallel else "off",
            next="0" if parallel else "1",
            note=_PARALLEL_NOTE if parallel else ""),
        mode=_AGE.format(age=escape(mode)) if mode else "",
        new=_NEW_INSTANCE.format(slug=escape(slug)) if new_instance else "",
        # The Rename button always acts on the project, never the instance:
        # a worktree has no `origin` of its own to rename, only the parent
        # does. `origin_slug` defaults to this row's own slug, which is
        # correct for a project row and irrelevant when there's no button.
        warn=(_WARN if fix_remote else _NOTE).format(
            text=escape(warning), slug=escape(origin_slug or slug)) if warning else "",
        link=_LINK.format(url=escape(url)) if url else "",
        # Nothing to read on a listener that is not running.
        output=_OUTPUT.format(slug=escape(slug)) if running else "",
        # None: running commands is off, so no control at all
        run="" if run is None else _RUN_ROW.format(
            slug=escape(slug), name=escape(name),
            state=_RUN_STATE.format(slug=escape(slug), name=escape(name),
                                    tone=escape(run_tone), label=escape(run)) if run else ""))


def page(*, rows: list[str], count: str, usage: str = "", signed_in: bool,
         run: bool = False) -> str:
    return _PAGE.format(css=_CSS, js=(_RUN_JS + _JS) if run else _JS,
                        runbox=_RUNBOX if run else "",
                        count=escape(count), usage=escape(usage),
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



_RUNBOX = """<dialog id="runbox" class="runbox" aria-labelledby="runbox-title">
  <div class="dlg-head">
    <div class="name" id="runbox-title"></div>
    <form method="dialog"><button class="btn stop" type="submit">Close</button></form>
  </div>
  <div id="runbox-body" class="panel"></div>
</dialog>"""

_RUN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Run · {title}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header><h1>{title}</h1></header>
<div class="card panel" id="panel" data-slug="{slug}">{body}</div>
<a class="back" href="/">Back to sessions</a>
</div>
{script}
</body>
</html>
"""

_RUN_FORBIDDEN = """<p class="err">Not allowed.</p>
<p class="hint">Running commands is only open to the box's owner, over the
tailnet, from the launcher's own page.</p>"""

_RUN_FORM = """<form method="post" action="/run/{slug}" data-api="/api/run/{slug}">
  <label for="command-{slug}">Command</label>
  <textarea id="command-{slug}" name="command" autocapitalize="off" autocorrect="off"
            spellcheck="false" placeholder="paste the command Claude gave you"></textarea>
  <button class="btn" type="submit">Run</button>
</form>
<p class="hint">Runs as you, in a login shell, in this project's directory.</p>"""

_RUN_VIEW = """<p class="cmd">{command}</p>
<p class="where">{cwd}</p>
<pre class="term">{output}</pre>
<p class="exit {exit_cls}">{exit_text}</p>
{controls}"""

# Only while the command is alive: an answer to a prompt, and a way out.
_RUN_LIVE = """<form method="post" action="/run/{slug}/input" data-api="/api/run/{slug}/input">
  <input name="text" autocapitalize="off" autocorrect="off" spellcheck="false"
         autocomplete="off" placeholder="answer a prompt (sent with Enter)">
  <button class="btn" type="submit">Send</button>
</form>
<div class="pair">
  <form method="post" action="/run/{slug}/interrupt" data-api="/api/run/{slug}/interrupt">
    <button class="btn stop" type="submit">Ctrl-C</button></form>
  <button class="btn stop" type="button" data-copy>Copy output</button>
</div>"""

_RUN_DONE = """<div class="pair">
  <button class="btn" type="button" data-copy>Copy output</button>
  <form method="post" action="/run/{slug}/close" data-api="/api/run/{slug}/close">
    <button class="btn stop" type="submit">Done</button></form>
</div>"""

# Drives one run panel — the modal's body on the main page, or the card on
# /run/<slug>. The server renders the panel (the /api/run replies carry it);
# this only swaps it in when the state changes, and in between just updates
# the output, so an answer half-typed into the input box survives a poll.
_RUN_JS = """
window.runPanel = function (root, slug) {
  var api = '/api/run/' + encodeURIComponent(slug);
  var timer = null, key = null, stopped = false;

  function ok(r) { if (!r.ok) throw new Error(r.status); return r.json(); }
  function keyOf(d) { return d.exists + '/' + d.running; }
  function term() { return root.querySelector('.term'); }

  function render(d) {
    key = keyOf(d);
    root.innerHTML = d.html;
    wire();
    var t = term();
    if (t) t.scrollTop = t.scrollHeight;
  }

  function apply(d) {
    if (stopped) return;
    if (keyOf(d) !== key) {
      render(d);
    } else {
      var t = term();
      if (t && t.textContent !== d.output) {
        var atEnd = t.scrollTop + t.clientHeight >= t.scrollHeight - 8;
        t.textContent = d.output;
        if (atEnd) t.scrollTop = t.scrollHeight;
      }
    }
    clearTimeout(timer);
    if (d.running) timer = setTimeout(load, document.hidden ? 5000 : 1500);
  }

  function load() {
    fetch(api, { headers: { 'Accept': 'application/json' } })
      .then(ok).then(apply)
      .catch(function () { if (!stopped) timer = setTimeout(load, 5000); });
  }

  function note(text) {
    var old = root.querySelector('.why');
    if (old) old.remove();
    var el = document.createElement('p');
    el.className = 'why';
    el.textContent = text;
    root.appendChild(el);
  }

  function wire() {
    root.querySelectorAll('form[data-api]').forEach(function (form) {
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        var button = form.querySelector('button');
        if (button) button.disabled = true;
        fetch(form.dataset.api, { method: 'POST', headers: { 'Accept': 'application/json' },
                                  body: new URLSearchParams(new FormData(form)) })
          .then(ok)
          .then(function (d) {
            var input = form.querySelector('input[name="text"]');
            if (input) input.value = '';
            if (button) button.disabled = false;
            apply(d);
          })
          .catch(function () {
            if (button) button.disabled = false;
            note('The devbox did not answer. Check that it is up, then try again.');
          });
      });
    });
    root.querySelectorAll('[data-copy]').forEach(function (button) {
      button.addEventListener('click', function () {
        var part = function (sel) { var el = root.querySelector(sel); return el ? el.textContent : ''; };
        var text = '$ ' + part('.cmd') + '\\n' + part('.term') + '\\n' + part('.exit');
        navigator.clipboard.writeText(text).then(
          function () { button.textContent = 'Copied'; },
          function () { button.textContent = 'Copy failed'; });
      });
    });
  }

  load();
  return { stop: function () { stopped = true; clearTimeout(timer); } };
};
"""


def run_page(title: str, body: str, slug: str = "") -> str:
    script = (f"<script>{_RUN_JS}\nwindow.runPanel(document.getElementById('panel'), "
              f"document.getElementById('panel').dataset.slug);</script>") if slug else ""
    return _RUN_PAGE.format(css=_CSS, title=escape(title), slug=escape(slug),
                            body=body, script=script)


def run_forbidden() -> str:
    return _RUN_FORBIDDEN


def run_form(*, slug: str) -> str:
    return _RUN_FORM.format(slug=escape(slug))


def run_view(*, slug: str, command: str, cwd: str, output: str, running: bool,
             exit_code: int | None) -> str:
    if running:
        exit_cls, exit_text = "busy", "Running…"
    elif exit_code is None:
        exit_cls, exit_text = "bad", "Finished (exit code unknown)"
    else:
        exit_cls = "ok" if exit_code == 0 else "bad"
        exit_text = f"Finished — exit code {exit_code}"
    controls = (_RUN_LIVE if running else _RUN_DONE).format(slug=escape(slug))
    return _RUN_VIEW.format(
        command=escape(command), cwd=escape(cwd), output=escape(output),
        exit_cls=exit_cls, exit_text=exit_text, controls=controls)
