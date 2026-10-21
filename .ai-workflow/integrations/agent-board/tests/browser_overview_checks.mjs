// Existing local browser tooling only; all writes go to an owned synthetic instance.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
const exec=promisify(execFile), here=path.dirname(fileURLToPath(import.meta.url));
const python=process.env.AGENT_BOARD_PYTHON||(process.platform==='win32'?'py':'python3'),pythonArgs=python==='py'?['-3','-B']:['-B'];
const evidence=path.resolve(process.argv[2]),cli=path.resolve(here,'../board.py');
const {chromium}=await import(process.env.AGENT_BOARD_PLAYWRIGHT_MODULE);
await fs.mkdir(evidence,{recursive:true});
const root=await fs.mkdtemp(path.join(os.tmpdir(),'agent-board-overview-'));
const args=[...pythonArgs,cli,'--config',path.join(root,'config.json'),'--runtime',path.join(root,'runtime')];
const checks=[],errors=[];
let browser,fixture,stopResult;
const runCLI=async(...tail)=>JSON.parse((await exec(python,[...args,...tail])).stdout);
// Cards are one line now; run, audit and notice details live in the task detail.
const detailText=async(page,id)=>{await page.evaluate(id=>openTask(id,null,false),id);await page.waitForFunction(id=>detailValue?.id===id,id);const text=await page.locator('#detail-body').innerText();await page.locator('#close-detail').click();return text;};
try{
  fixture=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'overview_fixture.py'),root])).stdout);
  browser=await chromium.launch({headless:true,executablePath:process.env.AGENT_BOARD_CHROME||undefined});
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  await context.route('**/*',r=>new URL(r.request().url()).origin===fixture.url?r.continue():r.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(fixture.url+'/?view=list');await page.locator('.task-entry').first().waitFor();
  const card=page.locator('#tasks [data-task-id="reviewed"]');
  assert.equal(await card.locator('.badge').innerText(),'Ended');
  let text=await detailText(page,'reviewed');
  assert.match(text,/Execution · Ended/);assert.equal(text.match(/Audit · In progress/g).length,2);
  assert.match(text,/Matches the current execution candidate/);assert.match(text,/Not the current execution candidate/);
  assert.equal(await page.locator('#attention [data-task-id="reviewed"]').count(),0);
  assert.equal(await page.locator('.metric').count(),0);
  assert.equal(await page.locator('.stream-link[data-stream=""] .nav-meta').count(),0);
  assert.equal(await page.locator('#attention-count').innerText(),'None');
  assert.equal(await page.locator('#attention .inbox-row').count(),0);
  assert.match(await page.locator('#attention').innerText(),/Nothing needs you\./);
  checks.push('two running reviews visible beside ended execution; sidebar omits audit metric; normal audit not human request');
  for(const id of ['input','orphan']){
    assert.equal(await page.locator('#attention [data-task-id="'+id+'"]').count(),0);
    const detail=await detailText(page,id);
    assert.match(detail,/Waiting for input; the item is not stated/);assert.match(detail,/Who must give input is not declared/);
    assert.equal(await page.locator('#tasks [data-task-id="'+id+'"] .notice').count(),0);
  }
  assert.match(await detailText(page,'orphan'),/Audit target binding unknown/);
  assert.equal(await page.evaluate(()=>window.XSS),undefined);assert.equal(await card.locator('img,script').count(),0);
  checks.push('Execution/audit waiting_input without a declaration stays undeclared, not human queue; unknown candidate; literal injection');
  await page.screenshot({path:path.join(evidence,'overview-1440.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('#status').selectOption('running');assert.equal(await page.locator('.task-entry').count(),1);
  await page.locator('#agent').selectOption('Reviewer-one');assert.equal(await page.locator('.task-entry').count(),1);
  await page.locator('#status').selectOption('ended');assert.equal(await page.locator('.task-entry').count(),0);
  await page.locator('#agent').selectOption('Implementer');assert.equal(await page.locator('.task-entry').count(),1);
  await page.locator('#status').selectOption('');await page.locator('#agent').selectOption('');
  await page.locator('.stream-link[data-stream="Implementation"]').click();assert.equal(await page.locator('.task-entry').count(),1);
  await page.locator('.stream-link[data-stream=""]').click();
  for(let i=0;i<3;i++){await page.locator('#status').selectOption('unknown');await page.locator('#status').selectOption('');}
  assert.equal(await page.locator('#tasks [data-task-id="orphan"]').count(),1);
  assert.equal(await page.evaluate(()=>state.tasks.find(t=>t.id==='orphan').overview_runs.length),1);
  checks.push('status/agent filters match same run; workstream filter; repeated rendering does not mutate summary');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(evidence,'overview-390.png'),fullPage:true});
  await card.getByRole('button').first().click();await page.locator('#detail-body .detail-section').first().waitFor();
  assert.match(await page.locator('#detail').innerText(),/audit-one/);
  assert.match(await page.locator('#detail').innerText(),/None recorded; no independent audit/);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(evidence,'detail-390.png'),fullPage:true});
  await page.locator('#close-detail').click();
  checks.push('1440/390 actual rendering and details without overflow; no acceptance invented');
  assert.equal((await runCLI('report','reviewed','audit-one','--status','waiting_input','--human-input','required')).status,'RECORDED');
  // A flag without any text is a grey tag on the task, not an inbox row.
  await page.locator('#tasks [data-task-id="reviewed"] .declaration-tag[data-run-id="audit-one"]').waitFor({timeout:16000});
  assert.equal(await page.locator('#attention [data-task-id="reviewed"]').count(),0);
  checks.push('real isolated CLI report reaches the task through automatic page refresh; untold flag is a tag, not inbox');
  assert.equal((await runCLI('report','reviewed','audit-two','--human-input','required','--decision','Choose B')).status,'RECORDED');
  await page.locator('#refresh').click();await page.waitForFunction(()=>openQuestions(state.tasks.find(t=>t.id==='reviewed')).length===2);
  assert.equal(await page.locator('.stream-link[data-stream=""] .nav-meta').innerText(),'Needs you 1');
  assert.match(await page.locator('#attention .inbox-row[data-run-id="audit-two"]').innerText(),/Choose B/);
  assert.equal(await page.locator('#tasks [data-task-id="reviewed"] .ask-tag').innerText(),'Needs you 1');
  await page.locator('#attention-filter').selectOption('human');await page.locator('#agent').selectOption('Implementer');
  assert.equal(await page.locator('.task-entry').count(),0);
  await page.locator('#agent').selectOption('Reviewer-two');assert.equal(await page.locator('.task-entry').count(),1);
  await page.locator('#clear-filters').click();
  checks.push('a request with text is one inbox row, the untold flag a tag; human/status/agent filters match same run');
  for(const run of ['audit-one','audit-two'])assert.equal((await runCLI('report','reviewed',run,'--status','ended')).status,'RECORDED');
  await page.locator('#refresh').click();await page.waitForFunction(()=>state.tasks.find(t=>t.id==='reviewed').overview_runs.filter(r=>r.role==='audit'&&r.state.status==='ended').length===2);
  assert.equal(await page.locator('#attention [data-task-id="reviewed"]').count(),1);
  // The asking run ended: the request folds under "Possibly outdated", never closes by itself.
  assert.equal(await page.locator('#attention .stale-inbox [data-task-id="reviewed"]').count(),1);
  assert.equal(await page.locator('#tasks [data-task-id="reviewed"] .declaration-tag').count(),1);
  checks.push('ending review does not silently resolve explicit requests; it folds as possibly stale');
  for(const run of ['audit-one','audit-two'])assert.equal((await runCLI('report','reviewed',run,'--human-input','not_required')).status,'RECORDED');
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>!openQuestions(state.tasks.find(t=>t.id==='reviewed')).length&&!document.querySelector('#attention [data-task-id="reviewed"]'));
  assert.equal(await page.locator('#tasks [data-task-id="reviewed"] .declaration-tag').count(),0);
  const t=await runCLI('show','reviewed');
  assert.equal(t.current_run,'impl');assert.equal(t.runs.length,3);assert.deepEqual(t.deliveries,[]);
  checks.push('ended review disappears from active summary but remains in history; no ownership or acceptance transition');
  // The same ownerless task can run normally, need input, then finish.
  assert.equal((await runCLI('report','orphan','orphan-review','--status','running')).status,'RECORDED');
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>!state.tasks.find(t=>t.id==='orphan').needs_attention);
  assert.equal(await page.locator('#attention [data-task-id="orphan"]').count(),0);
  assert.deepEqual(await page.evaluate(()=>reasons(state.tasks.find(t=>t.id==='orphan'))),[]);
  const orphan=page.locator('#tasks [data-task-id="orphan"]');
  assert.equal(await orphan.locator('.badge').innerText(),'Unknown');
  text=await detailText(page,'orphan');
  assert.match(text,/Audit · In progress/);assert.doesNotMatch(text,/ · stale|Current execution activity unknown/);
  // Candidate binding may be unknown and visibly flagged; that is not stale/attention.
  assert.match(text,/Audit target binding unknown/);
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(evidence,'audit-only-'+width+'.png'),fullPage:true});
  }
  await page.locator('#agent').selectOption('Reviewer-orphan');await page.locator('#status').selectOption('running');
  assert.equal(await page.locator('.task-entry').count(),1);
  await page.locator('#agent').selectOption('');await page.locator('#status').selectOption('');
  checks.push('fresh audit-only visible/filterable at 1440/390 without task attention, stale notice or missing-owner reason');
  assert.equal((await runCLI('report','orphan','orphan-review','--status','waiting_input','--human-input','required')).status,'RECORDED');
  await page.locator('#refresh').click();
  await orphan.locator('.declaration-tag[data-run-id="orphan-review"]').waitFor();
  assert.equal(await page.locator('#attention [data-task-id="orphan"]').count(),0);
  assert.doesNotMatch(await orphan.innerText(),/Current execution activity unknown/);
  assert.equal((await runCLI('report','orphan','orphan-review','--status','ended')).status,'RECORDED');
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>state.tasks.find(t=>t.id==='orphan').overview_runs[0].state.status==='ended');
  assert.equal(await orphan.locator('.declaration-tag').count(),1);
  assert.equal((await runCLI('report','orphan','orphan-review','--human-input','not_required')).status,'RECORDED');
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>state.tasks.find(t=>t.id==='orphan').overview_runs.length===0);
  assert.equal(await page.locator('#tasks [data-task-id="orphan"] .declaration-tag').count(),0);
  assert.match(await detailText(page,'orphan'),/no open audit/);
  const ended=await runCLI('show','orphan');
  assert.equal(ended.current_run,null);assert.deepEqual(ended.deliveries,[]);assert.equal(ended.runs.length,1);
  checks.push('audit-only explicit request persists until withdrawn; unknown quality stays local, no acceptance or ownership');
  await page.setViewportSize({width:1440,height:900});
  // A delayed old detail must not replace the newer selection.
  let releaseOld,oldStarted,holdOld=true;
  const oldIntercepted=new Promise(resolve=>oldStarted=resolve);
  const oldRoute=async route=>{
    if(holdOld){holdOld=false;oldStarted();await new Promise(resolve=>releaseOld=resolve);}
    await route.continue();
  };
  await page.route('**/api/task?id=reviewed',oldRoute);
  // Ended tasks fold per board; the user opens the fold first.
  await page.locator('#tasks details.ended-group:has([data-task-id="reviewed"]) > summary').click();
  await page.locator('#tasks [data-task-id="reviewed"] button').first().click();
  await oldIntercepted;
  await page.locator('#tasks [data-task-id="input"] button').first().click();
  await page.waitForFunction(()=>selected==='input'&&document.querySelector('#detail-id')?.textContent.includes('input'));
  const oldResponse=page.waitForResponse(r=>r.url().includes('/api/task?id=reviewed'));
  releaseOld();await oldResponse;await page.waitForTimeout(100);
  assert.match(await page.locator('#detail-id').innerText(),/^input/);
  await page.unroute('**/api/task?id=reviewed',oldRoute);
  checks.push('delayed old detail response cannot overwrite a newer selected task');
  let releaseClosed,closeStarted,holdClose=true;
  const closeIntercepted=new Promise(resolve=>closeStarted=resolve);
  const closeRoute=async route=>{
    if(holdClose){holdClose=false;closeStarted();await new Promise(resolve=>releaseClosed=resolve);}
    await route.continue();
  };
  await page.route('**/api/task?id=input',closeRoute);
  await page.locator('#tasks [data-task-id="input"] button').first().click();
  await closeIntercepted;
  await page.locator('#detail button').first().click();
  const closedResponse=page.waitForResponse(r=>r.url().includes('/api/task?id=input'));
  releaseClosed();await closedResponse;await page.waitForTimeout(100);
  assert.equal(await page.locator('#detail').isHidden(),true);
  await page.unroute('**/api/task?id=input',closeRoute);
  checks.push('closing detail invalidates an in-flight detail response');
  assert.deepEqual(errors,[]);checks.push('no browser JavaScript errors');
  await fs.writeFile(path.join(evidence,'browser-results.json'),JSON.stringify({tested_at:new Date().toISOString(),checks,errors,viewports:[1440,390],scope:'author synthetic isolated Chrome test, not independent or real audit-client acceptance'},null,2));
  console.log(JSON.stringify({passed:checks.length,checks}));
}finally{
  if(browser)await browser.close();
  if(fixture){
    stopResult=await runCLI('stop');
    await fs.writeFile(path.join(evidence,'fixture-lifecycle.json'),JSON.stringify({root,url:fixture.url,start:fixture,status:stopResult,finished_at:new Date().toISOString(),note:'Owned temp runtime retained outside evidence manifest; credential not exported.'},null,2));
    assert.equal(stopResult.status,'STOPPED');
  }
}
