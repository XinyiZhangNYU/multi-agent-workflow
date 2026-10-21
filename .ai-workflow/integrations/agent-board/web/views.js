'use strict';
// Three readings of the same state: a brief, who is working, and a feed.
// The full task list ("list") is the board from app.js. Display only; the one
// write is still the user's "Handled" close from app.js.
const VIEWS = ['brief', 'agents', 'feed', 'list'];
let currentView = null;
function zoneClock(value, opts) {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? 'time unknown' : new Intl.DateTimeFormat('en-US', {timeZone:displayZone(), hourCycle:'h23', ...opts}).format(d);
}
const zoneTime = v => zoneClock(v, {hour:'2-digit', minute:'2-digit'});
const zoneDay = v => zoneClock(v, {month:'long', day:'numeric', weekday:'short'});
function remembered(key, value) { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch {} return null; }
function initialView() {
  const asked = new URL(location.href).searchParams.get('view');
  if (VIEWS.includes(asked)) return asked;
  const last = remembered('agent-board-view');
  return VIEWS.includes(last) ? last : 'brief';
}
function showView(view, remember = true) {
  currentView = view;
  for (const n of document.querySelectorAll('#views > [data-view]')) n.hidden = n.dataset.view !== view;
  for (const b of document.querySelectorAll('.view-tab')) b.setAttribute('aria-current', String(b.dataset.view === view));
  const url = new URL(location.href); url.searchParams.set('view', view); history.replaceState(null, '', url);
  if (remember) remembered('agent-board-view', view);
  if (view === 'feed') { feedMark = remembered('agent-board-feed-seen'); loadFeed(); }
  renderViews();
}
function quietTask(t) { const at = updateClock(t); return Number.isFinite(at) && Date.now() - at > QUIET_MS; }
function ownerAgent(t) { return (overviewRuns(t).find(r => r.role === 'execution') || t.current)?.agent || ''; }
function latestOf(t) { return Object.hasOwn(t, 'display_updated_at') ? t.display_updated_at : t.business_at; }
function viewHead(title, lead) { const h = el('header', 'view-head'); h.append(el('h1', '', title)); if (lead) h.append(el('p', 'view-lead', lead)); return h; }
function sectionTitle(text) { return el('h2', 'view-section', text); }

// ---- A · Brief ----
function needCard(t, q) {
  const key = qKey(t, q), c = el('article', 'need' + (maybeStale(q) ? ' stale' : ''));
  c.dataset.taskId = t.id; c.dataset.runId = q.run_id; c.dataset.questionId = q.question_id || '';
  c.append(el('p', 'need-q', q.question));
  const waited = q.asked_at ? span(q.asked_at) : null;
  c.append(el('p', 'need-meta', [t.title, q.agent, !q.asked_at ? 'asked at unknown time' : waited === '0 min' ? 'just asked' : 'waited ' + (waited || '?')].filter(Boolean).join(' · ')));
  if (q.options?.length) c.append(el('p', 'need-meta', 'Options: ' + q.options.join(' / ') + ' (reply to the agent in its session)'));
  if (q.unanswered_policy) c.append(el('p', 'need-meta', 'If unanswered: ' + q.unanswered_policy));
  if (maybeStale(q)) c.append(el('p', 'need-meta stale-note', 'The asking run ' + (q.run_ended ? 'has ended' : q.former_owner ? 'was handed off' : 'has been quiet for over 48 h') + ', so this may be outdated; the board never closes it automatically.'));
  const acts = el('div', 'need-acts'), open = button('Details', () => openTask(t.id, open), 'button');
  open.dataset.focus = 'brief-open-' + key;
  acts.append(closeButton(t, q), open); c.append(acts);
  return c;
}
function taskLine(t, prefix) {
  const row = el('div', 'line'); row.dataset.taskId = t.id;
  const what = button('', () => openTask(t.id, what), 'line-what'); what.dataset.focus = prefix + t.id;
  what.append(el('b', '', t.title), el('span', '', t.state.phase || t.next_step?.text || 'No phase reported yet'));
  const tail = el('span', 'line-tags'), asks = openQuestions(t).filter(q => !untold(q)).length;
  if (asks) tail.append(el('span', 'tag ask-tag', 'Needs you ' + asks));
  row.append(el('span', 'line-who', ownerAgent(t) || '—'), what, tail, timeTag(latestOf(t)));
  return row;
}
function renderBrief() {
  const listed = state.tasks.filter(t => !isOld(t));
  const inbox = inboxItems(), fresh = inbox.filter(x => !maybeStale(x.q)), stale = inbox.filter(x => maybeStale(x.q));
  const moving = listed.filter(t => t.state.status !== 'ended' && !quietTask(t)).sort(newestFirst);
  const done = listed.filter(t => t.state.status === 'ended' && !quietTask(t)).sort(newestFirst);
  const silent = listed.filter(t => t.state.status !== 'ended' && quietTask(t)).sort(newestFirst);
  const flags = listed.flatMap(t => openQuestions(t).filter(untold).map(q => ({t, q})));
  const undeclared = listed.filter(t => openDecisions(t).length).sort(newestFirst);
  $('tab-count-brief').textContent = fresh.length ? String(fresh.length) : '';
  preserve($('brief'), () => {
    const parts = [viewHead((fresh.length ? fresh.length + ' waiting for your decision' : 'Nothing new waiting for your decision') + ', ' + moving.length + ' moving',
      done.length + ' finished in the last 48 h' + (silent.length ? '; ' + silent.length + ' silent for over two days (not necessarily a problem)' : '') + '. State read at ' + zoneTime(state.generated_at) + ' (' + displayZone() + ').')];
    if (fresh.length) { parts.push(sectionTitle('Waiting for your decision')); const box = el('div', 'needs'); box.append(...fresh.map(x => needCard(x.t, x.q))); parts.push(box); }
    if (stale.length) { const box = el('div', 'needs'); box.append(...stale.map(x => needCard(x.t, x.q))); const d = disclosure('Possibly outdated · ' + stale.length + ' (the asking run ended, was handed off or was quiet for 48 h; never closed automatically)', 'brief-stale', [box]); d.className = 'brief-fold'; parts.push(d); }
    if (flags.length) {
      const box = el('div', 'flags');
      for (const {t, q} of flags) { const row = el('div', 'flag'); row.dataset.taskId = t.id; row.dataset.runId = q.run_id; const b = button(t.title, () => openTask(t.id, b), 'link'); b.dataset.focus = 'brief-flag-' + qKey(t, q); row.append(b, el('span', 'subtle', (q.agent || q.run_id) + ' declared it needs your action but did not say what'), closeButton(t, q)); box.append(row); }
      parts.push(disclosure('Unstated "needs you" flags · ' + flags.length, 'brief-flags', [box]));
    }
    if (undeclared.length) {
      const box = el('div', 'flags');
      for (const t of undeclared) for (const r of openDecisions(t)) { const row = el('div', 'flag'); row.dataset.taskId = t.id; const b = button(t.title, () => openTask(t.id, b), 'link'); b.dataset.focus = 'brief-decision-' + t.id + '-' + r.id; row.append(b, el('span', 'subtle', roleName(r) + ' · ' + r.state.decision)); box.append(row); }
      parts.push(disclosure('Decisions an agent wrote without saying it needs you · ' + undeclared.length + ' (for review)', 'brief-undeclared', [box]));
    }
    parts.push(sectionTitle('Moving · ' + moving.length));
    const now = el('div', 'lines'); now.append(...moving.map(t => taskLine(t, 'brief-task-')));
    parts.push(moving.length ? now : el('p', 'subtle', 'No open task updated in the last 48 h.'));
    parts.push(sectionTitle('Recently finished · ' + done.length));
    const recent = el('div', 'lines'); recent.append(...done.slice(0, 5).map(t => taskLine(t, 'brief-done-')));
    parts.push(done.length ? recent : el('p', 'subtle', 'No task reported ending in the last 48 h.'));
    if (done.length > 5) { const more = el('div', 'lines'); more.append(...done.slice(5).map(t => taskLine(t, 'brief-done-'))); parts.push(disclosure((done.length - 5) + ' more', 'brief-done-more', [more])); }
    if (silent.length) {
      parts.push(sectionTitle('Silent for over two days · ' + silent.length));
      const quiet = el('div', 'lines'); quiet.append(...silent.map(t => taskLine(t, 'brief-quiet-')));
      parts.push(disclosure(silent.slice(0, 4).map(t => t.title).join(', ') + (silent.length > 4 ? ' and others, ' + silent.length + ' in all' : ''), 'brief-quiet', [quiet]));
    }
    const old = state.tasks.filter(isOld).length;
    if (old) parts.push(el('p', 'subtle view-foot', old + (old === 1 ? ' more old task is' : ' more old tasks are') + ' past the display period; check "Show old tasks" under "All tasks" to see them.'));
    $('brief').replaceChildren(...parts);
  });
}

// ---- B · Who's working ----
function agentClass(agent) { const a = String(agent || '').toLowerCase(); return a.includes('claude') ? 'claude' : a.includes('codex') ? 'codex' : 'other'; }
function workingRuns() {
  const out = [];
  for (const t of state.tasks) {
    if (isOld(t)) continue;
    for (const r of overviewRuns(t)) out.push({t, r, kind:r.role === 'audit' ? 'Audit' : 'Execution'});
    for (const r of parallelRuns(t)) out.push({t, r, kind:'Parallel execution'});
  }
  return out.filter(x => x.r.state?.status !== 'ended' || x.r.human_input_required)
    .sort((a, b) => stableOrder(b.r.business_at || '', a.r.business_at || '') || stableOrder(a.r.id, b.r.id));
}
function agentCard({t, r, kind}) {
  const c = button('', () => openTask(t.id, c), 'agent-card' + (r.stale ? ' quiet' : ''));
  c.dataset.taskId = t.id; c.dataset.runId = r.id; c.dataset.focus = 'agent-' + t.id + '-' + r.id;
  const status = r.state?.status || 'unknown', top = el('span', 'agent-top');
  top.append(el('span', 'avatar ' + agentClass(r.agent), String(r.agent || '?').slice(0, 2)), el('span', 'agent-name', (r.agent || 'Label unknown') + ' · ' + kind),
    el('span', 'pill ' + (r.stale ? 'quiet' : status), r.stale ? (r.business_at ? 'silent for ' + span(r.business_at) : 'Never reported') : (status === 'running' && r.role === 'audit' ? 'Auditing' : names[status] || 'Unknown')));
  c.append(top, el('span', 'agent-task', t.title));
  const asks = openQuestions(t).filter(q => q.run_id === r.id && !untold(q));
  c.append(asks.length ? el('span', 'agent-ask', 'Asks you: ' + firstLine(asks[0].question) + (asks.length > 1 ? ' (' + asks.length + ' in all)' : ''))
    : el('span', 'agent-doing', r.state?.phase || r.state?.next_step || 'No phase reported yet'));
  const foot = el('span', 'agent-foot'); foot.append(el('span', '', t.workstream), el('span', '', ago(r.business_at))); c.append(foot);
  return c;
}
function renderAgents() {
  const runs = workingRuns(), live = runs.filter(x => !x.r.stale), idle = runs.filter(x => x.r.stale);
  const agents = new Set(live.map(x => x.r.agent)), tasks = new Set(live.map(x => x.t.id));
  $('tab-count-agents').textContent = live.length ? String(live.length) : '';
  preserve($('agents'), () => {
    const parts = [viewHead(live.length ? live.length + ' runs working' : 'No run is active right now',
      live.length ? agents.size + ' agents on ' + tasks.size + ' tasks; a run counts as active only if it reported in the last 24 h.' : 'No run reported in the last 24 h. Registered runs are below.')];
    if (live.length) { const grid = el('div', 'agent-grid'); grid.append(...live.map(agentCard)); parts.push(grid); }
    if (idle.length) { const grid = el('div', 'agent-grid'); grid.append(...idle.map(agentCard)); const d = disclosure('Registered but silent for over 24 h · ' + idle.length, 'agents-idle', [grid]); d.className = 'agents-fold'; parts.push(d); }
    if (!runs.length) parts.push(el('p', 'empty', 'No open runs.'));
    $('agents').replaceChildren(...parts);
  });
}

// ---- C · Feed ----
let feedEvents = null, feedPrint = '', feedFilter = 'all', feedMark = null, feedGeneration = 0, feedError = '';
const FEED_LIMIT = 300;
const opWords = {ask:'Asked', resolve:'Resolved', withdraw:'Withdrawn', user_close:'You marked it handled on the page', claim:'Claimed execution', delivery:'Delivery record'};
const feedFilters = {all:() => true, ask:e => ['ask', 'resolve', 'withdraw', 'user_close'].includes(e.op), done:e => e.op === 'progress' && e.status === 'ended', delivery:e => e.op === 'delivery'};
async function loadFeed() {
  if (currentView !== 'feed') return;
  const generation = ++feedGeneration;
  try {
    const r = await get('/api/feed?limit=' + FEED_LIMIT); if (generation !== feedGeneration) return;
    if (r.service !== 'agent-board-v1' || (instance && r.instance_id !== instance)) throw new Error('The service instance changed; reload the whole page');
    feedError = ''; const fp = JSON.stringify(r.events); if (fp === feedPrint) return;
    feedPrint = fp; feedEvents = r.events; renderFeed();
  } catch (e) { if (generation !== feedGeneration) return; feedError = 'Feed read failed; keeping the last content: ' + e.message; renderFeed(); }
}
function feedLine(e) {
  const word = e.op === 'progress' ? (e.status === 'ended' ? 'Finished' : e.status === 'waiting_input' ? 'Waiting for input' : e.status === 'waiting_dependency' ? 'Waiting on dependency' : '') : opWords[e.op] || e.op;
  return (word ? word + (e.text ? ': ' : '') : '') + (e.text || (e.op === 'progress' ? names[e.status] || 'Report' : ''));
}
function renderFeed() {
  for (const b of document.querySelectorAll('#feed-filters .chip')) b.setAttribute('aria-pressed', String(b.dataset.feed === feedFilter));
  if (!feedEvents) { if (feedError) $('feed').replaceChildren(el('p', 'error', feedError)); return; }
  const known = new Set(state?.tasks.map(t => t.id) || []);
  preserve($('feed'), () => {
    const parts = []; if (feedError) parts.push(el('p', 'error', feedError));
    const list = feedEvents.filter(feedFilters[feedFilter]);
    let day = '', prev = null, marked = false;
    for (const [i, e] of list.entries()) {
      if (!marked && feedMark && i > 0 && e.at <= feedMark) { parts.push(el('div', 'feed-mark', 'Above: new reports since you last opened "Feed"')); marked = true; prev = null; }
      if (e.at <= (feedMark || '')) marked = true;
      const d = zoneDay(e.at); if (d !== day) { parts.push(el('h2', 'feed-day', d)); day = d; prev = null; }
      const grouped = prev && prev.task_id === e.task_id, row = el('div', 'ev' + (grouped ? ' grouped' : ''));
      row.dataset.taskId = e.task_id; row.dataset.op = e.op;
      const dot = ['ask', 'resolve', 'withdraw', 'user_close', 'claim', 'delivery'].includes(e.op) ? e.op : e.status || 'unknown';
      const body = el('div', 'ev-body');
      if (!grouped) {
        const head = el('div', 'ev-head');
        if (known.has(e.task_id)) { const b = button(e.title, () => openTask(e.task_id, b), 'link'); b.dataset.focus = 'feed-' + i; head.append(b); } else head.append(el('b', '', e.title));
        head.append(el('span', 'ev-who', [e.agent, e.role === 'audit' ? 'Audit' : '', e.workstream].filter(Boolean).join(' · '))); body.append(head);
      }
      body.append(el('div', 'ev-text', feedLine(e)));
      const t = el('span', 'ev-time', zoneTime(e.at)); t.title = fullTime(e.at);
      row.append(t, el('span', 'dot ' + dot), body); parts.push(row); prev = e;
    }
    if (!list.length) parts.push(el('p', 'empty', 'No matching reports.'));
    else parts.push(el('p', 'subtle view-foot', 'Shows at most the latest ' + FEED_LIMIT + ' reports (excluding run registrations and document snapshots); times are in ' + displayZone() + '.'));
    $('feed').replaceChildren(...parts);
  });
  if (feedEvents.length) remembered('agent-board-feed-seen', feedEvents[0].at);
}

function renderViews() {
  if (!state) return;
  renderBrief(); renderAgents();
  if (currentView === 'feed' && feedEvents) renderFeed();
}
function onBoardRefresh() { if (currentView === 'feed') loadFeed(); }
for (const b of document.querySelectorAll('.view-tab')) b.addEventListener('click', () => showView(b.dataset.view));
for (const b of document.querySelectorAll('#feed-filters .chip')) b.addEventListener('click', () => { feedFilter = b.dataset.feed; renderFeed(); });
// Sticky parts sit below the header, whose height changes when it wraps.
new ResizeObserver(() => document.documentElement.style.setProperty('--header-h', document.querySelector('.app-header').offsetHeight + 'px')).observe(document.querySelector('.app-header'));
showView(initialView(), false);
