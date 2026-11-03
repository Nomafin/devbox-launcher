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

from .agents.runtime import SOCKET

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
  --raised: #1c2a35;
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
/* The project list: an agent's title and its buttons share one line. */
.wrap.wide { max-width: 47.5rem; }
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
form.fix, form.gap { margin: .625rem 0 0; }
.note { margin-top: .375rem; font-size: .75rem; color: var(--ink-dim); }
.terminal { margin: .625rem 0 0; }
/* Not `.link` — that name is taken by the login page's authorize URL, which is
   monospace, and .btn.link would inherit its family. */
.btn.quiet { width: 100%; background: transparent; border-color: var(--edge);
             color: var(--ink-dim); font-weight: 500; font-size: .875rem; }

.new summary { display: flex; align-items: center; justify-content: center;
               list-style: none; }
.new summary::-webkit-details-marker { display: none; }
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

/* A live agent's whole point is the link, so it gets a row of its own and a
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
/* Ghosted, not red: stopping an agent is routine and keeps the
   conversation, and coral is spoken for — it means a state, not a control.
   Four red Stop buttons shouted louder than the one row that had failed. */
.btn.stop { background: transparent; color: var(--ink-dim); }
.btn:active { transform: translateY(1px); }
.btn[disabled] { opacity: .65; cursor: default; }
:focus-visible { outline: 2px solid var(--live); outline-offset: 2px; }

/* The only motion on the page, and it answers a tap: it marks the seconds a
   session takes to settle, which is otherwise indistinguishable from a
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
.row.agent.parked { display: none; }
body.show-parked .row.agent.parked { display: block; opacity: .7; }
.mission { font-size: 13px; color: var(--ink-dim); margin-top: 4px; }

/* The project card. Its sections carry their own padding so the agent list
   can run edge to edge between two rules. */
.row.project { padding: 0; border-left-width: 1px; overflow: hidden; }
.phead { display: flex; justify-content: space-between; align-items: flex-start;
         gap: .75rem; padding: .875rem 1rem .625rem; }
.pwho { min-width: 0; }
.pbranch { flex: none; max-width: 45%; overflow: hidden; white-space: nowrap;
           text-overflow: ellipsis; padding: .125rem .5rem; border-radius: .375rem;
           background: var(--raised); border: 1px solid var(--edge);
           font-family: var(--mono); font-size: .75rem; }
.row.project > .warn, .row.project > .why { margin: 0 1rem .625rem; }
.agents { border-top: 1px solid var(--edge); padding: .5rem .75rem 0; }
/* No heading over an empty list, nor over one whose agents are all parked
   out of sight. */
.agents:not(:has(.row.agent:not(.parked))) { display: none; }
body.show-parked .agents:has(.row.agent) { display: block; }
.atitle { padding: 0 .25rem; font-size: .6875rem; text-transform: uppercase;
          letter-spacing: .08em; color: var(--ink-dim); }
.pfoot { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
         padding: .625rem .75rem .875rem; border-top: 1px solid var(--edge); }
.agents + .pfoot { border-top: 0; }
.pfoot > * { flex: 1 1 10rem; }
.pfoot > form { margin: 0; }
.pfoot .btn { width: 100%; }
.pfoot .new[open] { flex-basis: 100%; }
.pfoot .runstate { flex: none; }

/* An agent: one line until opened. The rail still carries its state. */
.row.agent { margin: .5rem 0; padding: 0; background: var(--raised); }
.ahead { display: flex; align-items: center; gap: .625rem; padding: .625rem .75rem;
         cursor: pointer; }
/* The chevron is the script's, so without it there is none: the details
   are simply always shown. */
.chev { display: none; flex: none; width: 32px; height: 32px; padding: 0;
        place-items: center; border: 1px solid var(--edge); border-radius: .375rem;
        background: transparent; color: var(--ink); cursor: pointer; }
.js .chev { display: grid; }
.chev svg { display: block; transition: transform .15s; }
.row.agent.open .chev svg { transform: rotate(90deg); }
.ahead:hover .chev { border-color: var(--steel); background: var(--steel); }
.awho { flex: 1; min-width: 0; }
.awho .name { font-size: .9375rem; }
.name code { font-family: var(--mono); font-size: .8125rem; padding: 1px 6px;
             background: var(--body); border: 1px solid var(--edge); border-radius: .3rem; }
.awho .meta { margin-top: .125rem; gap: .25rem .625rem; }
.abtns { display: flex; gap: .5rem; flex: none; }
.abtns .btn { width: auto; min-width: 0; padding: .5rem .75rem; font-size: .875rem; }
.row.agent > .why { margin: 0 .75rem .625rem; }
.adetail { padding: 0 .75rem .75rem 3.375rem; }
.adetail > .meta { margin-top: 0; }
.js .row.agent:not(.open) .adetail { display: none; }
.adbtns { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .625rem; }
.adbtns form.gap { margin: 0; }
@media (prefers-reduced-motion: reduce) { .chev svg { transition: none; } }
/* On a phone the buttons take a line of their own under the title. */
@media (max-width: 560px) {
  .ahead { flex-wrap: wrap; }
  .awho { flex: 1 1 calc(100% - 3.25rem); }
  .abtns { flex: 1 1 100%; }
  .js .abtns { padding-left: 2.625rem; }
  .abtns > *, .abtns form.act { flex: 1; }
  .abtns .btn { width: 100%; }
  .adetail { padding-left: .75rem; }
}


/* The Clone and New project pages and the header links that lead to them. */
.links { display: flex; flex-wrap: wrap; gap: .25rem 1.25rem; margin: -.5rem 0 1.25rem;
         font-size: .875rem; }
.links a { color: var(--ink-dim); text-decoration: none; }
.card + .card, .card + .row, .row + .card { margin-top: .75rem; }
.card form + form, .card form + p { margin-top: .75rem; }
.check { display: flex; align-items: center; gap: .5rem; margin-bottom: .75rem;
         color: var(--ink); font-size: .9375rem; }
.check input { width: auto; min-height: 0; margin: 0; }
.search { display: flex; gap: .5rem; margin-bottom: .75rem; }
.search input { margin: 0; min-width: 0; }
.search .btn { flex: none; }
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
  // Hides each agent's details until it is opened; see .js in the CSS.
  document.documentElement.classList.add('js');
  var timer = null;

  function rows() { return list.querySelectorAll('.row[data-slug]'); }
  function find(slug) { return list.querySelector('.row[data-slug="' + slug + '"]'); }

  // A project card holds its agents' rows, so a plain querySelector on it
  // also finds what is inside them. This finds the card's own match only.
  function own(row, selector) {
    var found = row.querySelectorAll(selector);
    for (var i = 0; i < found.length; i++) {
      if (found[i].closest('.row') === row) return found[i];
    }
    return null;
  }

  // Which agents are open: kept in memory for this page, and in storage per
  // agent key so a reload keeps it. Storage can be missing or refuse (a
  // private window); then agents start closed but still open and close.
  var opened = {};
  function isOpen(slug) {
    if (slug in opened) return opened[slug];
    try { return localStorage.getItem('devbox-open:' + slug) === '1'; } catch (e) { return false; }
  }
  function remember(slug, open) {
    opened[slug] = open;
    try {
      if (open) { localStorage.setItem('devbox-open:' + slug, '1'); }
      else { localStorage.removeItem('devbox-open:' + slug); }
    } catch (e) { /* not remembered, still toggled */ }
  }
  // Applied after every render of a row, so a poll never closes an agent.
  function dress(row) {
    if (!row || !row.classList.contains('agent')) return;
    var open = isOpen(row.dataset.slug);
    row.classList.toggle('open', open);
    var chev = own(row, '.chev');
    if (chev) chev.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  // A card is replaced around its agent list: the rows already in it move
  // into the new card, keeping whatever is open inside them. Each agent row
  // is replaced on its own, from its own entry in the payload.
  function replace(row, html) {
    var holder = document.createElement('div');
    holder.innerHTML = html;
    var fresh = holder.firstElementChild;
    var kids = own(row, '.alist');
    var slot = own(fresh, '.alist');
    if (kids && slot) slot.replaceWith(kids);
    row.replaceWith(fresh);
    return fresh;
  }

  // Where a new row goes: an agent into its project's card, anything else
  // (or an agent whose card is missing) onto the list itself.
  function home(slug) {
    var cut = slug.indexOf('/');
    var card = cut > 0 ? find(slug.slice(0, cut)) : null;
    return (card && own(card, '.alist')) || list;
  }

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
      if (row && own(row, 'details.out[open]')) return;
      // Nor while its "New agent" disclosure is open: the same poll would
      // otherwise snap it shut and, worse, wipe a name half-typed on a phone.
      // Belt and braces below — either alone would do, but a background poll
      // eating someone's typing is exactly the failure this guards against.
      if (row && own(row, 'details.new[open]')) return;
      // Nor while the permission picker is open: a poll landing between
      // reading the modes and tapping one would shut it under the finger.
      if (row && own(row, 'details.modes[open]')) return;
      // Nor while focus is in it — except on a chevron or the copy button,
      // which a click leaves focused: that would freeze an agent the moment
      // it was used. The focus is put back after the swap instead, on the same
      // button if it only moved (into a new card) or on its replacement.
      var focus = document.activeElement;
      var kept = !focus || !row || !row.contains(focus) ? null
        : focus.matches('.chev') ? '.chev'
        : focus.matches('button[data-terminal]') ? 'button[data-terminal]' : null;
      if (row && !kept && row.contains(focus)) return;
      if (row) { row = replace(row, p.html); } else {
        home(p.slug).insertAdjacentHTML('beforeend', p.html);
        row = find(p.slug);
      }
      dress(row);
      if (kept) (list.contains(focus) ? focus : own(row, kept) || focus).focus();
    });
    rows().forEach(function (row) {
      if (!seen[row.dataset.slug] && !row.hasAttribute('data-pending')) row.remove();
    });
    // Reordering moves rows, and moving an element drops its focus.
    var active = document.activeElement;
    reorder(projects);
    if (active !== document.activeElement && list.contains(active)) active.focus();
  }

  // The payload is ordered running-first. Patching rows in place keeps their
  // old positions, so the order is reapplied by moving the existing nodes —
  // appendChild moves rather than copies, which preserves an open disclosure.
  // Each row moves within its own parent: a card within the list, an agent
  // within its card. Never while something is pending: a row must not jump
  // out from under the finger that just tapped it.
  function reorder(projects) {
    if (list.querySelector('.row[data-pending]')) return;
    projects.forEach(function (p) {
      var row = find(p.slug);
      if (row) row.parentNode.appendChild(row);
    });
  }

  // Anywhere on an agent's header opens or closes it, bar its own controls.
  list.addEventListener('click', function (event) {
    var head = event.target.closest('.ahead');
    if (!head) return;
    if (!event.target.closest('.chev') &&
        event.target.closest('button, a, form, input, summary')) return;
    var row = head.closest('.row');
    remember(row.dataset.slug, !row.classList.contains('open'));
    dress(row);
  });
  rows().forEach(dress);

  function applyPayload(data) {
    apply(data.projects || []);
    var count = document.getElementById('count');
    if (count) count.textContent = data.count;
    var usage = document.getElementById('usage');
    if (usage) usage.textContent = data.usage || '';
    var parked = document.getElementById('parked');
    if (parked) { parked.textContent = 'Parked (' + (data.parked || 0) + ')'; parked.hidden = !data.parked; }
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
    var why = own(row, '.why');
    if (why) why.remove();
    // Sent as a body, not just a slug in the URL: Create carries the typed
    // name as a form field, and an empty body costs nothing for the forms
    // that have none (Start, Stop, Park).
    fetch(form.dataset.api, { method: 'POST', headers: { 'Accept': 'application/json' },
                              body: new URLSearchParams(new FormData(form)) })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        // Clear pending before apply(), not after: apply() skips any row
        // still carrying data-pending, on the assumption its own response is
        // seconds away and authoritative. This *is* that response, so the
        // row must stop looking in-flight before it is asked to re-render —
        // otherwise a successful Archive leaves a disabled ghost row behind,
        // a successful Create freezes its parent row forever, and schedule()
        // never sees the busy row clear, polling at 4s instead of 20s for
        // good.
        row.removeAttribute('data-pending');
        // Its own form succeeded, so the disclosure has done its job; left
        // open, apply() would skip the card and Create would stay disabled.
        var disclosure = form.closest('details.new');
        if (disclosure) disclosure.open = false;
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
    var form = event.target.closest('form.act, form.fix, form.gap');
    if (!form || !form.dataset.api) return;
    event.preventDefault();
    // Fresh, Park, Archive, Create and Quick chat all take seconds — a
    // worktree add or remove, or a session settling — so they show the same
    // pending state as Start/Stop; the remote rename and a mode change on a
    // stopped agent return in milliseconds and need none. ".gap" is a styling
    // class shared with ".fix" (same amount of breathing room above the
    // button); it is not the same behaviour, which is why it is not just
    // called "fix" too.
    if (form.classList.contains('fix')) { fix(form); } else { act(form); }
  });

  // The remote rename and a stopped agent's mode. Same shape as act(), but there
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
    fetch('/api/pane/' + details.dataset.slug.split('/').map(encodeURIComponent).join('/'),
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
  // behind a hidden tab. "ready" is NOT motion: an agent not yet connected
  // rests there indefinitely, and treating it as busy meant one
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

  // The terminal command needs the box's name as the browser reached it.
  list.addEventListener('click', function (event) {
    var button = event.target.closest('button[data-terminal]');
    if (!button) return;
    var command = 'ssh -t ' + button.dataset.user + '@' + location.hostname + ' ' +
                  button.dataset.terminal;
    var fallback = function () { window.prompt('Copy this command:', command); };
    if (!navigator.clipboard) return fallback();
    navigator.clipboard.writeText(command).then(
      function () { button.textContent = 'Copied'; }, fallback);
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
<div class="wrap wide">
<header>
  <h1>devbox sessions</h1>
  <span class="count" id="count">{count}</span>{parked}
</header>
<p class="usage" id="usage">{usage}</p>{links}
{banner}
<div id="list">{rows}</div>
<p class="hint">A running agent appears in the Claude app as
<code>&lt;project&gt;/&lt;name&gt;</code>.</p>
</div>
{runbox}
<script>{js}</script>
</body>
</html>
"""

# The permission mode the agent's session runs in. The Claude app has no
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
_MODE_CHIP = """<form class="{cls}" method="post" action="/agents/{slug}/mode"
      data-api="/api/agents/{slug}/mode" data-slug="{slug}" data-mode="{mode}"
      data-label="{label}" data-pending-label="Switching…">
      <input type="hidden" name="mode" value="{mode}">
      <button class="btn chip{on}" type="submit">{label}</button>
    </form>"""

# The CLI's mode names are not what anyone calls them out loud.
_MODE_LABELS = {
    # Records nothing: settings.json's defaultMode (or the CLI's own) applies.
    "default": "Default (settings)",
    "acceptEdits": "Accept edits",
    "auto": "Auto",
    "plan": "Plan",
}

# Said on a running row only, because that is the only place it costs anything:
# the agent has to come back up before the new mode is in force.
_MODE_RESTART_NOTE = ("Changing this restarts the agent and resumes the "
                      "same conversation.")
_MODE_NOTE = "Used the next time this agent starts."

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
    <summary>Screen now</summary>
    <pre class="tail">Loading…</pre>
  </details>"""

# The label says what the link does; the URL itself was unreadable on a phone
# and only crowded the tap target. It is still the href, so long-press copies it.
_LINK = """
  <a class="open" href="{url}">Open in the Claude app</a>"""

# The label is only there once a command has been started from this project,
# and it opens the same modal: closing the modal does not stop the command, so
# the card is where you find it again.
_RUN_ROW = """
    <a class="btn quiet" href="/run/{slug}" data-run="{slug}"
       data-name="{name}">Run a command</a>{state}"""

_RUN_STATE = """<a class="runstate" href="/run/{slug}" data-run="{slug}" data-name="{name}"
     data-tone="{tone}">{label}</a>"""

_WHY = """
  <div class="why">{reason}</div>"""

# A project is a card: its agents live inside it, in .alist, and its actions
# in the footer. The agent rows are separate rows in /api/projects, keyed
# "<project>/<name>"; the server nests them here on a full page load (nest())
# and the client script moves them across when it replaces the card.
_PROJECT_ROW = """<div class="row project" data-slug="{slug}" data-state="{state}">
  <div class="phead">
    <div class="pwho">
      <div class="name">{name}</div>
      <div class="meta"><span class="id">{ident}</span></div>
    </div>{branch}
  </div>{warn}
  <div class="agents"><div class="atitle">Claude agents</div>{alist}</div>{foot}
</div>"""

_ALIST = '<div class="alist"></div>'

_BRANCH = """<span class="pbranch" title="{branch}">&#9095; {branch}</span>"""

_FOOT = """
  <div class="pfoot">{items}
  </div>"""

_NEW_AGENT = """
    <details class="new">
      <summary class="btn quiet">+ New agent on its own branch</summary>
      <form class="gap" method="post" action="/projects/{slug}/new"
            data-api="/api/projects/{slug}/new" data-pending-label="Creating…">
        <input name="name" placeholder="name, e.g. drafts" autocapitalize="off"
               autocomplete="off" spellcheck="false" required>
        <button class="btn" type="submit">Create</button>
      </form>
    </details>
    <form class="gap" method="post" action="/projects/{slug}/scratch"
          data-api="/api/projects/{slug}/scratch" data-pending-label="Starting…">
      <button class="btn quiet" type="submit">Quick question</button>
    </form>"""

# Collapsed, an agent is one line: which branch, its state, and the two
# controls you reach for most. The rest is in .adetail, always in the markup so
# opening it costs no request; the page script hides it until opened, and
# without the script every agent simply reads as open. The reason line sits
# outside it: a failure must show whether or not the row is open.
_AGENT_ROW = """<div class="row agent{cls}" data-slug="{key}" data-state="{state}">
  <div class="ahead">
    <button class="chev" type="button" aria-expanded="false" aria-label="Details">{chevron}</button>
    <div class="awho">
      <div class="name">{title}</div>
      <div class="meta">{status}{age}{active}{place}</div>
    </div>
    <div class="abtns">{terminal}
      <form class="act" method="post" action="/agents/{path}/{action}"
            data-api="/api/agents/{path}/{action}" data-label="{label}"
            data-pending-label="{pending}">
        <button class="btn {btn}" type="submit">{label}</button>
      </form>
    </div>
  </div>{why}
  <div class="adetail">
    <div class="meta"><span class="id">{display}</span></div>{mission}{buttons}{note}{permission}{link}{output}
  </div>
</div>"""

_CHEVRON = ('<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">'
            '<path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')

_AGENT_BUTTONS = """
    <div class="adbtns">{buttons}
    </div>"""

_AGENT_BUTTON = """
      <form class="gap" method="post" action="/agents/{path}/{action}"
            data-api="/api/agents/{path}/{action}" data-pending-label="{pending}">{fields}
        <button class="btn" type="submit">{label}</button>
      </form>"""

_MISSION = """
    <div class="mission">{text}</div>"""

# The host is filled in by the page script from the address the launcher was
# opened at, which is the box's tailnet name.
_TERMINAL = """
      <button class="btn quiet" type="button" data-terminal="{attach}" data-user="{user}">Copy terminal command</button>"""

_TERMINAL_NOTE = """
    <div class="note">Paste the terminal command into your own terminal. Ctrl-b d detaches;
    the agent keeps running.</div>"""

# A page-level problem carried as a row, so it comes and goes with the polls
# like any other: tmux on the agents socket not answering, say.
_NOTICE_ROW = """<div class="row notice" data-slug="{slug}" data-state="failed">
  <div class="name">{title}</div>
  <div class="why">{text}</div>
</div>"""

_PARKED_TOGGLE = """<button id="parked" class="btn" type="button" {hidden}
  onclick="document.body.classList.toggle('show-parked')">Parked ({n})</button>"""

_EMPTY = """<div class="empty">No projects here yet. Clone a repo into
<code>~/projects</code> on the devbox and it shows up on this page.</div>"""

_BANNER = """<div class="banner">
  <div class="name">This box is signed out of Claude</div>
  <p>No agent can start until it is signed in again.</p>
  <form method="post" action="/login/start"><button class="btn" type="submit">Sign in</button></form>
</div>"""


def permission_label(mode: str | None) -> str:
    """What a mode is called on the page. An unrecorded mode reads as the one
    the CLI would pick for itself, which is what the agent actually gets."""
    return _MODE_LABELS.get(mode or "default", mode or "default")


def project_row(*, slug, name, ident, warning=None, fix_remote=True, run=None,
                run_tone="", new=True, branch=None) -> str:
    """One project card, with an empty agent list for nest() or the page
    script to fill. `branch` is the checkout's branch, or None to show none."""
    items = (
        (_NEW_AGENT.format(slug=escape(slug)) if new else "")
        + ("" if run is None else _RUN_ROW.format(
            slug=escape(slug), name=escape(name),
            state=_RUN_STATE.format(slug=escape(slug), name=escape(name), tone=escape(run_tone),
                                    label=escape(run)) if run else "")))
    return _PROJECT_ROW.format(
        slug=escape(slug), state="project", name=escape(name), ident=escape(ident),
        branch=_BRANCH.format(branch=escape(branch)) if branch else "",
        warn=(_WARN if fix_remote else _NOTE).format(text=escape(warning), slug=escape(slug))
        if warning else "",
        alist=_ALIST, foot=_FOOT.format(items=items) if items else "")


def nest(rows: list[dict]) -> list[str]:
    """The /api/projects rows as the page draws them: each agent row inside
    its project's card. An agent whose project has no card stays top-level."""
    agents: dict[str, list[str]] = {}
    for row in rows:
        if "/" in row["slug"]:
            agents.setdefault(row["slug"].split("/", 1)[0], []).append(row["html"])
    out = []
    for row in rows:
        if "/" in row["slug"]:
            continue
        inner = "\n".join(agents.pop(row["slug"], []))
        out.append(row["html"].replace(_ALIST, f'<div class="alist">{inner}</div>', 1)
                   if inner else row["html"])
    return out + [html for orphans in agents.values() for html in orphans]


def notice_row(*, slug, title, text) -> str:
    return _NOTICE_ROW.format(slug=escape(slug), title=escape(title), text=escape(text))


def _agent_button(path, action, label, pending, force=False) -> str:
    fields = '\n    <input type="hidden" name="force" value="1">' if force else ""
    return _AGENT_BUTTON.format(path=escape(path), action=action, label=escape(label),
                                pending=escape(pending), fields=fields)


def agent_row(*, key, name, display, state, status, running, parked, scratch, archivable,
              resumable, broken, mode, age=None, active=None, mission=None, reason=None,
              url=None, stale=False, output=None, ssh_user=None, branch=None,
              own_copy=False) -> str:
    """One agent. `state` is the rail colour and the client's marker, as on
    project rows before: connected / starting / ready / failed / stopped.
    `branch` is what its working directory has checked out (None: unknown, and
    the row is titled by name); `own_copy` says that directory is a worktree
    of its own rather than the project's checkout."""
    path = key                       # "<project>/<name>": both segments are slugs
    if broken:
        action, label, pending, btn = "repair", "Recreate worktree", "Recreating…", "start"
    elif running:
        action, label, pending, btn = "stop", "Stop", "Stopping…", "stop"
    else:
        action, label, pending, btn = "start", "Resume" if resumable else "Start", "Starting…", "start"
    buttons = []
    if not broken and not scratch:
        if stale:
            buttons.append(_agent_button(path, "fresh", "Fresh anyway", "Starting…", force=True))
        else:
            buttons.append(_agent_button(path, "fresh", "Fresh conversation", "Starting…"))
    if not running and not parked and not scratch:
        buttons.append(_agent_button(path, "park", "Park", "Parking…"))
    if archivable and not running:
        buttons.append(_agent_button(path, "archive", "Archive", "Archiving…"))
    terminal = note = ""
    if running and ssh_user and not broken:
        # Quoted twice, as ssh hands the box's login shell the command as one
        # string: zsh, on either side, expands a word starting with "=". Both
        # name segments are slugs, so no quote can be inside.
        attach = f"tmux -u -L {SOCKET} attach -t \"'={display}'\""
        terminal = _TERMINAL.format(attach=escape(attach), user=escape(ssh_user))
        note = _TERMINAL_NOTE
    if scratch:
        title, place = "Quick question", ""
    else:
        title = f"Agent on <code>{escape(branch)}</code>" if branch else escape(name)
        place = _AGE.format(age="own copy of the repo" if own_copy else "project folder")
    return _AGENT_ROW.format(
        cls=" parked" if parked else "", key=escape(key), state=escape(state),
        chevron=_CHEVRON, title=title, place=place, terminal=terminal, note=note,
        path=escape(path), action=action, label=escape(label),
        pending=escape(pending), btn=btn,
        status=f'<span class="state">{escape(status)}</span>' if status else "",
        age=_AGE.format(age=escape(age)) if age else "",
        active=_AGE.format(age=escape(active)) if active else "",
        display=escape(display),
        mission=_MISSION.format(text=escape(mission)) if mission else "",
        why=_WHY.format(reason=escape(reason)) if reason else "",
        buttons=_AGENT_BUTTONS.format(buttons="".join(buttons)) if buttons else "",
        permission="" if scratch else _permission_agent(path, mode, running),
        link=_LINK.format(url=escape(url)) if url else "",
        output=_OUTPUT.format(slug=escape(key)) if (running if output is None else output) else "")


def _permission_agent(path: str, current: str | None, running: bool) -> str:
    cls = "gap" if running else "fix"
    selected = current or "default"
    chips = "".join(
        _MODE_CHIP.format(slug=escape(path), mode=escape(mode), cls=cls, label=escape(label),
                          on=" on" if mode == selected else "")
        for mode, label in _MODE_LABELS.items())
    return _MODE.format(current=escape(permission_label(current)), chips=chips,
                        note=_MODE_RESTART_NOTE if running else _MODE_NOTE)

def page(*, rows: list[str], count: str, usage: str = "", signed_in: bool,
         run: bool = False, parked: int = 0, github: bool = False) -> str:
    return _PAGE.format(css=_CSS, js=(_RUN_JS + _JS) if run else _JS,
                        links=_LINKS if github else "",
                        runbox=_RUNBOX if run else "",
                        count=escape(count), usage=escape(usage),
                        parked=_PARKED_TOGGLE.format(n=parked, hidden="" if parked else "hidden"),
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
  <p class="hint">Start your agents again from the sessions page.</p>
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



# --- GitHub: clone, new project ------------------------------------------------
# Plain forms in the sign-in page's frame: these are visited, not polled, so
# they need none of the main page's script. A running clone is the one page
# that changes on its own, and a meta refresh covers it.

_LINKS = """
<nav class="links"><a href="/clone">Clone</a><a href="/new-project">New project</a></nav>"""

_SUB_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">{refresh}
<title>devbox · {title}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header><h1>{title}</h1></header>
{body}
<a class="back" href="/">Back to sessions</a>
</div>
</body>
</html>
"""

_REFRESH = '\n<meta http-equiv="refresh" content="2">'

_ERR = '<div class="card"><p class="err">{text}</p></div>'

_ACCEPTED = '<div class="card"><p class="hint">Invitations accepted: {repos}</p></div>'

_CLONE_SEARCH = """<form class="search" method="get" action="/clone">
  <input name="q" value="{query}" placeholder="search repositories" autocapitalize="off"
         autocomplete="off" spellcheck="false">
  <button class="btn" type="submit">Search</button>
</form>"""

_REPO_ROW = """<div class="row">
  <div class="head">
    <div class="name">{name}</div>{action}
  </div>
  <div class="meta"><span class="age">{date}</span></div>{description}
</div>"""

_REPO_CLONE = """
    <form class="act" method="post" action="/clone">
      <input type="hidden" name="repo" value="{name}">
      <button class="btn" type="submit">Clone</button>
    </form>"""

_CLONE_RUNNING = """<div class="card">
  <p>Cloning <code>{slug}</code>…</p>
  <p class="hint">This page refreshes by itself.</p>
</div>"""

_CLONE_FAILED = """<div class="card">
  <p class="err">{error}</p>
  <a class="btn" href="/clone">Back to repositories</a>
</div>"""

_CLONE_UNKNOWN = """<div class="card">
  <p>No clone of <code>{slug}</code> is in progress.</p>
  <a class="btn" href="/clone">Clone a repository</a>
</div>"""

_CLONE_DONE = """<div class="card">
  {error}<p>Cloned.{on} Pick the branch to work on.</p>{existing}
  <form method="post" action="/clone/{slug}/branch">
    <label for="new-branch">New branch</label>
    <input id="new-branch" name="branch" autocapitalize="off"
           autocomplete="off" spellcheck="false" required>
    <input type="hidden" name="new" value="1">
    <button class="btn" type="submit">Create branch</button>
  </form>
</div>"""

_CLONE_EXISTING = """
  <form method="post" action="/clone/{slug}/branch">
    <label for="branch">Existing branch</label>
    <select id="branch" name="branch">{options}</select>
    <input type="hidden" name="new" value="">
    <button class="btn" type="submit">Switch</button>
  </form>"""

_NEW_PROJECT = """<div class="card">
  <form method="post" action="/new-project">
    <label for="name">Name</label>
    <input id="name" name="name" value="{name}" placeholder="e.g. kokeilu" autocapitalize="off"
           autocomplete="off" spellcheck="false" required>
    <label class="check"><input type="checkbox" name="github" value="1" checked>
      Create a private GitHub repo for it</label>
    <p class="hint">Starts an empty git repository on <code>main</code>.</p>
    <button class="btn" type="submit">Create</button>
  </form>
</div>"""


def _sub_page(title: str, body: str, refresh: bool = False) -> str:
    return _SUB_PAGE.format(css=_CSS, title=escape(title), body=body,
                            refresh=_REFRESH if refresh else "")


def _err(text: str | None) -> str:
    return _ERR.format(text=escape(text)) if text else ""


def clone_list_page(*, query: str, repos: list[dict], error: str | None = None,
                    accepted: list[str] = ()) -> str:
    """`repos`: dicts of slug, name, description, date and cloned."""
    rows = "".join(_REPO_ROW.format(
        name=escape(r["name"]), date=escape(r["date"]),
        action=f'\n    <a class="state" href="/clone/{escape(r["slug"])}">Cloned</a>' if r["cloned"]
        else _REPO_CLONE.format(name=escape(r["name"])),
        description=_MISSION.format(text=escape(r["description"])) if r["description"] else "")
        for r in repos)
    if not rows and not error:
        rows = '<div class="empty">No repositories found.</div>'
    done = _ACCEPTED.format(repos=escape(", ".join(accepted))) if accepted else ""
    return _sub_page("Clone", _CLONE_SEARCH.format(query=escape(query)) + _err(error) + done + rows)


def clone_status_page(*, slug: str, state: str, error: str | None = None,
                      branches: list[str] = (), current: str = "") -> str:
    """`state`: running, done, failed, or unknown (no such clone). `current`,
    the checked-out branch, is the one the picker starts on."""
    if state == "running":
        return _sub_page("Cloning", _CLONE_RUNNING.format(slug=escape(slug)), refresh=True)
    if state == "failed":
        return _sub_page("Clone failed", _CLONE_FAILED.format(error=escape(error or "")))
    if state == "unknown":
        return _sub_page("Clone", _CLONE_UNKNOWN.format(slug=escape(slug)))
    # A branch made here may not be on the remote yet; it is still where we are.
    names = list(branches) if not current or current in branches else [current, *branches]
    options = "".join(f'<option value="{escape(b)}"{" selected" if b == current else ""}>'
                      f'{escape(b)}</option>' for b in names)
    existing = _CLONE_EXISTING.format(slug=escape(slug), options=options) if names else ""
    return _sub_page(slug, _CLONE_DONE.format(
        slug=escape(slug), existing=existing,
        on=f" On <code>{escape(current)}</code>." if current else "",
        error=f'<p class="err">{escape(error)}</p>' if error else ""))


def new_project_page(*, name: str = "", error: str | None = None) -> str:
    return _sub_page("New project", _err(error) + _NEW_PROJECT.format(name=escape(name)))


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
  var timer = null, key = null, stopped = false, changed = 0;
  // A prompt that prints nothing (`read x`) looks like a command still busy;
  // after a while without output, say it may be waiting for an answer.
  var QUIET_MS = 4000;

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
    var now = Date.now();
    if (keyOf(d) !== key) {
      render(d);
      changed = now;
    } else {
      var t = term();
      if (t && t.textContent !== d.output) {
        var atEnd = t.scrollTop + t.clientHeight >= t.scrollHeight - 8;
        t.textContent = d.output;
        if (atEnd) t.scrollTop = t.scrollHeight;
        changed = now;
      }
    }
    var exit = root.querySelector('.exit');
    if (d.running && exit) {
      exit.textContent = now - changed >= QUIET_MS
        ? 'Running… no output for a while. Waiting for input? Answer below.'
        : 'Running…';
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
             exit_code: int | None, signal: int | None = None) -> str:
    if running:
        exit_cls, exit_text = "busy", "Running…"
    elif exit_code is None:
        exit_cls, exit_text = "bad", "Finished (exit code unknown)"
    else:
        exit_cls = "ok" if exit_code == 0 else "bad"
        exit_text = f"Finished — exit code {exit_code}"
        if signal == 2:
            exit_text += " (interrupted)"
        elif signal:
            exit_text += f" (killed by signal {signal})"
    controls = (_RUN_LIVE if running else _RUN_DONE).format(slug=escape(slug))
    return _RUN_VIEW.format(
        command=escape(command), cwd=escape(cwd), output=escape(output),
        exit_cls=exit_cls, exit_text=exit_text, controls=controls)
