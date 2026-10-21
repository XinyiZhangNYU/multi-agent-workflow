// Brief / agents / feed views. Existing local browser tooling only; all writes go to an owned synthetic instance.
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
const root=await fs.mkdtemp(path.join(os.tmpdir(),'agent-board-views-'));
const args=[...pythonArgs,cli,'--config',path.join(root,'config.json'),'--runtime',path.join(root,'runtime')];
const checks=[],errors=[];
let browser,fixture,stopResult;
const runCLI=async(...tail)=>JSON.parse((await exec(python,[...args,...tail])).stdout);
const shown=page=>page.evaluate(()=>[...document.querySelectorAll('#views > [data-view]')].filter(n=>!n.hidden).map(n=>n.dataset.view));
const noOverflow=page=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
// Deliberately dimmed stale/quiet items are exempt; everything else must reach 4.5:1.
const contrast=page=>page.evaluate(()=>{
  const rgb=v=>(v.match(/[\d.]+/g)||[]).map(Number),lum=c=>c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
  function bg(n){let col=[255,255,255],chain=[];while(n){chain.unshift(n);n=n.parentElement;}for(const e of chain){const v=rgb(getComputedStyle(e).backgroundColor),a=v.length<4?1:v[3];col=col.map((x,i)=>v[i]*a+x*(1-a));}return col;}
  const values=[...document.querySelectorAll('body *')].filter(n=>!n.children.length&&n.textContent.trim()&&n.getClientRects().length&&!['OPTION','SCRIPT','STYLE'].includes(n.tagName)&&getComputedStyle(n).opacity==='1'&&!n.closest('.quiet,.stale')).map(n=>{const style=getComputedStyle(n),a=lum(rgb(style.color)),b=lum(bg(n));return{text:n.textContent.slice(0,25),ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
  return{checked:values.length,failures:values.filter(x=>x.ratio<4.5)};
});
try{
  fixture=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'views_fixture.py'),root])).stdout);
  browser=await chromium.launch({headless:true,executablePath:process.env.AGENT_BOARD_CHROME||undefined});
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route('**/*',r=>new URL(r.request().url()).origin===fixture.url?r.continue():r.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(fixture.url+'/');await page.locator('#brief .view-head h1').waitFor();
  assert.deepEqual(await shown(page),['brief']);
  assert.equal(await page.locator('.view-tab[aria-current=true]').getAttribute('data-view'),'brief');
  assert.equal(new URL(page.url()).searchParams.get('view'),'brief');
  checks.push('V01 default view is the brief; only its page is visible and its tab is current');
  const brief=page.locator('#brief');
  assert.equal(await brief.locator('.view-head h1').innerText(),'1 waiting for your decision, 2 moving');
  assert.equal(await page.locator('#tab-count-brief').innerText(),'1');
  const need=brief.locator('.need[data-task-id="asked"]');
  assert.match(await need.locator('.need-q').innerText(),/^<script>window\.XSS=2<\/script>Use approach A or B\?$/);
  const needText=await need.innerText();
  assert.match(needText,/Options: A \/ B \(reply to the agent in its session\)/);assert.match(needText,/If unanswered: Default to A/);assert.match(needText,/Claude-Code/);
  assert.equal(await brief.locator('img,script').count(),0);assert.equal(await page.evaluate(()=>window.XSS),undefined);
  assert.match(await brief.innerText(),/Moving · 2/);assert.match(await brief.innerText(),/Recently finished · 1/);assert.match(await brief.innerText(),/Silent for over two days · 1/);
  assert.equal(await brief.locator('details .line[data-task-id="quiet"]').count(),1);
  assert.equal(await brief.locator('.line[data-task-id="ancient"]').count(),0);
  assert.match(await brief.innerText(),/1 more old task is past the display period/);
  checks.push('V02 brief: one fresh request card (options, policy, literal text), moving/done/quiet groups, old task only counted');
  await brief.locator('.line[data-task-id="moving"] .line-what').click();
  await page.waitForFunction(()=>detailValue?.id==='moving');assert.match(await page.locator('#detail-id').innerText(),/^moving/);
  await page.locator('#close-detail').click();
  await need.getByRole('button',{name:'Details'}).click();await page.waitForFunction(()=>detailValue?.id==='asked');
  await page.locator('#close-detail').click();
  checks.push('V03 brief lines and request cards open the task detail');
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});assert.equal(await noOverflow(page),true);
    await page.screenshot({path:path.join(evidence,'brief-'+width+'.png'),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1000});
  let c=await contrast(page);assert.equal(c.failures.length,0,JSON.stringify(c.failures));

  await page.locator('.view-tab[data-view="agents"]').click();
  assert.deepEqual(await shown(page),['agents']);assert.equal(new URL(page.url()).searchParams.get('view'),'agents');
  assert.equal(await page.evaluate(()=>localStorage.getItem('agent-board-view')),'agents');
  const agents=page.locator('#agents');
  assert.equal(await agents.locator('.view-head h1').innerText(),'2 runs working');
  assert.equal(await page.locator('#tab-count-agents').innerText(),'2');
  assert.deepEqual((await agents.locator('.agent-grid').first().locator('.agent-card').evaluateAll(n=>n.map(x=>x.dataset.runId))).sort(),['asker','worker']);
  assert.match(await agents.locator('.agent-card[data-run-id="asker"] .agent-ask').innerText(),/^Asks you: <script>window\.XSS=2<\/script>Use approach A or B\?/);
  assert.equal(await agents.locator('.agent-card[data-run-id="worker"] .avatar').getAttribute('class'),'avatar codex');
  const idle=agents.locator('details.agents-fold');
  assert.match(await idle.locator('summary').innerText(),/Registered but silent for over 24 h · 1/);
  assert.equal(await idle.locator('.agent-card.quiet[data-run-id="sleeper"]').count(),1);
  assert.equal(await agents.locator('[data-run-id="finisher"],[data-run-id="elder"]').count(),0);
  assert.equal(await agents.locator('img,script').count(),0);
  await agents.locator('.agent-card[data-run-id="worker"]').click();await page.waitForFunction(()=>detailValue?.id==='moving');
  await page.locator('#close-detail').click();
  checks.push('V04 agents: live runs as cards (ask shown literally), stale run folded, ended/old runs absent, card opens detail');
  await page.goto(fixture.url+'/');await page.locator('#agents .agent-card').first().waitFor();
  assert.deepEqual(await shown(page),['agents']);
  await page.goto(fixture.url+'/?view=list');await page.locator('#tasks .task-entry').first().waitFor();
  assert.deepEqual(await shown(page),['list']);assert.equal(await page.evaluate(()=>localStorage.getItem('agent-board-view')),'agents');
  await page.locator('.view-tab[data-view="agents"]').click();
  checks.push('V05 tab choice is in ?view= and remembered; an explicit ?view= wins without overwriting the memory');
  await idle.locator('summary').click();
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});assert.equal(await noOverflow(page),true);
    await page.screenshot({path:path.join(evidence,'agents-'+width+'.png'),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1000});
  c=await contrast(page);assert.equal(c.failures.length,0,JSON.stringify(c.failures));

  await page.locator('.view-tab[data-view="feed"]').click();
  const feed=page.locator('#feed');await feed.locator('.ev').first().waitFor();
  assert.deepEqual(await shown(page),['feed']);
  assert.equal(await feed.locator('.feed-mark').count(),0);
  assert.equal(await feed.locator('.ev').first().getAttribute('data-op'),'ask');
  assert.match(await feed.locator('.ev').first().innerText(),/Asked: <script>window\.XSS=2<\/script>Use approach A or B\?/);
  assert.match(await feed.locator('.ev[data-task-id="moving"][data-op="progress"]').innerText(),/Writing tests/);
  assert.doesNotMatch(await feed.innerText(),/second line is hidden/);
  assert.equal(await feed.locator('.ev[data-op="run"],.ev[data-op="task"]').count(),0);
  assert.equal(await feed.locator('h2.feed-day').count()>=2,true);
  assert.equal(await feed.locator('img,script').count(),0);assert.equal(await page.evaluate(()=>window.XSS),undefined);
  checks.push('V06 feed: newest report first, first line only, no registrations, grouped by display-zone day, literal text');
  await page.locator('#feed-filters .chip[data-feed="ask"]').click();
  assert.deepEqual([...new Set(await feed.locator('.ev').evaluateAll(n=>n.map(x=>x.dataset.op)))],['ask']);
  await page.locator('#feed-filters .chip[data-feed="done"]').click();
  assert.deepEqual((await feed.locator('.ev').evaluateAll(n=>n.map(x=>x.dataset.taskId))).sort(),['ancient','done']);
  await page.locator('#feed-filters .chip[data-feed="delivery"]').click();
  assert.match(await feed.innerText(),/No matching reports/);
  await page.locator('#feed-filters .chip[data-feed="all"]').click();
  assert.equal(await page.locator('#feed-filters .chip[aria-pressed=true]').getAttribute('data-feed'),'all');
  await feed.locator('.ev[data-task-id="done"] .link').first().click();await page.waitForFunction(()=>detailValue?.id==='done');
  await page.locator('#close-detail').click();
  checks.push('V07 feed filters (requests / done / delivery / all) and task title opens detail');
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});assert.equal(await noOverflow(page),true);
    await page.screenshot({path:path.join(evidence,'feed-'+width+'.png'),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1000});
  c=await contrast(page);assert.equal(c.failures.length,0,JSON.stringify(c.failures));
  // A report that arrives while the feed is open counts as seen; one that arrives elsewhere is new.
  assert.equal((await runCLI('report','moving','worker','--phase','Reported while open','--human-input','not_required')).status,'RECORDED');
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#feed .ev')?.textContent.includes('Reported while open'));
  await page.locator('.view-tab[data-view="brief"]').click();
  assert.equal((await runCLI('report','moving','worker','--phase','New phase reported')).status,'RECORDED');
  await page.locator('#refresh').click();await page.waitForFunction(()=>state.tasks.find(t=>t.id==='moving').state.phase==='New phase reported');
  await page.locator('.view-tab[data-view="feed"]').click();
  await page.locator('#feed .feed-mark').waitFor();
  assert.match(await feed.locator('.ev').first().innerText(),/New phase reported/);
  assert.equal(await page.evaluate(()=>{const m=document.querySelector('#feed .feed-mark');return [...document.querySelectorAll('#feed .ev')].filter(e=>e.compareDocumentPosition(m)&Node.DOCUMENT_POSITION_FOLLOWING).length;}),1);
  assert.equal(await feed.locator('.ev').first().getAttribute('data-task-id'),'moving');
  checks.push('V08 real CLI report reaches the open feed on refresh; reopening the feed marks what is new since last visit');

  await page.locator('.view-tab[data-view="brief"]').click();
  const close=need.locator('.close-q');
  await close.click();assert.equal(await close.innerText(),'Confirm close?');
  assert.equal(await brief.locator('.need').count(),1);
  const posted=page.waitForResponse(r=>r.url().endsWith('/api/user-close')&&r.request().method()==='POST');
  await close.click();assert.equal((await posted).status(),200);
  await page.waitForFunction(()=>!document.querySelector('#brief .need'));
  assert.match(await brief.locator('.view-head h1').innerText(),/^Nothing new waiting for your decision, 2 moving$/);
  assert.equal(await page.locator('#tab-count-brief').innerText(),'');
  assert.match(await page.locator('.agent-card[data-run-id="asker"] .agent-doing').innerText(),/Comparing two approaches/);
  const shownTask=await runCLI('show','asked');
  assert.equal(shownTask.current_run,'asker');assert.deepEqual(shownTask.deliveries,[]);
  checks.push('V09 brief "Handled" needs two clicks, POSTs user-close, removes the card everywhere; no ownership or acceptance change');
  await page.locator('.view-tab[data-view="feed"]').click();
  await page.waitForFunction(()=>document.querySelector('#feed .ev')?.dataset.op==='user_close');
  assert.match(await feed.locator('.ev').first().innerText(),/You marked it handled on the page/);
  checks.push('V10 the user close shows in the feed');
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
