// Author UI regression. Owned synthetic database + unchanged production HTTP server.
// Browser requests are GET-only; no live board, external font, or source mutation.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
const exec=promisify(execFile),here=path.dirname(fileURLToPath(import.meta.url));
const python=process.env.AGENT_BOARD_PYTHON||(process.platform==='win32'?'py':'python3'),pythonArgs=python==='py'?['-3','-B']:['-B'];
const output=path.resolve(process.argv[2]),root=await fs.mkdtemp(path.join(os.tmpdir(),'board-workstream-ui-'));
const {chromium}=await import(process.env.AGENT_BOARD_PLAYWRIGHT_MODULE);
await fs.mkdir(output,{recursive:true});
const results=[],errors=[],network=[];
let fixture,browser;
async function check(name,fn){
  try {results.push({name,result:'PASS',detail:await fn()??null});console.log('PASS '+name);}
  catch(e){results.push({name,result:'FAIL',detail:e.stack});console.log('FAIL '+name+' '+e.message);}
}
const rows=p=>p.locator('#tasks .task-entry');
const row=(p,id)=>p.locator('#tasks .task-entry[data-task-id="'+id+'"]');
const stream=(p,name)=>p.locator('.stream-link[data-stream="'+name+'"]').click();
async function ready(p){await p.waitForFunction(()=>document.querySelector('#tasks').getAttribute('aria-busy')==='false');}
async function open(p,id){await row(p,id).locator('.task-name').click();await p.locator('#detail-body .detail-section').first().waitFor();}
// Cards are one line; run, audit and notice details live in the task detail.
const detailText=async(p,id)=>{await p.evaluate(id=>openTask(id,null,false),id);await p.waitForFunction(id=>detailValue?.id===id,id);const text=await p.locator('#detail-body').innerText();await p.locator('#close-detail').click();return text;};
const notes=(p,id)=>p.evaluate(id=>reasons(state.tasks.find(t=>t.id===id)).join(' | '),id);
async function context(size={width:1440,height:900}){
  const c=await browser.newContext({viewport:size,reducedMotion:'reduce',permissions:['clipboard-read','clipboard-write']});
  await c.route('**/*',r=>{
    if(r.request().method()!=='GET'||new URL(r.request().url()).origin!==fixture.url){network.push(r.request().url());return r.abort();}
    return r.continue();
  });
  const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));
  p.on('console',e=>{if(e.type()==='error'&&!/503|404|409/.test(e.text()))errors.push(e.text());});
  await p.goto(fixture.url+'/?view=list');await ready(p);return{c,p};
}
const refresh=p=>p.evaluate(()=>document.querySelector('#refresh').click());
try {
  fixture=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'workstream_fixture.py'),root])).stdout);
  const health=await(await fetch(fixture.url+'/api/health')).json();
  assert.equal(health.service,'agent-board-v1');assert.equal(health.instance_id,fixture.instance_id);
  const state=await(await fetch(fixture.url+'/api/state')).json();
  assert.equal(state.demo,undefined);assert.equal(state.tasks.length,36);
  browser=await chromium.launch({headless:true,executablePath:process.env.AGENT_BOARD_CHROME||undefined});
  for(const size of [{width:1440,height:900},{width:1280,height:800},{width:390,height:844}]){
    const{c,p}=await context(size);
    await check(size.width+': real non-demo API / light palette / full task set',async()=>{
      assert.equal(await rows(p).count(),36);
      const geometry=await p.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,background:getComputedStyle(document.body).backgroundColor,scheme:getComputedStyle(document.documentElement).colorScheme,taskTop:document.querySelector('#tasks').getBoundingClientRect().top}));
      assert(geometry.scrollWidth<=size.width);assert.equal(geometry.background,'rgb(243, 240, 231)');assert.equal(geometry.scheme,'light');
      assert(!await p.locator('body').innerText().then(t=>t.includes('demo data')));assert((await p.locator('#read-time').innerText()).includes('2026'));
      await p.screenshot({path:path.join(output,'board-'+size.width+'.png')});
      await p.screenshot({path:path.join(output,'board-'+size.width+'-full.png'),fullPage:true});return geometry;
    });
    await check(size.width+': board -> decision -> evidence -> same reading position',async()=>{
      await stream(p,'Frontend');await p.locator('#attention-filter').selectOption('human');
      assert.equal(await rows(p).count(),1);assert.equal(await row(p,'public-docs').locator('.ask-tag').innerText(),'Needs you 1');
      assert((await p.locator('#attention .inbox-row[data-task-id="public-docs"]').innerText()).includes('30 or 90'));
      await row(p,'public-docs').locator('.task-name').scrollIntoViewIfNeeded();
      const before=await p.evaluate(()=>({scroll:scrollY,stream:document.querySelector('#workstream').value,filter:document.querySelector('#attention-filter').value}));
      await open(p,'public-docs');await p.locator('#detail-body .copy-path').first().click();
      await p.waitForFunction(()=>document.querySelector('#detail-body .copy-status').textContent==='Copied');
      const value=await p.locator('#detail-body .path code').first().innerText();assert.equal(await p.evaluate(()=>navigator.clipboard.readText()),value);
      await p.screenshot({path:path.join(output,'detail-'+size.width+'.png')});
      await p.keyboard.press('Escape');assert(await p.locator('#detail').isHidden());
      assert.deepEqual(await p.evaluate(()=>({scroll:scrollY,stream:document.querySelector('#workstream').value,filter:document.querySelector('#attention-filter').value})),before);
      assert.equal(await p.evaluate(()=>document.activeElement.dataset.focus),'task-public-docs');
    });
    await c.close();
  }
  const{c,p}=await context();
  await check('Same-run joint filters and clear keeps workstream',async()=>{
    await stream(p,'Data export');await p.locator('#status').selectOption('ended');await p.locator('#agent').selectOption('agent-a');assert.equal(await rows(p).count(),1);
    await p.locator('#agent').selectOption('reviewer');assert.equal(await rows(p).count(),0);
    await p.locator('#clear-filters').click();assert.equal(await p.locator('#workstream').inputValue(),'Data export');
    await stream(p,'');await p.locator('#search').fill('form-validation');assert.equal(await rows(p).count(),1);await p.locator('#clear-filters').click();
  });
  await check('Execution, review, candidate applicability, source distinctions',async()=>{
    const single=await detailText(p,'form-validation');
    assert(single.includes('Execution · Ended'));assert(single.includes('Audit · In progress'));assert(single.includes('Not the current execution candidate'));
    assert((await detailText(p,'notifications')).includes('the item is not stated'));
    assert((await row(p,'nightly').locator('.badge').innerText()).includes('stale'));
    assert((await detailText(p,'board-layout')).includes('Sources disagree'));
    assert(!(await row(p,'audit-only').innerText()).includes('Current execution activity unknown'));
    assert.equal(await row(p,'public-docs').locator('.audit-brief').count(),0);
    await open(p,'nightly');assert((await p.locator('#detail-body').innerText()).includes('Program observation'));
    assert((await p.locator('#detail-body').innerText()).includes('Observed at'));await p.locator('#close-detail').click();
    await open(p,'doc-export');assert((await p.locator('#detail-body').innerText()).includes('Not for the current candidate'));
    // Historical deliveries fold under their own summary; opening it shows the source caveat.
    await p.locator('#detail-body details[data-key="old-deliveries"] > summary').click();
    assert((await p.locator('#detail-body').innerText()).includes('not verified automatically'));await p.locator('#close-detail').click();
  });
  await check('Global attention unchanged by local filters / no results',async()=>{
    const before=await p.locator('#attention').innerText();
    assert(before.includes('30 or 90'));assert(!before.includes('Release feed'));assert(!before.includes('the item is not stated'));
    assert.equal(await p.locator('.stream-link[data-stream=""] .nav-meta').innerText(),'Needs you 1');
    assert.equal(await p.locator('.metric').count(),0);
    assert((await detailText(p,'release-feed')).includes('No action needed from you'));
    assert((await notes(p,'notifications')).includes('Who must give input is not declared'));
    await stream(p,'Frontend');await p.locator('#search').fill('nothing matches');assert.equal(await rows(p).count(),0);
    assert.equal(await p.locator('#attention').innerText(),before);
    await p.locator('#clear-filters').click();await stream(p,'');
  });
  await check('Unresolved stale ended audit stays human; contradictory non-human text remains local',async()=>{
    await p.route('**/api/state',async r=>{
      const response=await r.fetch(),body=await response.json();
      // Openness is the server's open_questions; simulate it, not the raw declaration.
      const old=body.tasks.find(t=>t.id==='public-docs');old.state.human_input='not_required';old.overview_runs[0].state.human_input='not_required';
      old.open_questions=[];old.human_input_required=false;old.overview_runs[0].human_input_required=false;
      const t=body.tasks.find(t=>t.id==='form-validation'),audit=t.overview_runs.find(r=>r.role==='audit');
      audit.state.human_input='required';audit.state.status='ended';audit.state.decision=null;audit.stale=true;audit.human_input_required=true;
      t.open_questions=[{kind:'declaration',question_id:null,question:null,options:[],item:null,unanswered_policy:null,asked_at:audit.business_at,
        run_id:audit.id,agent:audit.agent,client:audit.client,role:'audit',run_ended:true,former_owner:false,run_business_at:audit.business_at,run_stale:true}];t.human_input_required=true;
      await r.fulfill({response,json:body});
    });
    // A flag without text from an ended audit: a grey tag on the task, still open, not an inbox row.
    await refresh(p);await p.waitForFunction(()=>document.querySelector('#tasks [data-task-id="form-validation"] .declaration-tag'));
    assert.equal(await p.locator('#attention .inbox-row').count(),0);
    assert.equal(await row(p,'form-validation').locator('.declaration-tag .close-q').count(),1);
    assert.equal(await p.evaluate(()=>state.tasks.find(t=>t.id==='form-validation').overview_runs.find(r=>r.role==='audit').state.status),'ended');
    assert((await notes(p,'public-docs')).includes('Declared no action needed from you, but a decision is still pending'));
    assert.equal(await p.locator('#attention [data-task-id="public-docs"]').count(),0);
    await p.unroute('**/api/state');await refresh(p);await p.waitForFunction(()=>document.querySelector('#attention').textContent.includes('30 or 90'));
  });
  await check('Zero requests is header-only; no repeated explanations or source badges',async()=>{
    await p.route('**/api/state',async r=>{
      const response=await r.fetch(),body=await response.json();
      for(const t of body.tasks){t.open_questions=[];t.human_input_required=false;for(const run of [...t.overview_runs,...(t.parallel_runs||[])]){run.state.human_input='unknown';run.human_input_required=false;}}
      await r.fulfill({response,json:body});
    });
    await refresh(p);await p.waitForFunction(()=>!document.querySelector('#attention .inbox-row'));
    assert.equal(await p.locator('#attention').textContent(),'Nothing needs you.');
    assert.equal(await p.locator('#attention-count').innerText(),'None');
    assert.equal(await p.locator('.stream-link .nav-meta').count(),0);
    assert(await p.locator('#stream-summary').isHidden());
    assert.equal(await p.locator('.nav-legend,.scope-note,.scope-label,.filter-help,#tasks .source-labels').count(),0);
    assert.equal(await p.locator('footer p').count(),0);
    assert.equal(await p.locator('#connection').innerText(),'Local service online');
    assert((await notes(p,'notifications')).includes('Who must give input is not declared'));
    assert((await row(p,'nightly').locator('.badge').innerText()).includes('stale'));
    for(const size of [{width:1440,height:900},{width:390,height:844}]){
      await p.setViewportSize(size);
      assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await p.screenshot({path:path.join(output,'zero-requests-'+size.width+'.png')});
    }
    await p.setViewportSize({width:1440,height:900});
    await p.unroute('**/api/state');await refresh(p);await p.waitForFunction(()=>document.querySelector('#attention .inbox-row'));
  });
  await check('Actual 10-second refresh preserves focus, filters, expanded inbox row and detail',async()=>{
    await p.locator('#search').fill('public-docs');await p.locator('#attention .inbox-row[data-task-id="public-docs"] .inbox-more').click();
    await open(p,'public-docs');await p.locator('#detail-body > details > summary').click();await p.locator('#search').focus();
    await p.route('**/api/state',async r=>{const response=await r.fetch();const body=await response.json();body.event_count+=2;body.tasks.find(t=>t.id==='public-docs').state.phase='AUTO REFRESH';await r.fulfill({response,json:body});});
    await p.route('**/api/task?*',async r=>{const response=await r.fetch();const body=await response.json();body.state.progress='AUTO DETAIL';await r.fulfill({response,json:body});});
    await p.waitForFunction(()=>document.querySelector('#tasks').textContent.includes('AUTO REFRESH')&&document.querySelector('#detail-body').textContent.includes('AUTO DETAIL'),null,{timeout:15000});
    assert.equal(await p.evaluate(()=>document.activeElement.id),'search');assert.equal(await p.locator('#search').inputValue(),'public-docs');
    assert.equal(await p.locator('#attention .inbox-row[data-task-id="public-docs"] .inbox-detail').count(),1);assert(await p.locator('#detail-body > details').evaluate(n=>n.open));
    await p.unroute('**/api/state');await p.unroute('**/api/task?*');await p.locator('#close-detail').click();await p.locator('#clear-filters').click();
    await p.locator('#attention .inbox-row[data-task-id="public-docs"] .inbox-more').click();
  });
  await check('Old overview response cannot overwrite a newer response',async()=>{
    let release,started,count=0;const gate=new Promise(r=>release=r),seen=new Promise(r=>started=r);
    await p.route('**/api/state',async r=>{const response=await r.fetch();const body=await response.json();if(++count===1){body.tasks[0].title='OLD RESPONSE';started();await gate;}else body.tasks[0].title='NEW RESPONSE';await r.fulfill({response,json:body});});
    await refresh(p);await seen;await refresh(p);await p.waitForFunction(()=>document.querySelector('#tasks').textContent.includes('NEW RESPONSE'));
    release();await p.waitForTimeout(150);assert(!(await p.locator('#tasks').innerText()).includes('OLD RESPONSE'));await p.unroute('**/api/state');
  });
  await check('Delayed details cannot replace new selection or reopen after close',async()=>{
    let release,started;const gate=new Promise(r=>release=r),seen=new Promise(r=>started=r);
    await p.route('**/api/task?*',async r=>{if(new URL(r.request().url()).searchParams.get('id')==='public-docs'){started();await gate;}await r.continue();});
    await row(p,'public-docs').locator('.task-name').click();await seen;await open(p,'doc-export');
    release();await p.waitForTimeout(150);assert((await p.locator('#detail-id').innerText()).includes('doc-export'));
    await p.locator('#close-detail').click();await p.unroute('**/api/task?*');
    let release2,started2;const gate2=new Promise(r=>release2=r),seen2=new Promise(r=>started2=r);
    await p.route('**/api/task?*',async r=>{started2();await gate2;await r.continue();});
    await row(p,'public-docs').locator('.task-name').click();await seen2;await p.locator('#close-detail').click();
    release2();await p.waitForTimeout(150);assert(await p.locator('#detail').isHidden());await p.unroute('**/api/task?*');
  });
  await check('Failure retains old content; service identity mismatch fails closed',async()=>{
    await p.locator('#search').fill('doc-export');const before=await p.locator('#tasks').innerText();
    await p.route('**/api/state',r=>r.fulfill({status:503,contentType:'application/json',body:'{}'}));await refresh(p);await p.locator('#error').waitFor();
    assert.equal(await p.locator('#tasks').innerText(),before);assert.equal(await p.locator('#search').inputValue(),'doc-export');await p.unroute('**/api/state');
    await p.route('**/api/state',async r=>{const response=await r.fetch();const body=await response.json();body.instance_id='different-instance';body.tasks=[];await r.fulfill({response,json:body});});
    await refresh(p);await p.waitForFunction(()=>document.querySelector('#error').textContent.includes('The service instance changed'));assert.equal(await p.locator('#tasks').innerText(),before);await p.unroute('**/api/state');
    await refresh(p);await p.waitForFunction(()=>document.querySelector('#error').hidden);await p.locator('#clear-filters').click();
  });
  await check('Wrong detail task identity rejected',async()=>{
    await p.route('**/api/task?*',async r=>{const response=await r.fetch();const body=await response.json();body.id='wrong-task';await r.fulfill({response,json:body});});
    await row(p,'public-docs').locator('.task-name').click();await p.waitForFunction(()=>document.querySelector('#error').textContent.includes('task identity mismatch'));
    assert.equal(await p.locator('#detail-body .detail-section').count(),0);await p.unroute('**/api/task?*');await p.locator('#close-detail').click();
  });
  await check('Loading, empty and untrusted text remain explicit',async()=>{
    await p.route('**/api/state',async r=>{await new Promise(resolve=>setTimeout(resolve,500));await r.continue();});
    await p.goto(fixture.url+'/?view=list',{waitUntil:'domcontentloaded'});assert.equal(await p.locator('#tasks').getAttribute('aria-busy'),'true');await ready(p);await p.unroute('**/api/state');
    await p.route('**/api/state',async r=>{const response=await r.fetch();const body=await response.json();body.tasks=[];await r.fulfill({response,json:body});});
    await refresh(p);await p.waitForFunction(()=>document.querySelector('#tasks').textContent.includes('No tasks registered yet'));await p.unroute('**/api/state');
    await p.route('**/api/state',async r=>{const response=await r.fetch();const body=await response.json();body.tasks[0].title='<img src=x onerror="window.pwned=1">';await r.fulfill({response,json:body});});
    await refresh(p);await p.waitForFunction(()=>document.querySelector('#tasks').textContent.includes('<img'));assert.equal(await p.locator('#tasks img').count(),0);assert.equal(await p.evaluate(()=>window.pwned),undefined);await p.unroute('**/api/state');await refresh(p);
    // Let the real state replace the injected one before the next check reads nodes.
    await p.waitForFunction(()=>!document.querySelector('#tasks').textContent.includes('<img')&&document.querySelector('#tasks .task-entry'));
  });
  await check('Text contrast / keyboard focus / reduced motion',async()=>{
    await p.locator('#search').focus();assert.equal(await p.locator('#search').evaluate(n=>getComputedStyle(n).outlineStyle),'solid');
    await p.keyboard.press('Tab');assert.equal(await p.evaluate(()=>document.activeElement.id),'status');
    assert.equal(await p.locator('#tasks .task-group > .task-list > .task-entry').first().evaluate(n=>getComputedStyle(n).animationName),'none');
    const contrast=await p.evaluate(()=>{
      const rgb=v=>(v.match(/[\d.]+/g)||[]).map(Number),lum=c=>c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
      function bg(n){let col=[255,255,255],chain=[];while(n){chain.unshift(n);n=n.parentElement;}for(const e of chain){const v=rgb(getComputedStyle(e).backgroundColor),a=v.length<4?1:v[3];col=col.map((x,i)=>v[i]*a+x*(1-a));}return col;}
      const values=[...document.querySelectorAll('body *')].filter(n=>!n.children.length&&n.textContent.trim()&&n.getClientRects().length&&!['OPTION','SCRIPT','STYLE'].includes(n.tagName)).map(n=>{const style=getComputedStyle(n),a=lum(rgb(style.color)),b=lum(bg(n));return{text:n.textContent.slice(0,25),ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
      return{checked:values.length,min:Math.min(...values.map(x=>x.ratio)),failures:values.filter(x=>x.ratio<4.5)};
    });assert.equal(contrast.failures.length,0,JSON.stringify(contrast.failures));return contrast;
  });
  await check('HTTP CSP and asset routes unchanged; no inline script or external assets',async()=>{
    const response=await fetch(fixture.url+'/');assert((response.headers.get('content-security-policy')||'').includes("script-src 'self'"));
    const html=await response.text();assert(!html.includes('lab.js'));assert(!html.includes('https://'));assert(!html.includes('<script>'));
    assert.equal((await fetch(fixture.url+'/write-token')).status,404);
  });
  await c.close();
  await check('Narrow modal traps focus, returns focus, no overflow',async()=>{
    const{c,p}=await context({width:390,height:844});await open(p,'doc-export');
    assert.equal(await p.locator('#detail').getAttribute('aria-modal'),'true');assert(await p.locator('#workspace').evaluate(n=>n.inert));
    await p.locator('#close-detail').focus();await p.keyboard.press('Shift+Tab');assert(await p.locator('#detail').evaluate(n=>n.contains(document.activeElement)));
    assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await p.keyboard.press('Escape');assert(!(await p.locator('#workspace').evaluate(n=>n.inert)));await c.close();
  });
  await check('Browser GET-only / same origin / no console errors',async()=>{assert.deepEqual(errors,[]);assert.deepEqual(network,[]);});
} finally {
  if(browser)await browser.close();
  let stop=null;
  if(fixture)stop=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'../board.py'),'--config',path.join(root,'config.json'),'--runtime',path.join(root,'stress'),'stop'])).stdout);
  await fs.writeFile(path.join(output,'RESULTS.json'),JSON.stringify({scope:'author synthetic Chrome test; not independent review',fixture,root,stop,results,errors,network,passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length},null,2));
}
console.log(JSON.stringify({passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length,output}));
if(results.some(x=>x.result==='FAIL'))process.exitCode=1;
