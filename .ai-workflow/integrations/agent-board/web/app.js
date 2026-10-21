'use strict';
const $ = id => document.getElementById(id);
const names = {running:'Running', waiting_input:'Waiting for input', waiting_dependency:'Waiting on dependency', paused:'Paused', ended:'Ended', unknown:'Unknown'};
const kinds = {agent_report:'Agent self-report · identity not verified', document_snapshot:'Document snapshot · not the current run', program_observation:'Program observation · not a service heartbeat', user_web:'Your action on the board page'};
const stages = {implementation:'Implementation', self_test:'Author self-test', independent_audit:'Independent audit record (not verified automatically)', deployment:'Deployment record', post_deploy:'Post-deploy verification'};
const fields = {status:'Run status', phase:'Phase', progress:'Progress', next_step:'Next step', blocker:'Blocker', decision:'Decision / request', human_input:'Needs your action (self-reported)', unanswered_policy:'If unanswered', dependencies:'Dependencies', candidate_sha:'Candidate SHA', audit_id:'audit_id', worktree:'Worktree', evidence:'Evidence', count:'Actual count'};
const humanNames = {required:'Needs your action', not_required:'No action needed from you', unknown:'Not declared'};
let state = null, selected = null, statePrint = '', detailPrint = '', detailGeneration = 0, refreshGeneration = 0, instance = null, lastTrigger = null;
let readingPosition = 0;
let detailValue = null;
function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
function str(v) { return v == null || v === '' ? 'Unknown' : typeof v === 'object' ? JSON.stringify(v) : String(v); }
// Browser-local time zone; the zone name is always printed next to absolute times.
// The board's configured display zone (display_time_zone); an unknown name falls back to UTC.
function displayZone() { const zone = state?.timezone || 'UTC'; try { new Intl.DateTimeFormat('en-US', {timeZone: zone}); return zone; } catch { return 'UTC'; } }
function time(value) { if (!value) return 'Unknown'; const d = new Date(value); return Number.isNaN(d.getTime()) ? 'Unknown' : new Intl.DateTimeFormat('en-US', {timeZone: displayZone(), month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23', timeZoneName:'short'}).format(d); }
function fullTime(value) { if (!value) return 'Unknown'; const d = new Date(value); return Number.isNaN(d.getTime()) ? 'Unknown' : new Intl.DateTimeFormat('en-US', {timeZone: displayZone(), year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23', timeZoneName:'short'}).format(d); }
function span(value) { const ms = Date.now() - Date.parse(value); if (!Number.isFinite(ms)) return null; const m = Math.max(0, Math.round(ms / 60000)); return m < 60 ? m + ' min' : m < 2880 ? Math.round(m / 60) + ' h' : Math.round(m / 1440) + ' d'; }
function ago(value) { const s = value ? span(value) : null; return s === null ? 'time unknown' : s === '0 min' ? 'just now' : s + ' ago'; }
function timeTag(value, prefix = '') { const n = el('span', 'time', prefix + ago(value)); n.title = fullTime(value); return n; }
function button(text, fn, cls = 'task-name') { const b = el('button', cls, text); b.type = 'button'; b.addEventListener('click', fn); return b; }
function badge(status, role, run = null) {
  // A stale "running" says how long it has been silent instead of a separate notice.
  const silent = run?.stale && run.business_at && status !== 'ended' ? ' · stale, no update for ' + span(run.business_at) : '';
  return el('span', 'badge ' + (Object.hasOwn(names, status) ? status : 'unknown') + (silent ? ' silent' : ''), (role === 'audit' ? 'Audit · ' : 'Execution · ') + (status === 'running' && role === 'audit' ? 'In progress' : names[status] || 'Unknown') + silent);
}
function kv(parent, label, value) { const row = el('dl', 'kv'); row.append(el('dt', '', label), el('dd', '', str(value))); parent.append(row); }
function kvIf(parent, label, value) { if (value != null && value !== '' && !(Array.isArray(value) && !value.length)) kv(parent, label, value); }
function evidence(parent, paths) {
  for (const [i,p] of (paths || []).entries()) {
    const line = el('div', 'path'), code = el('code', '', p);
    const copy = button('Copy', async () => {
      try { await navigator.clipboard.writeText(p); feedback.textContent = 'Copied'; }
      catch { feedback.textContent = 'Copy failed; select the path and copy it by hand'; }
    }, 'copy-path');
    copy.setAttribute('aria-label','Copy path ' + p);
    copy.dataset.focus = 'copy-' + p + '-' + i;
    const feedback = el('span','copy-status'); feedback.setAttribute('role','status');
    line.append(code,copy); parent.append(line,feedback);
  }
}
function disclosure(label, key, children) { const d = el('details'); d.dataset.key = key; const summary = el('summary', '', label); summary.dataset.focus = key; d.append(summary, ...children); return d; }
function overviewRuns(t) { return t.overview_runs || (t.current ? [t.current] : []); }
function auditRuns(t) { return overviewRuns(t).filter(r => r.role === 'audit'); }
function parallelRuns(t) { return t.parallel_runs || []; }
// Open questions: asks not closed by question_id, plus human_input declarations.
// Handoff, ending and TTL close nothing; the server lists every one, never capped.
function openQuestions(t) { return t.open_questions || []; }
const QUIET_MS = 48 * 3600 * 1000;
// Like the display TTL, an unknown clock never counts as quiet.
function quietSince(value) { const at = value ? Date.parse(value) : NaN; return Number.isFinite(at) && Date.now() - at > QUIET_MS; }
// Folded, never closed: the asking run ended, handed off, or went quiet for 48 h.
function maybeStale(q) { return !!(q.run_ended || q.former_owner || quietSince(q.run_business_at || q.asked_at)); }
// Old-style "needs you" flags without any text: a tag on the task, not an inbox row.
function untold(q) { return q.kind === 'declaration' && !q.question; }
function qKey(t, q) { return t.id + '|' + (q.question_id || 'run:' + q.run_id); }
function firstLine(text) { return String(text || '').split('\n').find(l => l.trim()) || ''; }
const pageToken = document.querySelector('meta[name="board-page-token"]')?.content || '';
const inboxOpen = new Set();
let confirming = null, confirmTimer = null;
// The page's one write: the user marks a request handled. It never answers or approves.
function closeButton(t, q, label = 'Handled') {
  const key = qKey(t, q), b = el('button', 'close-q' + (confirming === key ? ' confirming' : ''), confirming === key ? 'Confirm close?' : label);
  b.type = 'button'; b.dataset.focus = 'close-' + key; b.title = 'Mark as handled by you; this is not an answer or an approval, and the original text stays in the task detail';
  b.addEventListener('click', async () => {
    if (confirming !== key) {
      confirming = key; clearTimeout(confirmTimer);
      confirmTimer = setTimeout(() => { confirming = null; statePrint = ''; detailPrint = ''; refresh(); }, 6000);
      b.textContent = 'Confirm close?'; b.classList.add('confirming'); return;
    }
    confirming = null; clearTimeout(confirmTimer); b.disabled = true; b.textContent = 'Closing…';
    try {
      const body = {task_id:t.id}; if (q.question_id) body.question_id = q.question_id; else body.target_run = q.run_id;
      const r = await fetch('/api/user-close', {method:'POST', cache:'no-store', headers:{'Content-Type':'application/json', 'X-Board-Page-Token':pageToken, 'X-Agent-Board-Instance':instance || ''}, body:JSON.stringify(body), signal:AbortSignal.timeout(4500)});
      if (!r.ok) throw new Error(r.status === 403 ? 'The page has expired; reload the whole page and try again' : 'HTTP ' + r.status);
      statePrint = ''; detailPrint = ''; await refresh();
    } catch (e) { b.disabled = false; b.textContent = label; $('error').hidden = false; $('error').textContent = 'Close not completed: ' + e.message; }
  });
  return b;
}
// Quiet = stale or never reported; an open question keeps its audit visible.
function liveAudits(t) { return auditRuns(t).filter(r => (r.business_at && !r.stale) || r.human_input_required); }
function quietAudits(t) { return auditRuns(t).filter(r => !liveAudits(t).includes(r)); }
function outboxFor(t) { return state?.outbox?.by_task?.[t.id] || null; }
function isEnded(t) { return t.state.status === 'ended' && !openQuestions(t).length && !liveAudits(t).length && !parallelRuns(t).some(r => !r.stale) && !outboxFor(t); }
// Decisions an agent wrote down without declaring that the user must act.
function openDecisions(t) { return [...overviewRuns(t), ...parallelRuns(t).filter(r => !r.former_owner)].filter(r => r.state.decision && !r.human_input_required && r.state.status !== 'ended' && !r.stale); }
function roleName(r) { return r.role === 'audit' ? 'Audit' : r.role === 'execution' ? 'Execution' : 'Parallel execution'; }
function askerName(q, t) {
  const role = q.role === 'audit' ? 'Audit' : q.run_id === t.current_run ? 'Execution' : 'Parallel execution';
  return role + ' ' + (q.agent ? q.agent + ' / ' : '') + q.run_id + (q.former_owner ? ' (source handed off)' : '') + (q.run_ended ? ' (run ended, not withdrawn)' : '');
}
function questionReason(q, t) { return askerName(q, t) + ' · ' + (q.question || 'Needs your action; the request is not stated yet'); }
function questionMeta(q) {
  return [q.question_id ? 'Question ' + q.question_id.slice(0, 8) : 'Declaration, no question ID',
    q.item ? 'Linked ' + itemName(q.item) + (q.link_state === 'unknown_item' ? ' (not in the plan)' : '') : '',
    q.options?.length ? 'Options ' + q.options.join(' / ') : '',
    q.asked_at ? 'Waiting ' + (span(q.asked_at) || 'time unknown') : 'Asked at unknown time'].filter(Boolean).join(' · ');
}
function questionStale(q) { return q.run_stale ? 'The requesting run is stale (' + (q.run_business_at ? 'no update for ' + span(q.run_business_at) : 'never reported') + '); the board never closes it automatically; click "Handled" when done' : ''; }
function nextSource(n) { return n.source === 'plan_focus' ? 'Plan focus ' + itemName(n.item) : n.source === 'current_run' ? 'Current execution run self-report' + (n.run_id ? ' · ' + n.run_id : '') : 'Not specified'; }
function reasons(t) {
  // One line per real issue; per-run staleness of audits is summarized once.
  const a = [];
  for (const r of overviewRuns(t)) {
    if (r.role === 'audit' && (r.stale || !r.business_at)) continue;
    const prefix = roleName(r) + ' · ';
    if (r.state.decision && !r.human_input_required) a.push(prefix + 'Decision pending: ' + r.state.decision);
    if (r.state.blocker) a.push(prefix + 'Blocked: ' + r.state.blocker);
    if (r.state.status === 'waiting_input' && !r.state.decision && !r.state.blocker) a.push(prefix + 'Waiting for input; the item is not stated');
    if (r.state.human_input === 'not_required' && r.state.decision) a.push(prefix + 'Declared no action needed from you, but a decision is still pending; the owning agent checks it');
    else if ((!r.state.human_input || r.state.human_input === 'unknown') && (r.state.status === 'waiting_input' || r.state.decision)) a.push(prefix + 'Who must give input is not declared');
    if (r.role === 'execution' && r.stale && !r.business_at) a.push(prefix + 'Activity unknown / not connected yet');
  }
  if (!t.current && !auditRuns(t).length) a.push('Current execution activity unknown / not connected yet, and no open audit');
  if (t.conflicts.length) a.push('Sources disagree; to be checked');
  if (t.snapshots_truncated) a.push(t.snapshot_source_count + ' document sources; only 200 shown');
  if (t.time_warnings) a.push(t.time_warnings + ' source times abnormal; not used to choose the state');
  return a;
}
function options(id, values) {
  const n = $(id), unique = [...new Set(values.filter(Boolean))].sort();
  if (JSON.stringify([...n.options].slice(1).map(o => o.value)) === JSON.stringify(unique)) return;
  const old = n.value, first = n.firstElementChild; n.replaceChildren(first);
  for (const value of unique) n.append(new Option(value, value));
  n.value = [...n.options].some(o => o.value === old) ? old : '';
}
function preserve(root, build) {
  const open = new Set([...root.querySelectorAll('details[open][data-key]')].map(n => n.dataset.key));
  const focus = root.contains(document.activeElement) ? document.activeElement.dataset.focus : null;
  const scroll = root.scrollTop; build();
  for (const n of root.querySelectorAll('details[data-key]')) n.open = open.has(n.dataset.key);
  if (focus) [...root.querySelectorAll('[data-focus]')].find(n => n.dataset.focus === focus)?.focus({preventScroll:true});
  root.scrollTop = scroll;
}
function attentionFlags(t) {
  const runs = overviewRuns(t);
  return {
    human:openQuestions(t).length > 0,
    dependency:runs.some(r => r.state.blocker || r.state.status === 'waiting_dependency'),
    verify:!!(runs.some(r => r.stale) || (!t.current && !auditRuns(t).length) || t.conflicts.length || t.time_warnings || t.snapshots_truncated || outboxFor(t))
  };
}
function isOld(t){return t.display_expired===true&&!openQuestions(t).length;}
function updateClock(t){
  // Same valid business/source clock as display TTL, never receipt/refresh time.
  // A record without display_updated_at falls back to execution time.
  const value=Object.hasOwn(t,'display_updated_at')?t.display_updated_at:t.business_at;
  const millis=value?Date.parse(value):NaN;
  return Number.isFinite(millis)?millis:-Infinity;
}
function stableOrder(a,b){return a<b?-1:a>b?1:0;}
function newestFirst(a,b){
  const left=updateClock(a),right=updateClock(b);
  return left===right?stableOrder(a.id,b.id):right-left;
}
function orderedStreams(tasks,extra=[]){
  const latest=new Map();
  for(const t of tasks)latest.set(t.workstream,Math.max(latest.get(t.workstream)??-Infinity,updateClock(t)));
  for(const name of extra)if(!latest.has(name))latest.set(name,-Infinity);
  return [...latest.keys()].sort((a,b)=>{
    const left=latest.get(a),right=latest.get(b);
    return left===right?stableOrder(a,b):right-left;
  });
}
function listedTasks(){return state.tasks.filter(t=>$('show-old').checked||!isOld(t)).sort(newestFirst);}
function scopedTasks(){return listedTasks().filter(t=>!$('workstream').value||t.workstream===$('workstream').value);}
function chooseStream(stream){
  const fromDeep=window.scrollY>100;
  $('workstream').value=stream; renderNavigation(); renderTasks();
  if(fromDeep)requestAnimationFrame(()=>{
    const headingTop=$('tasks-title').getBoundingClientRect().top+window.scrollY-12;
    window.scrollTo(0,Math.max(0,headingTop));
  });
}
function renderNavigation(){
  const listed=listedTasks(),selectedStream=$('workstream').value;
  const names=orderedStreams(listed,selectedStream?[selectedStream]:[]);
  preserve($('stream-nav'),()=>{
    $('stream-nav').replaceChildren(...['',...names].map(name=>{
      const list=listed.filter(t=>!name||t.workstream===name);
      const requests=list.reduce((n,t)=>n+openQuestions(t).filter(q=>!untold(q)).length,0);
      const b=button('',()=>chooseStream(name),'stream-link');
      b.dataset.stream=name; b.dataset.focus='stream-'+name; b.setAttribute('aria-current',String($('workstream').value===name));
      const top=el('span','nav-top');top.append(el('span','',name||'All workstreams'));if(requests)top.append(el('span','nav-meta','Needs you '+requests));
      b.append(top);return b;
    }));
  });
}
function outboxBanner() {
  // Queued means unconfirmed: possibly recorded with a lost receipt, or blocked.
  const o = state.outbox, n = $('outbox-banner');
  if (!o || !(o.pending || o.task_board_pending || o.unreadable)) { n.hidden = true; n.replaceChildren(); return; }
  const tasks = Object.keys(o.by_task || {}).length, queued = o.pending + o.task_board_pending, parts = [];
  if (queued) parts.push(el('strong', '', queued + (queued === 1 ? ' report' : ' reports') + ' not yet confirmed as delivered to the board'),
    el('span', '', (o.pending ? ': progress ' + o.pending + ' (' + tasks + ' tasks)' : '') + (o.task_board_pending ? ', task board ' + o.task_board_pending : '') + '. The affected tasks may not show their latest state.'));
  if (o.unreadable) parts.push(el('strong', '', (queued ? 'Also ' : '') + o.unreadable + (o.unreadable === 1 ? ' queued file' : ' queued files') + ' unreadable'), el('span', '', '; content unknown, not counted.'));
  parts.push(el('span', '', ' Check with board.py pending.'));
  n.replaceChildren(...parts); n.hidden = false;
}
function inboxRow(t, q) {
  const key = qKey(t, q), row = el('article', 'inbox-row'); row.dataset.taskId = t.id; row.dataset.runId = q.run_id; row.dataset.questionId = q.question_id || '';
  const line = el('div', 'inbox-line'), task = button(t.title, () => openTask(t.id, task), 'inbox-task'); task.dataset.focus = 'inbox-task-' + key; task.title = t.workstream + ' · ' + t.title;
  const text = el('span', 'inbox-q', firstLine(q.question)); text.title = q.question;
  const more = button(inboxOpen.has(key) ? 'Collapse' : 'Expand', () => { inboxOpen.has(key) ? inboxOpen.delete(key) : inboxOpen.add(key); renderOverview(); }, 'inbox-more');
  more.dataset.focus = 'inbox-more-' + key; more.setAttribute('aria-expanded', String(inboxOpen.has(key)));
  line.append(task, text, el('span', 'inbox-wait', q.asked_at ? 'waited ' + (span(q.asked_at) || '?') : 'time unknown'), closeButton(t, q), more); row.append(line);
  if (inboxOpen.has(key)) {
    const box = el('div', 'inbox-detail'); box.append(el('p', 'inbox-full', q.question));
    if (q.options?.length) box.append(el('p', 'subtle', 'Options · ' + q.options.join(' / ')));
    if (q.unanswered_policy) box.append(el('p', 'subtle', 'If unanswered · ' + q.unanswered_policy));
    box.append(el('p', 'subtle', t.workstream + ' · ' + askerName(q, t) + ' · ' + questionMeta(q)));
    if (maybeStale(q)) box.append(el('p', 'subtle', 'The asking run ' + (q.run_ended ? 'has ended' : q.former_owner ? 'was handed off' : (q.run_business_at ? 'has had no update for ' + span(q.run_business_at) : 'never reported')) + '; the board never closes it automatically; click "Handled" when done.'));
    row.append(box);
  }
  return row;
}
// Every open question with text, newest first; shared by the list and the brief.
function inboxItems(){
  return state.tasks.flatMap(t=>openQuestions(t).filter(q=>!untold(q)).map(q=>({t,q})))
    .sort((a,b)=>stableOrder(b.q.asked_at||'',a.q.asked_at||'')||stableOrder(qKey(a.t,a.q),qKey(b.t,b.q)));
}
function renderOverview() {
  options('workstream', state.tasks.map(t => t.workstream));
  options('agent', state.tasks.flatMap(t => [...overviewRuns(t), ...parallelRuns(t)].map(r => r.agent)));
  renderNavigation(); outboxBanner();
  const oldCount=state.tasks.filter(isOld).length;
  $('old-count').textContent=oldCount?'('+oldCount+')':'';
  const inbox=inboxItems();
  const fresh=inbox.filter(x=>!maybeStale(x.q)),stale=inbox.filter(x=>maybeStale(x.q));
  const undeclared=state.tasks.filter(t=>!isOld(t)&&openDecisions(t).length).sort(newestFirst);
  $('attention-count').textContent=inbox.length?String(inbox.length):'None';
  preserve($('attention'),()=>{
    const parts=[];
    if(fresh.length){const list=el('div','inbox');list.append(...fresh.map(x=>inboxRow(x.t,x.q)));parts.push(list);}
    else parts.push(el('p','subtle inbox-empty',stale.length?'Nothing new needs you.':'Nothing needs you.'));
    if(stale.length){const list=el('div','inbox');list.append(...stale.map(x=>inboxRow(x.t,x.q)));const d=disclosure('Possibly outdated · '+stale.length+' (the asking run ended, was handed off or was quiet for 48 h; never closed automatically)','stale-inbox',[list]);d.className='stale-inbox';parts.push(d);}
    $('attention').replaceChildren(...parts);
    $('attention').hidden=false;
  });
  preserve($('undeclared'),()=>{
    $('undeclared').replaceChildren();
    if(undeclared.length){
      const list=el('div','attention-items');
      for(const t of undeclared){
        const n=el('article','attention-item secondary');n.dataset.taskId=t.id;
        const b=button(t.title,()=>openTask(t.id,b),'attention-name');b.dataset.focus='attention-decision-'+t.id;
        n.append(el('div','subtle',t.workstream),b);
        for(const r of openDecisions(t))n.append(el('p','subtle',roleName(r)+' · '+r.state.decision));
        list.append(n);
      }
      $('undeclared').append(disclosure('Other pending decisions · '+undeclared.length+' (the agent did not declare it needs you; for review)','undeclared-decisions',[list]));
    }
    $('undeclared').hidden=!undeclared.length;
  });
  renderTasks();
  if(typeof renderViews==='function')renderViews();
}
function taskEntry(t) {
  // One line per task; everything else lives in the detail.
  const c=el('article','task-entry'+(t.id===selected?' selected':''));c.dataset.taskId=t.id;
  const r=overviewRuns(t).find(r=>r.role==='execution')||t.current,s=t.state,step=t.next_step||{};
  const b=button(t.title,()=>openTask(t.id,b));b.dataset.focus='task-'+t.id;
  const status=s.status||'unknown',silent=!!(r?.stale&&status!=='ended');
  const badgeNode=el('span','badge '+(Object.hasOwn(names,status)?status:'unknown')+(silent?' silent':''),(names[status]||'Unknown')+(silent?' · stale':''));
  if(silent&&r.business_at)badgeNode.title='no update for '+span(r.business_at);
  const phaseText=s.phase||step.text||'No phase reported yet';
  const phase=el('span','phase',phaseText);phase.title=phaseText+(step.text&&s.phase?'\nNext step · '+step.text:'');
  const tags=el('span','task-tags'),asks=openQuestions(t).filter(q=>!untold(q)).length;
  if(asks)tags.append(el('span','tag ask-tag','Needs you '+asks));
  for(const q of openQuestions(t).filter(untold)){const g=el('span','tag declaration-tag');g.dataset.runId=q.run_id;g.title='This run declared it needs your action but did not state the request ('+(q.agent||q.run_id)+')';g.append(el('span','','Needs you (unstated)'),closeButton(t,q,'×'));tags.append(g);}
  const queued=outboxFor(t);if(queued)tags.append(el('span','tag outbox-tag',queued.count+' undelivered'));
  if(isOld(t))tags.append(el('span','old-label','Old task'));
  if(t.task_board){const open=button('Plan',()=>openPlan(t.id,open),'plan-open');open.dataset.focus='plan-open-'+t.id;open.title=planOverviewText(t.task_board.overview)+(t.unassigned_run_count?' · unassigned runs '+t.unassigned_run_count:'');tags.append(open);}
  const latest=Object.hasOwn(t,'display_updated_at')?t.display_updated_at:t.business_at;
  c.append(b,badgeNode,phase,tags,timeTag(latest));
  return c;
}
function renderTasks(){
  if(!state)return;
  const status=$('status').value,agent=$('agent').value,search=$('search').value.trim().toLowerCase(),cat=$('attention-filter').value;
  const scoped=scopedTasks();
  const tasks=scoped.filter(t=>{
    // Status, agent and request must hold for one run; parallel runs count too,
    // the same set that feeds the agent options and the request count.
    const runs=[...overviewRuns(t),...parallelRuns(t)];if(!t.current)runs.unshift({state:{status:'unknown'},agent:null});
    const match=runs.some(r=>(!status||r.state.status===status)&&(!agent||r.agent===agent)&&
      (cat==='human'?!!r.human_input_required:cat==='dependency'?!!(r.state.blocker||r.state.status==='waiting_dependency'):true));
    return match&&[t.title,t.id].join(' ').toLowerCase().includes(search)&&(!cat||attentionFlags(t)[cat]);
  });
  $('tasks-title').textContent=$('workstream').value||'All workstreams';
  $('visible-count').textContent=tasks.length+' / '+scoped.length+' tasks (all: '+listedTasks().length+')';
  $('stream-summary').replaceChildren();$('stream-summary').hidden=true;
  preserve($('tasks'),()=>{
    $('tasks').replaceChildren();const groups=new Map();
    for(const t of tasks){if(!groups.has(t.workstream))groups.set(t.workstream,[]);groups.get(t.workstream).push(t);}
    // Keep block order consistent with the global sidebar even when a local
    // filter hides the newest task in one block.
    for(const name of orderedStreams(listedTasks()).filter(name=>groups.has(name))){
      const list=groups.get(name),group=el('section','task-group');group.dataset.stream=name;
      if(!$('workstream').value){const head=el('header','group-head');head.append(el('h3','',name),el('span','subtle',list.length+' tasks'));group.append(head);}
      // Ended or quiet for 48 h folds away unless it still waits on you.
      const fold=!status&&!search&&!cat,shown=t=>t.id===selected||openQuestions(t).length||(!isEnded(t)&&!(Date.now()-updateClock(t)>QUIET_MS&&Number.isFinite(updateClock(t))));
      const open=fold?list.filter(shown):list,ended=list.filter(t=>!open.includes(t));
      const rows=el('div','task-list');rows.append(...open.map(taskEntry));group.append(rows);
      if(ended.length){const d=disclosure(ended.length+' more (ended or no update for 48 h)','ended-'+name,[el('div','task-list',undefined)]);d.className='ended-group';d.lastChild.append(...ended.map(taskEntry));group.append(d);}
      $('tasks').append(group);
    }
    if(!tasks.length)$('tasks').append(el('p','empty',!state.tasks.length?'No tasks registered yet. Unknown is neither failure nor completion.':!listedTasks().length?'No recent tasks; turn on "Show old tasks" to see them.':'No task matches the current scope. Adjust the local filters or choose another workstream.'));
  });
  $('tasks').setAttribute('aria-busy','false');
}
function panelMode() {
  if (!selected) return;
  const modal = window.matchMedia('(max-width: 1023px)').matches;
  $('detail').setAttribute('role', modal ? 'dialog' : 'region');
  if (modal) $('detail').setAttribute('aria-modal', 'true'); else $('detail').removeAttribute('aria-modal');
  $('workspace').inert = modal;
}
function openTask(id, trigger = null, focus = true) {
  readingPosition = selected ? readingPosition : window.scrollY;
  ++detailGeneration; selected = id; detailPrint = ''; lastTrigger = trigger || lastTrigger;
  location.hash = encodeURIComponent(id); $('detail').hidden = false; document.body.classList.add('has-detail');
  const t = state?.tasks.find(n => n.id === id); detailValue = null; document.body.classList.toggle('has-plan', !!t?.task_board); $('detail-title').textContent = t?.title || 'Task detail'; $('detail-id').textContent = id;
  $('detail-body').replaceChildren(el('p', 'empty', 'Reading task, sources and audit targets…')); panelMode();
  if (focus) $('close-detail').focus({preventScroll:true});
  renderTasks(); loadDetail();
}
// The plan lives in the task detail; open it there, scrolled to the plan.
function openPlan(id, trigger = null) { planScroll = id; openTask(id, trigger); }
function closeDetail() {
  ++detailGeneration; selected = null; detailPrint = ''; detailValue = null; planScroll = null; location.hash = ''; $('detail').hidden = true;
  document.body.classList.remove('has-detail', 'has-plan'); $('workspace').inert = false; renderTasks();
  const id = lastTrigger?.dataset.focus;
  const target = id && [...document.querySelectorAll('[data-focus]')].find(n => n.dataset.focus === id);
  (target || $('search')).focus({preventScroll:true});
  window.scrollTo(0,readingPosition);
}
function section(root, title) { const n = el('section', 'detail-section'); n.append(el('h3', '', title)); root.append(n); return n; }
function runDetail(root, r, stateValue, key) {
  const n = el('div', 'source'); n.append(badge(stateValue.status, r?.role, r), el('p', 'subtle', r ? [r.id, r.agent, r.client].filter(Boolean).join(' / ') : 'No execution run has claimed the task'));
  for (const k of ['phase', 'progress', 'next_step', 'blocker', 'decision', 'unanswered_policy', 'candidate_sha', 'audit_id', 'worktree']) kvIf(n, k === 'next_step' ? 'Next step (self-reported by this run)' : fields[k], stateValue[k]);
  if (stateValue.item) kv(n, r?.role === 'audit' ? 'Audit target (linked)' : 'Linked work item', itemName(stateValue.item));
  kv(n, fields.human_input, humanNames[stateValue.human_input] || 'Not declared');
  if (stateValue.count) kv(n, 'Actual count', stateValue.count.done + ' / ' + stateValue.count.total + ' ' + stateValue.count.unit);
  evidence(n, stateValue.evidence); kv(n, 'Last report', r?.business_at ? fullTime(r.business_at) + ' (' + ago(r.business_at) + ')' : 'Never reported');
  if (r?.role === 'audit' && r.candidate_scope) kv(n, 'Target applicability', ({current:'Matches the current execution candidate', other:'Not the current execution candidate', unknown:'Audit target binding unknown'})[r.candidate_scope] || 'Audit target binding unknown');
  root.append(n); return n;
}
function questionBlock(q, t) {
  const n = el('div', 'source question-block'); n.dataset.runId = q.run_id; n.dataset.questionId = q.question_id || '';
  n.append(el('strong', '', questionReason(q, t)), el('p', 'subtle', questionMeta(q)));
  kvIf(n, 'If unanswered', q.unanswered_policy); if (q.run_stale) n.append(el('p', 'subtle', questionStale(q)));
  n.append(closeButton(t, q));
  return n;
}
const closureWhy = {unknown_question:'This task has no such question ID', other_task_run:'target_run is not a run of this task', no_open_declaration:'That run had no open declaration at the time'};
function closureText(c) { return [c.op === 'user_close' ? 'Closed by you on the board page' : (c.op === 'resolve' ? 'Resolved' : 'Withdrawn') + ' · by ' + c.run_id, c.question_id ? 'Question ' + c.question_id.slice(0, 8) : 'Target run ' + c.target_run, fullTime(c.at), c.text].join(' · '); }
function closedBlock(q, t) {
  const n = el('div', 'source'); n.append(el('strong', '', questionReason(q, t)), el('p', 'subtle', closureText(q.closed_by)));
  if (q.duplicates.length) n.append(el('p', 'subtle', 'Closed again ' + q.duplicates.length + ' times (the earliest close applies)'));
  return n;
}
// One run under a plan item or among unassigned runs; expands to its own report.
function runFold(r, t, prefix) {
  const full = t.runs?.find(x => x.id === r.id), box = el('div');
  const role = r.role === 'audit' ? 'Audit' : r.current ? 'Execution (current owner)' : 'Parallel execution';
  const tags = [r.item?.branch ? 'Branch ' + itemName(r.item) : '', r.open_request ? 'Has an open request' : '',
    r.link_state === 'unknown_item' ? 'Invalid link: the plan has no ' + itemName(r.item) : '', r.link_state === 'finished_item' ? 'The linked work item has finished' : ''];
  const label = [role, [r.agent, r.client].filter(Boolean).join(' / ') || 'Label unknown', r.id, names[r.status] || 'Unknown', r.phase, r.business_at ? ago(r.business_at) : 'Never reported', ...tags].filter(Boolean).join(' · ');
  if (full) runDetail(box, full, full.state, r.id); else box.append(el('p', 'subtle', 'This run\'s full report is not part of this read.'));
  const d = disclosure(label, prefix + r.id, [box]); d.className = 'run-fold'; d.dataset.runId = r.id; return d;
}
function renderDetail(t) {
  $('detail-title').textContent = t.title; $('detail-id').textContent = t.id + ' · ' + t.workstream;
  detailValue = t; document.body.classList.toggle('has-plan', !!t.task_board);
  const root = $('detail-body'); preserve(root, () => {
    root.replaceChildren();
    const step = t.next_step || {}, head = el('div', 'detail-next'), line = el('p', 'next');
    line.append(el('span', '', 'Next step · '), el('span', 'next-value', step.text || 'Not specified'));
    head.append(line, el('p', 'next-source', 'Source · ' + nextSource(step) + (step.text && step.at ? ' · ' + fullTime(step.at) : ''))); root.append(head);
    const queued = outboxFor(t);
    if (queued) root.append(el('p', 'outbox-note', queued.count + ' reports not yet confirmed as delivered (oldest ' + fullTime(queued.oldest_at) + '); what follows may not be the latest state'));
    const notes = reasons(t); if (notes.length) { const n = section(root, 'Notes'); for (const r of notes) n.append(el('p', 'notice info-notice', r)); }
    root.append(el('p', 'legend', 'Everything below is an agent self-report or a document / program observation; labels are not identity credentials. The board only displays: it never approves, executes or infers completion.'));
    const qs = t.questions || {open:openQuestions(t), closed:[], closed_count:0, unmatched:[], unmatched_count:0};
    const asks = section(root, 'Needs you · ' + qs.open.length); asks.dataset.section = 'questions';
    asks.append(...qs.open.map(q => questionBlock(q, t)));
    if (!qs.open.length) asks.append(el('p', 'subtle', 'No open questions (needs an agent never raised cannot be detected).'));
    if (qs.closed_count) asks.append(disclosure('Closed · ' + qs.closed_count + (qs.closed_count > qs.closed.length ? ' (latest ' + qs.closed.length + ' shown)' : ''), 'closed-questions', qs.closed.map(q => closedBlock(q, t))));
    if (qs.unmatched_count) asks.append(disclosure('Unmatched resolves / withdrawals · ' + qs.unmatched_count, 'unmatched-closures', qs.unmatched.map(c => el('p', 'subtle', closureText(c) + ' · ' + (closureWhy[c.why] || c.why)))));
    if (t.task_board) { drawPlan(t); root.append(planBox); }
    else {
      const execution = section(root, 'Execution'); kv(execution, 'Claim version', t.version); runDetail(execution, overviewRuns(t).find(r => r.role === 'execution') || t.current, t.state, 'execution');
      if (t.parallel_run_count) {
        const parallel = section(root, 'Parallel execution runs · ' + t.parallel_run_count + ' (do not change the task state)');
        for (const r of parallelRuns(t)) runDetail(parallel, r, r.state, r.id);
        if (t.parallel_run_count > parallelRuns(t).length) parallel.append(el('p', 'subtle', 'Only the latest ' + parallelRuns(t).length + ' shown; see the run list below for all.'));
      }
    }
    if (t.unassigned_runs) {
      const u = section(root, 'Unassigned runs · ' + t.unassigned_runs.length), fresh = t.unassigned_runs.filter(r => !r.stale), quiet = t.unassigned_runs.filter(r => r.stale);
      u.dataset.section = 'unassigned';
      u.append(el('p', 'subtle', 'Open runs with no declared link, or linked to a work item that is not in the plan; ownership is never guessed and plan progress is unaffected.'));
      if (fresh.length) u.append(disclosure('Recently active · ' + fresh.length, 'unassigned-fresh', fresh.map(r => runFold(r, t, 'unassigned-'))));
      if (quiet.length) u.append(disclosure('Stale or never reported · ' + quiet.length, 'unassigned-stale', quiet.map(r => runFold(r, t, 'unassigned-'))));
      if (!t.unassigned_runs.length) u.append(el('p', 'subtle', 'None.'));
    }
    const audits = section(root, 'Audit');
    for (const r of liveAudits(t)) runDetail(audits, r, r.state, r.id);
    const quiet = quietAudits(t);
    if (quiet.length) { const box = el('div'); for (const r of quiet) runDetail(box, r, r.state, r.id); audits.append(disclosure('Stale or never reported · ' + quiet.length, 'quiet-audits', [box])); }
    if (!auditRuns(t).length) audits.append(el('p', 'subtle', 'No open audit (an audit pass is never inferred).'));
    if (t.activity) { const a = t.activity, activity = section(root, 'Program observation'); activity.append(el('p', '', a.data.summary)); evidence(activity, [a.source.path]); kv(activity, 'Observed at', fullTime(a.source.source_at)); kv(activity, 'Read at', fullTime(a.source.read_at)); }
    if (t.dependency_states.length) { const deps = section(root, 'Dependencies'); for (const d of t.dependency_states) deps.append(el('p', '', d.task_id + ' · ' + d.note)); }
    if (t.snapshots.length || t.conflicts.length) {
      const snapshots = section(root, 'Document snapshots');
      if (t.snapshots_truncated) snapshots.append(el('p', 'notice', 'Only 200 / ' + t.snapshot_source_count + ' sources shown; differences may be incomplete.'));
      if (t.time_warnings) snapshots.append(el('p', 'notice', t.time_warnings + ' source times abnormal; not used to choose the latest state.'));
      if (t.superseded_snapshot_count) snapshots.append(el('p', 'subtle', t.superseded_snapshot_count + (t.superseded_snapshot_count === 1 ? ' document predates the latest execution report and is' : ' documents predate the latest execution report and are') + ' no longer compared.'));
      for (const conflict of t.conflicts) { const n = el('div', 'conflict'); n.append(el('strong', '', 'To check · ' + (fields[conflict.field] || conflict.field))); for (const [label, v] of conflict.claims) n.append(el('p', '', label + ' → ' + str(v))); snapshots.append(n); }
      for (const [i, s] of t.snapshots.entries()) {
        const n = el('div', 'source'); n.append(el('strong', '', s.source.label || kinds.document_snapshot)); evidence(n, [s.source.path]);
        for (const [label, v] of [['Source time', s.source.source_at], ['Read at', s.source.read_at]]) kv(n, label, fullTime(v));
        for (const [k, v] of Object.entries(s.data.fields)) kvIf(n, fields[k] || k, v);
        n.append(disclosure('Source excerpt', 'snapshot-' + i, [el('pre', '', s.data.excerpt)])); snapshots.append(n);
      }
    }
    const deliveryRow = d => { const n = el('div', 'source'); n.append(el('strong', '', (stages[d.data.stage] || d.data.stage) + ' · ' + d.data.verdict)); kv(n, 'run', d.run_id); kv(n, 'Candidate SHA', d.data.candidate_sha); kvIf(n, 'audit_id', d.data.audit_id); kv(n, 'Reported at', fullTime(d.occurred_at)); evidence(n, [d.data.evidence]); return n; };
    const deliveries = section(root, 'Delivery records'), applicable = t.deliveries.filter(d => d.applicable), other = t.deliveries.filter(d => !d.applicable);
    if (!t.deliveries.length) deliveries.append(el('p', 'subtle', 'None recorded; no independent audit, deployment or production GO is inferred.'));
    deliveries.append(...applicable.map(deliveryRow));
    if (other.length) deliveries.append(disclosure('Not for the current candidate / history · ' + other.length, 'old-deliveries', other.map(deliveryRow)));
    const runRow = r => el('p', 'run-history', [r.id, r.role, r.agent, r.client, names[r.state.status] || 'Unknown', r.state.phase, r.business_at ? ago(r.business_at) : 'Never reported'].filter(Boolean).join(' · '));
    const runs = section(root, 'All runs · ' + t.runs.length), done = t.runs.filter(r => r.state.status === 'ended');
    runs.append(...t.runs.filter(r => r.state.status !== 'ended').map(runRow));
    if (done.length) runs.append(disclosure('Ended · ' + done.length, 'ended-runs', done.map(runRow)));
    const timeline = el('ol', 'timeline'); for (const e of t.events) { const n = el('li'); n.append(el('strong', '', e.op + ' · ' + (e.run_id || 'document / task') + (e.seq ? ' #' + e.seq : '')), el('p', 'time', 'Occurred ' + fullTime(e.occurred_at) + ' · received ' + fullTime(e.received_at)), el('p', 'subtle', (kinds[e.body.source.kind] || 'Unknown source') + ' · ' + e.reason)); n.append(disclosure('Event content', 'event-' + e.body.event_id, [el('pre', '', JSON.stringify(e.body.data, null, 2))])); timeline.append(n); }
    root.append(disclosure('Event timeline · latest 100', 'timeline', [timeline]));
  });
  if (t.task_board) { scrollPlan(t); loadPlan(t); }
}
async function get(url) { const response = await fetch(url, {cache:'no-store', headers:instance ? {'X-Agent-Board-Instance':instance} : {}, signal:AbortSignal.timeout(4500)}); if (!response.ok) throw new Error('HTTP ' + response.status); const result = await response.json(); return result; }
async function loadDetail() {
  if (!selected) return;
  const id = selected, generation = ++detailGeneration;
  try { const t = await get('/api/task?id=' + encodeURIComponent(id)); if (selected !== id || generation !== detailGeneration) return; if(t.service !== 'agent-board-v1' || t.instance_id !== instance || t.id !== id) throw new Error('Detail instance or task identity mismatch'); const fp = JSON.stringify(t); if (fp === detailPrint) { if (t.task_board) loadPlan(t); return; } detailPrint = fp; renderDetail(t); }
  catch (e) { if (selected !== id || generation !== detailGeneration) return; $('error').hidden = false; $('error').textContent = 'Detail read failed; keeping what was already read: ' + e.message; }
}
async function refresh() {
  const generation = ++refreshGeneration;
  try {
    const next = await get('/api/state'); if (generation !== refreshGeneration) return;
    if (next.service !== 'agent-board-v1' || !next.instance_id || (instance && instance !== next.instance_id)) throw new Error('The service instance changed; reload the whole page and check again');
    instance = next.instance_id; state = next; const fp = JSON.stringify([next.tasks, next.event_count, next.outbox]);
    if (fp !== statePrint) { statePrint = fp; renderOverview(); }
    $('connection').textContent = 'Local service online'; $('refresh-time').textContent = 'Page refreshed ' + fullTime(new Date().toISOString()); $('read-time').textContent = 'State read ' + fullTime(next.generated_at);
    $('error').hidden = true; if (typeof onBoardRefresh === 'function') onBoardRefresh(); await loadDetail();
  } catch (e) { if (generation !== refreshGeneration) return; $('connection').textContent = 'This read did not complete'; $('error').hidden = false; $('error').textContent = 'Read failed; keeping the last view (not the latest state): ' + e.message; $('tasks').setAttribute('aria-busy', 'false'); }
}
for (const [k, v] of Object.entries(names)) $('status').append(new Option(v, k));
for (const id of ['status', 'agent', 'search', 'attention-filter']) $(id).addEventListener('input', renderTasks);
$('workstream').addEventListener('input', () => {renderNavigation();renderTasks();});
$('show-old').addEventListener('change', () => {renderNavigation();renderTasks();});
$('clear-filters').addEventListener('click', () => { for (const id of ['status', 'agent', 'search', 'attention-filter']) $(id).value = ''; renderTasks(); $('search').focus(); });
$('close-detail').addEventListener('click', closeDetail); $('refresh').addEventListener('click', () => refresh());
document.addEventListener('keydown', e => {
  if (!selected) return;
  if (e.key === 'Escape') { e.preventDefault(); closeDetail(); }
  if (e.key === 'Tab' && $('detail').getAttribute('aria-modal') === 'true') {
    const focusable = [...$('detail').querySelectorAll('button, summary, a[href], [tabindex="0"]')].filter(n => n.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1); if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
  }
});
window.addEventListener('resize', panelMode);
document.addEventListener('DOMContentLoaded', () => {
  refresh().then(() => {
    let id = null; try { id = decodeURIComponent(location.hash.slice(1)) || null; } catch {}
    if (id && state?.tasks.some(t => t.id === id)) openTask(id, null, false);
  });
});
setInterval(() => { if (!document.hidden) refresh(); }, 10000);
