// Author regression: synthetic SQLite, real loopback service, GET-only browser.
// No running board database, credential or business task is read or written.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
const exec=promisify(execFile),here=path.dirname(fileURLToPath(import.meta.url));
const python=process.env.AGENT_BOARD_PYTHON||(process.platform==='win32'?'py':'python3'),pythonArgs=python==='py'?['-3','-B']:['-B'];
const output=path.resolve(process.argv[2]),root=await fs.mkdtemp(path.join(os.tmpdir(),'board-ttl-ui-'));
const {chromium}=await import(process.env.AGENT_BOARD_PLAYWRIGHT_MODULE);
await fs.mkdir(output,{recursive:true});
const results=[],errors=[],network=[];
let fixture,browser,stop;
async function check(name,fn){
  try{results.push({name,result:'PASS',detail:await fn()??null});console.log('PASS '+name);}
  catch(e){results.push({name,result:'FAIL',detail:e.stack});console.log('FAIL '+name+' '+e.message);}
}
const rows=p=>p.locator('#tasks .task-entry');
const row=(p,id)=>p.locator('#tasks .task-entry[data-task-id="'+id+'"]');
const nav=(p,name)=>p.locator('.stream-link[data-stream="'+name+'"]');
const refresh=p=>p.evaluate(()=>refresh());
const ready=p=>p.waitForFunction(()=>document.querySelector('#tasks').getAttribute('aria-busy')==='false');
async function page(size={width:1440,height:900}){
  const c=await browser.newContext({viewport:size,reducedMotion:'reduce'});
  await c.route('**/*',r=>{
    if(r.request().method()!=='GET'||new URL(r.request().url()).origin!==fixture.url){network.push(r.request().url());return r.abort();}
    return r.continue();
  });
  const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));
  p.on('console',e=>{if(e.type()==='error'&&!/503|404/.test(e.text()))errors.push(e.text());});
  await p.goto(fixture.url+'/?view=list');await ready(p);return{c,p};
}
try{
  fixture=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'ttl_fixture.py'),root])).stdout);
  await fs.writeFile(path.join(output,'FIXTURE.json'),JSON.stringify({fixture,root},null,2));
  const original=await(await fetch(fixture.url+'/api/state')).json();
  assert.equal(original.service,'agent-board-v1');assert.equal(original.instance_id,fixture.instance_id);
  assert.equal(original.tasks.length,44);assert.equal(original.display_ttl_seconds,604800);
  assert.equal(original.tasks.filter(t=>t.display_expired).length,3);
  browser=await chromium.launch({headless:true,executablePath:process.env.AGENT_BOARD_CHROME||undefined});
  for(const size of [{width:1440,height:900},{width:1280,height:800},{width:390,height:844}]){
    const{c,p}=await page(size);
    await check(size.width+': default visibility / global counts / protected requests',async()=>{
      assert.equal(await rows(p).count(),41);assert.equal(await p.locator('#old-count').innerText(),'(3)');
      // The sidebar shows only "Needs you N"; task totals sit in the list heading.
      assert.equal(await nav(p,'').locator('.nav-meta').innerText(),'Needs you 3');assert.equal(await nav(p,'Old stream').count(),0);
      assert.equal(await p.locator('#attention .inbox-row').count(),3);
      for(const id of ['old-human','old-audit-human','unknown-run','unknown-doc','renewed-audit'])assert.equal(await row(p,id).count(),1);
      assert.equal(await row(p,'old-audit-human').locator('.ask-tag').innerText(),'Needs you 1');
      // Ten-day-old requests fold under "Possibly outdated"; still listed, never closed.
      assert((await p.locator('#attention .stale-inbox .inbox-row[data-task-id="old-audit-human"] .inbox-q').textContent()).includes('Please confirm: keep option'));
      assert((await p.locator('#visible-count').innerText()).includes('all: 41'));
      assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await p.screenshot({path:path.join(output,'ttl-default-'+size.width+'.png')});
    });
    await check(size.width+': old toggle / workstream / status and agent filter / evidence',async()=>{
      await p.locator('#show-old').check();assert.equal(await rows(p).count(),44);
      await nav(p,'Old stream').click();assert.equal(await rows(p).count(),3);
      assert.equal(await p.locator('#tasks .old-label').count(),3);
      await p.locator('#status').selectOption('running');await p.locator('#agent').selectOption('TTL-audit');
      assert.equal(await rows(p).count(),1);assert.equal(await row(p,'expired-audit').count(),1);
      await p.locator('#clear-filters').click();await p.locator('#search').fill('expired doc');
      assert.equal(await rows(p).count(),1);
      await row(p,'expired-doc').locator('.task-name').click();await p.locator('#detail-body .detail-section').first().waitFor();
      assert((await p.locator('#detail-body').innerText()).includes('All runs'));
      assert(await p.locator('#detail-body .copy-path').count()>=1);
      await p.keyboard.press('Escape');assert(await p.locator('#detail').isHidden());
      assert.equal(await p.locator('#search').inputValue(),'expired doc');
      await p.locator('#search').fill('');await p.locator('#show-old').uncheck();
      assert.equal(await rows(p).count(),0);assert.equal(await p.locator('#workstream').inputValue(),'Old stream');
      assert.equal(await nav(p,'Old stream').innerText(),'Old stream');assert(await p.locator('#tasks .empty').isVisible());
      await p.locator('#show-old').check();assert.equal(await rows(p).count(),3);
      await p.screenshot({path:path.join(output,'ttl-old-'+size.width+'.png')});
      assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    });
    await check(size.width+': refresh preserves TTL choice and keyboard focus',async()=>{
      await p.locator('#search').fill('expired');await p.locator('#search').focus();await p.keyboard.press('Shift+Tab');
      assert.equal(await p.evaluate(()=>document.activeElement.id),'show-old');
      await p.waitForTimeout(1100);
      const before=await p.locator('#read-time').innerText();await refresh(p);
      await p.waitForFunction(old=>document.querySelector('#read-time').textContent!==old,before);
      assert(await p.locator('#show-old').isChecked());assert.equal(await p.locator('#search').inputValue(),'expired');
      assert.equal(await p.locator('#workstream').inputValue(),'Old stream');
      assert.equal(await p.evaluate(()=>document.activeElement.id),'show-old');
      assert.equal(await p.locator('#show-old').evaluate(n=>getComputedStyle(n).outlineStyle),'solid');
      assert.equal(await p.locator('.heading-tools').evaluate(n=>getComputedStyle(n).animationName),'none');
    });
    await c.close();
  }
  const{c,p}=await page();
  await check('Time expiry without event change / valid update reappears / focus and selected stream retained',async()=>{
    await nav(p,'Data export').click();await p.locator('#search').fill('doc-export');await p.locator('#search').focus();
    assert.equal(await row(p,'doc-export').count(),1);
    await p.route('**/api/state',async r=>{const response=await r.fetch(),body=await response.json();body.tasks.find(t=>t.id==='doc-export').display_expired=true;await r.fulfill({response,json:body});});
    await refresh(p);await p.waitForFunction(()=>!document.querySelector('#tasks [data-task-id="doc-export"]'));
    assert.equal(await p.locator('#search').inputValue(),'doc-export');assert.equal(await p.evaluate(()=>document.activeElement.id),'search');
    assert.equal(await p.locator('#old-count').innerText(),'(4)');
    await p.unroute('**/api/state');await refresh(p);await row(p,'doc-export').waitFor();
    assert.equal(await p.locator('#old-count').innerText(),'(3)');
  });
  await check('Protected human request remains even with inconsistent expiration flag',async()=>{
    await p.route('**/api/state',async r=>{const response=await r.fetch(),body=await response.json();for(const t of body.tasks)if(t.human_input_required)t.display_expired=true;await r.fulfill({response,json:body});});
    await nav(p,'').click();await p.locator('#clear-filters').click();await refresh(p);
    await p.waitForFunction(()=>document.querySelector('#tasks [data-task-id="old-human"]'));
    assert.equal(await rows(p).count(),41);assert.equal(await p.locator('#attention .inbox-row').count(),3);
    await p.unroute('**/api/state');
  });
  await check('Old detail stays open across refresh; hidden tasks still support direct link',async()=>{
    // Same-document fragment navigation alone does not rerun startup. Check a
    // real page load, as when opening a copied task deep link in the browser.
    await p.goto(fixture.url+'/?view=list#expired-doc');await p.reload();await ready(p);await p.locator('#detail-body .detail-section').first().waitFor();
    assert.equal(await p.locator('#detail-id').innerText(),'expired-doc · Old stream');assert.equal(await row(p,'expired-doc').count(),0);
    await refresh(p);assert(await p.locator('#detail').isVisible());await p.keyboard.press('Escape');
  });
  await check('Failure retains old toggle, filters and previously read tasks',async()=>{
    await p.locator('#show-old').check();await p.locator('#search').fill('expired');const before=await p.locator('#tasks').innerText();
    await p.route('**/api/state',r=>r.fulfill({status:503,json:{error:'owned fixture error'}}));await refresh(p);
    await p.waitForFunction(()=>!document.querySelector('#error').hidden);
    assert.equal(await p.locator('#tasks').innerText(),before);assert(await p.locator('#show-old').isChecked());
    assert.equal(await p.locator('#search').inputValue(),'expired');await p.unroute('**/api/state');
  });
  await check('Only old tasks / no tasks / absent TTL fields compatibility',async()=>{
    await p.locator('#show-old').uncheck();await p.locator('#clear-filters').click();
    await p.route('**/api/state',async r=>{const response=await r.fetch(),body=await response.json();body.tasks=body.tasks.filter(t=>t.display_expired);await r.fulfill({response,json:body});});
    await refresh(p);await p.waitForFunction(()=>document.querySelector('#tasks').textContent.includes('No recent tasks'));
    await p.locator('#show-old').check();assert.equal(await rows(p).count(),3);await p.unroute('**/api/state');
    await p.route('**/api/state',async r=>{const response=await r.fetch(),body=await response.json();body.tasks=[];await r.fulfill({response,json:body});});
    await refresh(p);await p.waitForFunction(()=>document.querySelector('#tasks').textContent.includes('No tasks registered yet'));await p.unroute('**/api/state');
    await p.locator('#show-old').uncheck();
    await p.route('**/api/state',async r=>{const response=await r.fetch(),body=await response.json();for(const t of body.tasks)delete t.display_expired;await r.fulfill({response,json:body});});
    await refresh(p);await p.waitForFunction(()=>document.querySelectorAll('#tasks .task-entry').length===44);
    assert.equal(await p.locator('#old-count').innerText(),'');await p.unroute('**/api/state');
  });
  await check('GET-only same-origin / no browser errors / no history deleted',async()=>{
    assert.deepEqual(network,[]);assert.deepEqual(errors,[]);
    const last=await(await fetch(fixture.url+'/api/state')).json();assert.equal(last.tasks.length,44);assert.equal(last.event_count,original.event_count);
  });
  await c.close();
}finally{
  if(browser)await browser.close();
  if(fixture)stop=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'../board.py'),'--config',path.join(root,'config.json'),'--runtime',path.join(root,'stress'),'stop'])).stdout);
  await fs.writeFile(path.join(output,'RESULTS.json'),JSON.stringify({scope:'author synthetic TTL Chrome checks; not independent audit',fixture,root,stop,results,errors,network,passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length},null,2));
}
console.log(JSON.stringify({passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length,output}));
if(results.some(x=>x.result==='FAIL'))process.exitCode=1;
