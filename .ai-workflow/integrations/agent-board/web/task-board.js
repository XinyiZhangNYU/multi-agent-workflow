'use strict';
// Plan section of the task detail. Read-only: the summary is re-read only when
// the plan version moves; other mainlines' branches and history load on request.
// Linked runs come from /api/task and never derive plan progress.
const planNames={draft:'Draft',active:'Execution plan',awaiting_confirmation:'Awaiting planning confirmation',closed:'Closed',planned:'Planned',running:'Running',blocked:'Blocked',done:'Done',cancelled:'Cancelled',replaced:'Replaced'};
const planHistory=new Map(),planBox=el('section','detail-section plan-section');planBox.id='plan-content';planBox.setAttribute('aria-busy','false');
let planGeneration=0,plan={task:null,version:null,value:null,error:null,loading:null},planScroll=null;
function planOverviewText(o){return !o?.has_mainlines?'Not planned yet':`Mainlines ${o.done}/${o.active} · incomplete ${o.incomplete} · cancelled ${o.cancelled} · replaced ${o.replaced}`;}
function itemName(item){return item?item.mainline+(item.branch?'/'+item.branch:''):'';}
function planIdentity(v){if(v.service!=='agent-board-v1'||!v.instance_id||(instance&&v.instance_id!==instance))throw new Error('The service instance changed; reload the whole page');instance=v.instance_id;}
function planCurrent(id){return selected===id&&plan.task===id;}
function planFor(id){if(plan.task!==id){++planGeneration;plan={task:id,version:null,value:null,error:null,loading:null};planHistory.clear();}}
async function summaryPages(id,mainline=null,generation=planGeneration){
 let offset=0,value=null;do{const q=new URLSearchParams({task_id:id,summary:'1',offset:String(offset)});if(mainline)q.set('mainline',mainline);if(value)q.set('version',String(value.version));
  const next=await get('/api/task-board?'+q);if(generation!==planGeneration||!planCurrent(id))return null;planIdentity(next);if(next.task_id!==id)throw new Error('Task identity mismatch');
  if(!value)value=next;else{if(value.version!==next.version)throw new Error('Plan version changed');value.summary.mainlines.push(...next.summary.mainlines);value.summary.branches.push(...next.summary.branches);}offset=next.page?.next_offset;
 }while(offset!==null&&offset!==undefined);return value;
}
// Called on every detail render; fetches only when the listed plan version moved.
async function loadPlan(t){
 planFor(t.id);
 if(!t.task_board||(plan.value&&plan.version===t.task_board.version)||plan.loading===t.task_board.version)return;
 const generation=++planGeneration;plan.loading=t.task_board.version;planBox.setAttribute('aria-busy','true');
 try{const value=await summaryPages(t.id,null,generation);if(!value)return;plan.value=value;plan.version=value.version;plan.error=null;}
 catch(e){if(generation!==planGeneration||!planCurrent(t.id))return;plan.error=e.message;}
 finally{if(generation===planGeneration){plan.loading=null;planBox.setAttribute('aria-busy','false');}}
 if(planCurrent(t.id)&&detailValue?.id===t.id)drawPlan(detailValue);
}
function linkedRuns(t,m){return Object.entries(t.item_runs||{}).filter(([k])=>k===m.id||k.startsWith(m.id+'/')).flatMap(([,v])=>v);}
function historyDisclosure(value,m=null){
 const id=value.task_id,key=value.version+':'+(m?.id||'changes'),contents=el('div'),d=disclosure(m?'Folded-in branch results · '+m.history_count:'Explicit changes to this task','plan-history-'+(m?.id||'changes'),[contents]);let loaded=false;
 async function loadPage(offset=0){const generation=planGeneration,q=new URLSearchParams({task_id:id,section:'history',version:String(value.version),offset:String(offset)});if(m)q.set('mainline',m.id);
  try{const next=await get('/api/task-board?'+q);if(generation!==planGeneration||!planCurrent(id))return;planIdentity(next);if(next.task_id!==id||next.version!==value.version)throw new Error('History target changed');const old=offset?planHistory.get(key)?.items||[]:[];planHistory.set(key,{items:[...old,...next.history],next:next.page.next_offset});draw();loaded=true;}
  catch(e){if(generation===planGeneration&&planCurrent(id))contents.replaceChildren(el('p','notice','Read not completed: '+e.message));}
 }
 function draw(){const cached=planHistory.get(key);contents.replaceChildren();for(const item of cached.items){contents.append(el('p','',item.result?item.branch_id+' · '+item.result:JSON.stringify(item)));evidence(contents,item.evidence);}if(cached.next!==null)contents.append(button('Load more history',()=>loadPage(cached.next)));}
 if(planHistory.has(key)){draw();loaded=true;}d.addEventListener('toggle',()=>{if(d.open&&!loaded)loadPage();});return d;
}
function branchesDisclosure(value,m,t){
 const contents=el('div'),d=disclosure('Current related branches','plan-branches-'+m.id,[contents]);let loaded=false;
 function draw(sides){contents.replaceChildren();for(const s of sides){const item=el('section','plan-branch');item.dataset.branchId=s.id;item.append(el('strong','',s.goal),el('p','subtle',s.id+' · '+planNames[s.status]+' · linked runs '+((t.item_runs||{})[m.id+'/'+s.id]||[]).length),el('p','','Next step · '+str(s.next_step)));kvIf(item,'Plan owner (text)',s.owner);if(s.blocker)item.append(el('p','notice',s.blocker));evidence(item,s.evidence);contents.append(item);}if(!sides.length)contents.append(el('p','subtle','No unfinished branches.'));loaded=true;}
 if(m.id===value.summary.focus.mainline_id)draw(value.summary.branches);
 d.addEventListener('toggle',async()=>{if(d.open&&!loaded){const generation=planGeneration;try{const scoped=await summaryPages(value.task_id,m.id,generation);if(scoped&&scoped.version===value.version)draw(scoped.summary.branches);}catch(e){contents.replaceChildren(el('p','notice',e.message));}}});return d;
}
function drawPlan(t){
 planFor(t.id);const value=plan.value;planBox.dataset.taskId=t.id;
 preserve(planBox,()=>{
  planBox.replaceChildren(el('h3','','Plan'));
  if(plan.error)planBox.append(el('p','notice',value?'Plan read failed; keeping the last content (not the latest state): '+plan.error:'Plan read failed: '+plan.error));
  if(!value){if(!plan.error)planBox.append(el('p','empty','Reading plan…'));return;}
  const b=value.summary,head=el('div','plan-heading');head.append(el('p','plan-goal',b.goal),el('p','subtle',planNames[b.state]+' · version '+value.version+' · '+(b.frozen?'mainlines frozen':'mainlines not frozen yet')),el('p','plan-overview',planOverviewText(b.overview)),el('p','subtle','Frozen baseline '+(b.overview.baseline??'not recorded')+' · added '+b.overview.added+' · replacements added '+b.overview.replacements));
  const owners=[...new Set(b.mainlines.map(m=>m.owner).filter(Boolean))],sharedOwner=owners.length===1&&b.mainlines.every(m=>m.owner===owners[0])?owners[0]:null;if(sharedOwner)head.append(el('p','subtle','Plan owner (text) · '+sharedOwner));
  const focus=b.mainlines.find(m=>m.id===b.focus.mainline_id);head.append(el('p','plan-focus','Current focus · '+(focus?focus.id+' '+focus.goal:b.focus.mainline_id||'not set')),el('p','running-mainlines','All running mainlines · '+(b.mainlines.filter(m=>m.status==='running').map(m=>m.goal).join(' / ')||'none')));
  planBox.append(head);if(!b.mainlines.length)planBox.append(el('p','empty','Not planned yet.'));
  for(const m of b.mainlines){
   const card=el('section','plan-mainline'+(m.id===b.focus.mainline_id?' current':''));card.dataset.mainlineId=m.id;const title=el('div','plan-line-title');title.append(el('span','plan-position',m.id),el('h4','',m.goal),el('span','plan-status',planNames[m.status]));card.append(title);
   kvIf(card,'Next step',m.next_step);if(m.blocker)card.append(el('p','notice','Blocked · '+m.blocker));if(m.result)card.append(el('p','plan-result','Result · '+m.result));
   const runs=linkedRuns(t,m),audits=runs.filter(r=>r.role==='audit');card.append(el('p','plan-links subtle','Linked runs '+runs.length+(audits.length?' · audits '+audits.length:'')));
   const more=el('div');kv(more,'Completion criteria',m.completion);if(!sharedOwner)kvIf(more,'Plan owner (text)',m.owner);if(m.change_reason)more.append(el('p','notice','Change reason · '+m.change_reason));if(m.dependencies.length)kv(more,'Dependencies',m.dependencies.join(' / '));if(m.parallel_with.length)kv(more,'Parallel with',m.parallel_with.join(' / '));evidence(more,m.evidence);
   for(const [label,list] of [['Linked execution runs',runs.filter(r=>r.role!=='audit')],['Linked audit runs',audits]])if(list.length){const box=el('div','plan-runs');box.append(el('p','subtle',label+' · '+list.length));box.append(...list.map(r=>runFold(r,t,'plan-run-')));more.append(box);}
   more.append(branchesDisclosure(value,m,t));if(m.history_count)more.append(historyDisclosure(value,m));
   card.append(disclosure('Completion criteria, linked runs, branches and history','plan-more-'+m.id,[more]));planBox.append(card);
  }if(b.frozen)planBox.append(historyDisclosure(value));
 });
 scrollPlan(t);
}
// The "Plan" button lands on the plan once it is drawn.
function scrollPlan(t){const body=$('detail-body');if(planScroll!==t.id||!(plan.value||plan.error)||!body.contains(planBox))return;planScroll=null;body.scrollTop+=planBox.getBoundingClientRect().top-body.getBoundingClientRect().top;}
