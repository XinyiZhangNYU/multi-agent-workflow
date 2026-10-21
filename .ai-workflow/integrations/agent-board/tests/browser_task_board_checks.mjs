// Author UI checks for the plan section of the task detail (formerly the task
// board page). Random port, synthetic DB, browser GET-only, no live writes.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
const exec=promisify(execFile), here=path.dirname(fileURLToPath(import.meta.url));
const root=await fs.mkdtemp(path.join(os.tmpdir(),'task-board-ui-')), output=path.resolve(process.argv[2]);
const python=process.env.AGENT_BOARD_PYTHON||(process.platform==='win32'?'py':'python3'), {chromium}=await import(process.env.AGENT_BOARD_PLAYWRIGHT_MODULE);
const results=[],errors=[],blocked=[];let fixture,browser;
await fs.mkdir(output,{recursive:true});
async function check(name,fn){try{await fn();results.push({name,result:'PASS'});console.log('PASS '+name);}catch(e){results.push({name,result:'FAIL',error:e.stack});console.log('FAIL '+name+' '+e.message);}}
async function ready(p,id){await p.waitForFunction(id=>{const n=document.querySelector('#detail-body #plan-content');return document.querySelector('#detail-id').textContent.includes(id)&&n&&n.dataset.taskId===id&&n.getAttribute('aria-busy')==='false'&&n.querySelectorAll('.plan-mainline').length===2;},id);}
const listed=p=>p.waitForFunction(()=>document.querySelector('#tasks').getAttribute('aria-busy')==='false');
const openPlan=async(p,id)=>{await p.locator('.task-entry[data-task-id="'+id+'"] .plan-open').click();await ready(p,id);};
async function context(viewport){
 const c=await browser.newContext({viewport,reducedMotion:'reduce'});
 await c.route('**/*',r=>{if(r.request().method()!=='GET'||new URL(r.request().url()).origin!==fixture.url){blocked.push(r.request().url());return r.abort();}return r.continue();});
 const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));return {c,p};
}
try{
 fixture=JSON.parse((await exec(python,['-B',path.join(here,'task_board_fixture.py'),root],{env:{...process.env,PYTHONUTF8:'1'}})).stdout);
 browser=await chromium.launch({headless:true,executablePath:process.env.AGENT_BOARD_CHROME||undefined});
 for(const viewport of [{width:1440,height:900},{width:1280,height:800},{width:390,height:844}]){
  const{c,p}=await context(viewport);await p.goto(fixture.url+'/?view=list');await listed(p);
  await check('Plan opens in the task detail with stable mainline order '+viewport.width,async()=>{
   await p.locator('#search').fill('metrics');await openPlan(p,'doc-export');
   assert.deepEqual(await p.locator('.plan-mainline').evaluateAll(ns=>ns.map(n=>n.dataset.mainlineId)),['first','second']);
   assert.match(await p.locator('.plan-mainline').first().innerText(),/Done/);
   assert.equal(await p.locator('.plan-mainline.current').getAttribute('data-mainline-id'),'second');
   assert.match(await p.locator('.plan-heading').innerText(),/mainlines frozen/);
   await p.keyboard.press('Escape');assert(await p.locator('#detail').isHidden());assert.equal(await p.locator('#search').inputValue(),'metrics');
   assert.equal(await p.evaluate(()=>document.activeElement.dataset.focus),'plan-open-metrics-history');
   await openPlan(p,'doc-export');
  });
  await check('Collapsed branches, focus preservation, escaping '+viewport.width,async()=>{
   await p.locator('details[data-key="plan-more-second"] > summary').click();
   const d=p.locator('details[data-key="plan-branches-second"]');assert.equal(await d.getAttribute('open'),null);
   await d.locator('summary').click();await d.locator('summary').focus();
   await p.evaluate(()=>{detailPrint='';return refresh();});
   assert.equal(await d.evaluate(n=>n.open),true);
   assert.equal(await p.evaluate(()=>document.activeElement.dataset.focus),'plan-branches-second');
   assert.equal(await p.locator('#plan-content img').count(),0);assert.match(await d.innerText(),/<img src=x/);
  });
  await check('Layout and screenshot '+viewport.width,async()=>{
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   assert.equal(await p.evaluate(()=>[...document.querySelectorAll('#detail-body,#detail-body .plan-mainline')].every(n=>n.scrollWidth<=n.clientWidth+1)),true);
   assert.equal(await p.locator('#plan-content .copy-path').count(),1);
   await p.screenshot({path:path.join(output,'task-board-'+viewport.width+'.png'),fullPage:true});
  });
  await c.close();
 }
 const {c,p}=await context({width:1440,height:900});await p.goto(fixture.url+'/?view=list#public-docs');await listed(p);await ready(p,'public-docs');
 await check('Task hash link opens that task detail with its plan',async()=>{
  assert.match(await p.locator('.plan-goal').innerText(),/Page delivery/);
  assert.equal(await p.locator('#tab-task-board,#task-board-view').count(),0);
 });
 await check('Late plan response cannot overwrite a newly selected task',async()=>{
  let release;const gate=new Promise(r=>release=r);let started=false;
  await c.route('**/api/task-board?task_id=public-docs&summary=1*',async r=>{started=true;await gate;await r.continue();});
  await p.evaluate(()=>{plan.version=-1;return refresh();});const deadline=Date.now()+5000;while(!started&&Date.now()<deadline)await new Promise(r=>setTimeout(r,20));assert(started);
  await openPlan(p,'doc-export');
  release();await new Promise(r=>setTimeout(r,250));
  assert.match(await p.locator('#detail-id').innerText(),/doc-export/);assert.match(await p.locator('.plan-goal').innerText(),/Data recheck/);
  assert.equal(await p.locator('#plan-content').getAttribute('data-task-id'),'doc-export');
  await c.unroute('**/api/task-board?task_id=public-docs&summary=1*');
 });
 await check('Failed read for a new task shows no previous task plan; retry stays scoped',async()=>{
  await c.route('**/api/task-board?task_id=public-docs&summary=1*',r=>r.fulfill({status:503,json:{error:'synthetic new-task failure'}}));
  await p.locator('.task-entry[data-task-id="public-docs"] .plan-open').click();
  await p.waitForFunction(()=>document.querySelector('#plan-content')?.dataset.taskId==='public-docs'&&document.querySelector('#plan-content').getAttribute('aria-busy')==='false'&&/Plan read failed/.test(document.querySelector('#plan-content').textContent));
  assert.equal(await p.locator('.plan-heading').count(),0);assert(!((await p.locator('#plan-content').innerText()).includes('Data recheck')));
  assert.equal(new URL(p.url()).hash,'#public-docs');
  await c.unroute('**/api/task-board?task_id=public-docs&summary=1*');
  await p.evaluate(()=>refresh());await ready(p,'public-docs');
  assert.match(await p.locator('.plan-goal').innerText(),/Page delivery/);
 });
 await check('Request failure retains the verified plan with a notice',async()=>{
  const before=await p.locator('.plan-mainline').evaluateAll(ns=>ns.map(n=>n.innerText));
  await c.route('**/api/task-board?task_id=public-docs&summary=1*',r=>r.fulfill({status:503,json:{error:'synthetic failure'}}));
  await p.evaluate(()=>{plan.version=-1;return refresh();});
  await p.waitForFunction(()=>/keeping the last content/.test(document.querySelector('#plan-content').textContent));
  assert.deepEqual(await p.locator('.plan-mainline').evaluateAll(ns=>ns.map(n=>n.innerText)),before);
  await c.unroute('**/api/task-board?task_id=public-docs&summary=1*');
  await p.evaluate(()=>refresh());await p.waitForFunction(()=>!/keeping the last content/.test(document.querySelector('#plan-content').textContent));
 });
 await check('Loading is busy until the plan request finishes',async()=>{
  let release;const gate=new Promise(r=>release=r);
  await c.route('**/api/task-board?task_id=public-docs&summary=1*',async r=>{await gate;await r.continue();});
  const pending=p.evaluate(()=>{plan.version=-1;return refresh();});
  await p.waitForFunction(()=>document.querySelector('#plan-content').getAttribute('aria-busy')==='true');
  release();await pending;await ready(p,'public-docs');await c.unroute('**/api/task-board?task_id=public-docs&summary=1*');
 });
 await check('One poll reads state and detail; the plan only when its version moves',async()=>{
  const reads=[];const listen=r=>reads.push(new URL(r.url()).pathname);p.on('request',listen);
  await p.evaluate(()=>refresh());p.off('request',listen);
  assert(reads.includes('/api/state'));assert(reads.includes('/api/task'));assert(!reads.some(x=>x.startsWith('/api/task-board')),reads.join());
 });
 await check('Workstream overview line and plan counts for each task',async()=>{
  await p.keyboard.press('Escape');
  // The one-line card keeps a short "Plan" chip; the counts sit in its title and the detail.
  assert.match(await p.locator('.task-entry[data-task-id="export-second"] .plan-open').getAttribute('title'),/Mainlines 1\/2/);
  await openPlan(p,'export-second');
  assert.match(await p.locator('.plan-overview').innerText(),/Mainlines 1\/2/);assert.match(await p.locator('.running-mainlines').innerText(),/Mainline two/);
  await p.locator('#close-detail').click();assert.equal(await p.evaluate(()=>document.activeElement.dataset.focus),'plan-open-metrics-second');
 });
 await check('No plan is invented for progress-only tasks',async()=>{
  assert.equal(await p.locator('.task-entry .plan-open').count(),3);
  const plain=p.locator('.task-entry:not(:has(.plan-open))').first();assert(await plain.count());
  await plain.locator('.task-name').click();await p.locator('#detail-body .detail-section').first().waitFor();
  assert.equal(await p.locator('#detail-body #plan-content').count(),0);assert.equal(await p.locator('#detail-body h3').filter({hasText:/^Execution$/}).count(),1);
  await p.keyboard.press('Escape');
 });
 await c.close();
 await check('Full service process restart restores same plans',async()=>{
  const cli=path.join(here,'..','board.py'),args=['-B',cli,'--config',path.join(root,'config.json'),'--runtime',path.join(root,'stress')];
  const before=await(await fetch(fixture.url+'/api/task-board?task_id=doc-export')).json();
  await exec(python,[...args,'stop'],{env:{...process.env,PYTHONUTF8:'1'}});
  fixture=JSON.parse((await exec(python,[...args,'start','--port','0'],{env:{...process.env,PYTHONUTF8:'1'}})).stdout);
  const after=await(await fetch(fixture.url+'/api/task-board?task_id=doc-export')).json();
  assert.deepEqual(before.board,after.board);assert.equal(before.version,after.version);assert.notEqual(before.instance_id,after.instance_id);
 });
 assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
}finally{
 if(browser)await browser.close();
 if(fixture)await exec(python,['-B',path.join(here,'..','board.py'),'--config',path.join(root,'config.json'),'--runtime',path.join(root,'stress'),'stop'],{env:{...process.env,PYTHONUTF8:'1'}});
 await fs.writeFile(path.join(output,'browser-task-board.json'),JSON.stringify({results,errors,blocked,fixtureRoot:root},null,2));
 // Keep only this test's fixture as failure evidence; no other runtime cleanup.
}
if(results.some(r=>r.result==='FAIL')||errors.length||blocked.length)process.exitCode=1;
