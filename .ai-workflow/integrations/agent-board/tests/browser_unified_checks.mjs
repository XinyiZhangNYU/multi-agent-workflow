// Author UI checks for the unified task detail: plan section, run links,
// unassigned runs, per-question requests, next-step source, task hash link.
// Synthetic SQLite, real loopback service, GET-only browser, no live writes.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
const exec=promisify(execFile),here=path.dirname(fileURLToPath(import.meta.url));
const output=path.resolve(process.argv[2]),root=await fs.mkdtemp(path.join(os.tmpdir(),'board-unified-ui-'));
const python=process.env.AGENT_BOARD_PYTHON||(process.platform==='win32'?'py':'python3'),{chromium}=await import(process.env.AGENT_BOARD_PLAYWRIGHT_MODULE);
await fs.mkdir(output,{recursive:true});
const results=[],errors=[],network=[];
let fixture,browser,stop;
async function check(name,fn){
  try{results.push({name,result:'PASS',detail:await fn()??null});console.log('PASS '+name);}
  catch(e){results.push({name,result:'FAIL',detail:e.stack});console.log('FAIL '+name+' '+e.message);}
}
const row=(p,id)=>p.locator('#tasks .task-entry[data-task-id="'+id+'"]');
const ready=p=>p.waitForFunction(()=>document.querySelector('#tasks').getAttribute('aria-busy')==='false');
const planReady=p=>p.waitForFunction(()=>{const n=document.querySelector('#detail-body #plan-content');return n&&n.getAttribute('aria-busy')==='false'&&n.querySelectorAll('.plan-mainline').length===2;});
const noOverflow=p=>p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&[...document.querySelectorAll('#detail-body,#detail-body .plan-mainline')].every(n=>n.scrollWidth<=n.clientWidth+1));
async function page(size){
  const c=await browser.newContext({viewport:size,reducedMotion:'reduce'});
  await c.route('**/*',r=>{
    if(r.request().method()!=='GET'||new URL(r.request().url()).origin!==fixture.url){network.push(r.request().url());return r.abort();}
    return r.continue();
  });
  const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));
  p.on('console',e=>{if(e.type()==='error')errors.push(e.text());});
  return{c,p};
}
async function filter(p,{agent='',cat=''}){
  await p.locator('#clear-filters').click();
  if(agent)await p.locator('#agent').selectOption(agent);
  if(cat)await p.locator('#attention-filter').selectOption(cat);
  return p.locator('#tasks .task-entry').evaluateAll(n=>n.map(x=>x.dataset.taskId).sort());
}
try{
  fixture=JSON.parse((await exec(python,['-B',path.join(here,'unified_fixture.py'),root],{env:{...process.env,PYTHONUTF8:'1'}})).stdout);
  await fs.writeFile(path.join(output,'FIXTURE.json'),JSON.stringify({fixture,root},null,2));
  const original=await(await fetch(fixture.url+'/api/state')).json();
  assert.equal(original.instance_id,fixture.instance_id);
  const q2=fixture.questions.q2;
  browser=await chromium.launch({headless:true,executablePath:process.env.AGENT_BOARD_CHROME||undefined});
  for(const size of [{width:1440,height:900},{width:1280,height:800},{width:390,height:844}]){
    const{c,p}=await page(size);await p.goto(fixture.url+'/?view=list');await ready(p);
    await check(size.width+': requests are listed per question; task count stays per task',async()=>{
      // One inbox row per open question; the original text sits under the expand control.
      assert.equal(await p.locator('#attention-count').innerText(),'2');
      const items=p.locator('#attention .inbox-row[data-task-id="plan-task"]');
      assert.equal(await items.count(),2);
      for(const more of await items.locator('.inbox-more').all())await more.click();
      const text=await items.evaluateAll(n=>n.map(x=>x.innerText).join(' | '));
      assert(text.includes('Question two: keep the old field?'),text);assert(text.includes('Question '+q2.slice(0,8)),text);
      assert(text.includes('Declaration: please confirm the scope')&&text.includes('Declaration, no question ID'),text);
      assert(!text.includes('Question one'),'resolved question is not open: '+text);
      assert.equal(await row(p,'plan-task').locator('.ask-tag').innerText(),'Needs you 2');
      for(const less of await items.locator('.inbox-more').all())await less.click();
    });
    await check(size.width+': cards show one phase line; the next step and plan counts sit in titles',async()=>{
      const phase=async id=>[await row(p,id).locator('.phase').innerText(),await row(p,id).locator('.phase').getAttribute('title')].join(' | ');
      assert((await phase('plan-task')).includes('Branch one next step'));
      assert((await phase('no-plan-task')).includes('Without a plan the run self-report is used'));
      assert(!(await row(p,'plan-task').innerText()).includes('Run self-report, not shown in the overview'));
      assert.match(await row(p,'plan-task').locator('.plan-open').getAttribute('title'),/unassigned runs 3/);
      assert.equal(await row(p,'no-plan-task').locator('.plan-open').count(),0);
    });
    await check(size.width+': the Plan button opens the detail at the plan with links and unassigned runs',async()=>{
      await row(p,'plan-task').locator('.plan-open').click();await planReady(p);
      assert.match(await p.locator('#detail-id').innerText(),/plan-task/);
      assert.equal(await p.locator('#detail-body .detail-next .next-value').innerText(),'Branch one next step');
      assert.match(await p.locator('#detail-body .detail-next .next-source').innerText(),/Source · Plan focus m1\/b1/);
      assert.equal(await p.evaluate(()=>document.body.classList.contains('has-plan')),true);
      const top=await p.evaluate(()=>document.querySelector('#plan-content').getBoundingClientRect().top-document.querySelector('#detail-body').getBoundingClientRect().top);
      assert(Math.abs(top)<=2,'plan at the top of the detail viewport: '+top);
      assert.deepEqual(await p.locator('.plan-mainline').evaluateAll(n=>n.map(x=>x.dataset.mainlineId)),['m1','m2']);
      assert.equal(await p.locator('.plan-mainline.current').getAttribute('data-mainline-id'),'m1');
      assert.equal(await p.locator('.plan-mainline[data-mainline-id="m1"] .plan-links').innerText(),'Linked runs 2 · audits 1');
      assert.equal(await p.locator('.plan-mainline[data-mainline-id="m2"] .plan-links').innerText(),'Linked runs 0');
      assert.match(await p.locator('.plan-heading').innerText(),/Plan owner \(text\) · Plan text owner/);
      await p.locator('details[data-key="plan-more-m1"] > summary').click();
      const folds=p.locator('.plan-mainline[data-mainline-id="m1"] .run-fold');
      assert.deepEqual(await folds.evaluateAll(n=>n.map(x=>x.dataset.runId)),['exec-1','audit-1']);
      assert.match(await folds.nth(1).locator('summary').innerText(),/Audit · AuditAgent .*Branch m1\/b1/);
      assert.match(await folds.nth(0).locator('summary').innerText(),/Execution \(current owner\)/);
      const unassigned=p.locator('#detail-body [data-section="unassigned"]');
      assert.match(await unassigned.locator('h3').innerText(),/Unassigned runs · 3/);
      assert.equal(await unassigned.locator('details[data-key="unassigned-fresh"] > summary').innerText(),'Recently active · 2');
      assert.equal(await unassigned.locator('details[data-key="unassigned-stale"] > summary').innerText(),'Stale or never reported · 1');
      await unassigned.locator('details[data-key="unassigned-fresh"] > summary').click();
      assert.match(await unassigned.locator('.run-fold[data-run-id="peer-2"] > summary').innerText(),/Invalid link: the plan has no m9/);
      assert.match(await unassigned.locator('.run-fold[data-run-id="peer-1"] > summary').innerText(),/Has an open request/);
      assert.equal(await noOverflow(p),true);
      await p.screenshot({path:path.join(output,'unified-plan-'+size.width+'.png'),fullPage:false});
    });
    await check(size.width+': questions, closures and review target in the detail',async()=>{
      const asks=p.locator('#detail-body [data-section="questions"]');
      assert.equal(await asks.locator('h3').innerText(),'Needs you · 2');
      assert.equal(await asks.locator('.question-block').count(),2);
      assert.equal(await asks.locator('.question-block').first().getAttribute('data-question-id'),q2);
      assert.equal(await asks.locator('details[data-key="closed-questions"] > summary').innerText(),'Closed · 1');
      assert.equal(await asks.locator('details[data-key="unmatched-closures"] > summary').innerText(),'Unmatched resolves / withdrawals · 1');
      await asks.locator('details[data-key="unmatched-closures"] > summary').click();
      assert.match(await asks.innerText(),/This task has no such question ID/);
      assert.match(await p.locator('#detail-body').innerText(),/Audit target \(linked\)\s*m1\/b1/);
      assert.equal(await p.locator('#detail-body img').count(),0);
      assert.equal(await noOverflow(p),true);
      if(size.width<1024)assert.equal(await p.locator('#detail').getAttribute('aria-modal'),'true');
      await p.screenshot({path:path.join(output,'unified-questions-'+size.width+'.png'),fullPage:false});
    });
    await check(size.width+': a task without a plan keeps the execution section and the run source',async()=>{
      await p.keyboard.press('Escape');assert(await p.locator('#detail').isHidden());
      await row(p,'no-plan-task').locator('.task-name').click();
      await p.waitForFunction(()=>document.querySelector('#detail-id').textContent.includes('no-plan-task')&&document.querySelector('#detail-body .detail-next'));
      assert.match(await p.locator('#detail-body .next-source').innerText(),/Source · Current execution run self-report · exec-np/);
      assert.equal(await p.locator('#detail-body #plan-content').count(),0);
      assert.equal(await p.locator('#detail-body [data-section="unassigned"]').count(),0);
      assert(await p.locator('#detail-body h3').filter({hasText:/^Execution$/}).count()===1);
      assert.equal(await p.evaluate(()=>document.body.classList.contains('has-plan')),false);
      assert.match(await p.locator('#detail-body').innerText(),/Next step \(self-reported by this run\)\s*Without a plan the run self-report is used/);
      await p.keyboard.press('Escape');
    });
    await c.close();
  }
  const{c,p}=await page({width:1440,height:900});
  await check('Task hash link opens the task detail with its plan',async()=>{
    await p.goto(fixture.url+'/?view=list#plan-task');await ready(p);await planReady(p);
    assert.match(await p.locator('#detail-id').innerText(),/plan-task/);
    assert.equal(new URL(p.url()).hash,'#plan-task');
    assert.equal(await p.locator('#tab-task-board,#task-board-view').count(),0);
  });
  await check('Joint filters count the asking run',async()=>{
    assert.deepEqual(await filter(p,{cat:'human'}),['plan-task']);
    assert.deepEqual(await filter(p,{agent:'PeerAgent',cat:'human'}),['plan-task']);
    assert.deepEqual(await filter(p,{agent:'ExecAgent',cat:'human'}),['plan-task']);
    assert.deepEqual(await filter(p,{agent:'SoloAgent',cat:'human'}),[]);
    await p.locator('#clear-filters').click();
  });
  await check('GET-only same-origin / no browser errors / nothing changed',async()=>{
    assert.deepEqual(network,[]);assert.deepEqual(errors,[]);
    const last=await(await fetch(fixture.url+'/api/state')).json();assert.equal(last.event_count,original.event_count);
  });
  await c.close();
}finally{
  if(browser)await browser.close();
  if(fixture)stop=JSON.parse((await exec(python,['-B',path.join(here,'../board.py'),'--config',path.join(root,'config.json'),'--runtime',path.join(root,'runtime'),'stop'],{env:{...process.env,PYTHONUTF8:'1'}})).stdout);
  await fs.writeFile(path.join(output,'RESULTS.json'),JSON.stringify({scope:'author synthetic unified-detail Chrome checks; not independent audit',fixture,root,stop,results,errors,network,passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length},null,2));
}
console.log(JSON.stringify({passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length,output}));
if(results.some(x=>x.result==='FAIL'))process.exitCode=1;
