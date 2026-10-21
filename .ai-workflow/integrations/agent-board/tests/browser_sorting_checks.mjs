// Author regression: owned fixture, random loopback port, GET-only browser.
// Response overrides are synthetic presentation cases, never live board writes.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
const exec=promisify(execFile),here=path.dirname(fileURLToPath(import.meta.url));
const output=path.resolve(process.argv[2]);
const root=await fs.mkdtemp(path.join(os.tmpdir(),'board-sorting-ui-'));
const python=process.env.AGENT_BOARD_PYTHON||(process.platform==='win32'?'py':'python3'),pythonArgs=python==='py'?['-3','-B']:['-B'];
const {chromium}=await import(process.env.AGENT_BOARD_PLAYWRIGHT_MODULE);
await fs.mkdir(output,{recursive:true});
const results=[],errors=[],blocked=[];
let fixture,browser,body;
async function check(name,fn){
  try{results.push({name,result:'PASS',detail:await fn()??null});console.log('PASS '+name);}
  catch(e){results.push({name,result:'FAIL',detail:e.stack});console.log('FAIL '+name+' '+e.message);}
}
const epoch=Date.now()-300000,at=minute=>new Date(epoch+minute*60000).toISOString();
const task=id=>body.tasks.find(t=>t.id===id);
const row=(p,id)=>p.locator('.task-entry[data-task-id="'+id+'"]');
const nav=p=>p.locator('#stream-nav [data-stream]').evaluateAll(ns=>ns.map(n=>n.dataset.stream).filter(Boolean));
const groups=p=>p.locator('#tasks .task-group').evaluateAll(ns=>ns.map(n=>n.dataset.stream));
const refresh=p=>p.evaluate(()=>refresh());
const clear=p=>p.locator('#clear-filters').click();
async function ready(p){await p.waitForFunction(()=>document.querySelector('#tasks').getAttribute('aria-busy')==='false');}
async function context(size={width:1440,height:900}){
  const c=await browser.newContext({viewport:size,reducedMotion:'reduce'});
  await c.route('**/*',r=>{
    if(r.request().method()!=='GET'||new URL(r.request().url()).origin!==fixture.url){blocked.push(r.request().url());return r.abort();}
    return r.continue();
  });
  await c.route('**/api/state',r=>r.fulfill({json:structuredClone(body)}));
  const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));
  p.on('console',e=>{if(e.type()==='error'&&!/503/.test(e.text()))errors.push(e.text());});
  await p.goto(fixture.url+'/?view=list');await ready(p);return{c,p};
}
try{
  fixture=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'workstream_fixture.py'),root],{env:{...process.env,PYTHONUTF8:'1'}})).stdout);
  body=await(await fetch(fixture.url+'/api/state')).json();assert.equal(body.tasks.length,36);
  for(const t of body.tasks){
    if(t.workstream==='Backend')t.workstream='Cache layer';
    t.display_updated_at=at(-30);t.display_expired=false;
  }
  for(const [id,minute]of Object.entries({'public-docs':-1,'release-feed':-2,'doc-export':-3,'board-layout':-4,nightly:-5}))task(id).display_updated_at=at(minute);
  task('identity').display_updated_at=null;
  browser=await chromium.launch({headless:true,executablePath:process.env.AGENT_BOARD_CHROME||undefined});
  const{c,p}=await context();
  await check('Blocks follow latest effective update; sidebar and main agree',async()=>{
    const expected=['Frontend','Cache layer','Data export','Workflow','Operations'];
    assert.deepEqual(await nav(p),expected);assert.deepEqual(await groups(p),expected);
    assert.equal(await p.locator('.task-entry').count(),36);
  });
  await check('Tasks newest first inside each block, stable ties, unknown last',async()=>{
    const actual=await p.locator('.task-group').evaluateAll(ns=>ns.map(n=>({stream:n.dataset.stream,ids:[...n.querySelectorAll('.task-entry')].map(x=>x.dataset.taskId)})));
    for(const group of actual){
      const expected=body.tasks.filter(t=>t.workstream===group.stream).sort((a,b)=>{
        const ta=a.display_updated_at?Date.parse(a.display_updated_at):-Infinity,tb=b.display_updated_at?Date.parse(b.display_updated_at):-Infinity;
        return ta===tb?(a.id<b.id?-1:a.id>b.id?1:0):tb-ta;
      }).map(t=>t.id);
      assert.deepEqual(group.ids,expected);
    }
    const clock=row(p,'doc-export').locator('.time');
    assert.match(await clock.innerText(),/ ago$|^just now$/);assert.notEqual(await clock.getAttribute('title'),'Unknown');
    return actual;
  });
  await check('Clock excludes receipt/refresh/new registration; null is not fresh',async()=>{
    const actual=await p.evaluate(()=>{
      const known={id:'known',workstream:'A',display_updated_at:'2026-10-01T08:00:00Z',business_at:'2026-09-01T00:00:00Z'};
      const unknown={id:'unknown',workstream:'B',display_updated_at:null,business_at:'2099-01-01T00:00:00Z',received_at:'2099-01-01T00:00:00Z'};
      const invalid={...unknown,id:'invalid',display_updated_at:'invalid'};
      const undated={id:'undated',workstream:'C',business_at:'2026-09-01T00:00:00Z'};
      return{ids:[unknown,known,undated,invalid].sort(newestFirst).map(t=>t.id),streams:orderedStreams([unknown,known,undated,invalid]),undated:updateClock(undated),ties:orderedStreams([{...known,workstream:'Z'},{...known,workstream:'A'}])};
    });
    assert.deepEqual(actual.ids,['known','undated','invalid','unknown']);assert.deepEqual(actual.streams,['A','C','B']);
    assert.equal(actual.undated,Date.parse('2026-09-01T00:00:00Z'));assert.deepEqual(actual.ties,['A','Z']);
  });
  await check('10-second auto-refresh promotes the renamed block, preserving focus, expansion, details',async()=>{
    await row(p,'doc-export').locator('.task-name').click();await p.locator('#detail-body .detail-section').first().waitFor();
    // Cards are one line; expansion lives in the open detail and survives the reorder.
    const fold=p.locator('#detail-body details[data-key="timeline"]');await fold.evaluate(n=>n.open=true);
    await p.locator('#search').focus();const before=await p.locator('#detail-title').innerText();
    task('release-feed').display_updated_at=at(1);
    await p.waitForFunction(()=>document.querySelector('#tasks .task-group')?.dataset.stream==='Cache layer',{},{timeout:15000});
    assert.equal((await nav(p))[0],'Cache layer');
    assert.equal((await groups(p))[0],'Cache layer');
    assert.equal(await p.evaluate(()=>document.activeElement.id),'search');
    assert.equal(await fold.evaluate(n=>n.open),true);
    assert.equal(await p.locator('#detail-title').innerText(),before);assert(await p.locator('#detail').isVisible());
    await p.locator('#close-detail').click();
  });
  await check('Local filters cannot reorder blocks by a different clock',async()=>{
    await p.locator('#status').selectOption('running');await p.locator('#agent').selectOption('agent-a');
    assert.equal(await row(p,'release-feed').count(),0);
    assert.equal((await groups(p))[0],'Cache layer');assert.equal((await nav(p))[0],'Cache layer');
    task('board-layout').display_updated_at=at(2);await refresh(p);
    assert.equal(await p.locator('#status').inputValue(),'running');assert.equal(await p.locator('#agent').inputValue(),'agent-a');
    assert.equal((await groups(p))[0],'Workflow');await clear(p);
  });
  await check('Selected block stays selected when another block gets a newer update',async()=>{
    await p.locator('.stream-link[data-stream="Frontend"]').click();
    await p.locator('#search').fill('docs');task('release-feed').display_updated_at=at(3);await refresh(p);
    assert.equal(await p.locator('#workstream').inputValue(),'Frontend');assert.equal(await p.locator('#search').inputValue(),'docs');
    assert.equal((await nav(p))[0],'Cache layer');assert.deepEqual(await groups(p),['Frontend']);
    await clear(p);await p.locator('.stream-link[data-stream=""]').click();
  });
  await check('Old-task toggle recomputes order; missing-clock task remains visible',async()=>{
    const t=task('nightly'),saved=structuredClone(t);t.display_updated_at=at(4);t.display_expired=true;await refresh(p);
    assert.equal(await row(p,'nightly').count(),0);assert.equal(await row(p,'identity').count(),1);
    assert.equal((await nav(p)).at(-1),'Operations');
    await p.locator('#show-old').check();assert.equal((await nav(p))[0],'Operations');assert.equal((await groups(p))[0],'Operations');
    await p.locator('#show-old').uncheck();Object.assign(t,saved);await refresh(p);
  });
  await check('Delayed older state response cannot roll back newest ordering',async()=>{
    let arrived,release;const waiting=new Promise(r=>arrived=r),gate=new Promise(r=>release=r);
    let once=true;
    await p.route('**/api/state',async r=>{
      const snapshot=structuredClone(body);
      if(once){once=false;arrived();await gate;}
      await r.fulfill({json:snapshot});
    });
    const first=refresh(p);await waiting;task('board-layout').display_updated_at=at(4);await refresh(p);release();await first;
    assert.equal((await nav(p))[0],'Workflow');assert.equal((await groups(p))[0],'Workflow');
    await p.unroute('**/api/state');await p.route('**/api/state',r=>r.fulfill({json:structuredClone(body)}));
  });
  await check('Unchanged data does not rebuild rows or move focus',async()=>{
    const actual=await p.evaluate(async()=>{
      const node=document.querySelector('.task-entry');document.querySelector('#search').focus();
      await refresh();return{same:node===document.querySelector('.task-entry'),focus:document.activeElement.id};
    });assert.deepEqual(actual,{same:true,focus:'search'});
  });
  await check('Read failure retains last order and current filters',async()=>{
    const before=await nav(p);await p.locator('#search').fill('board');
    await p.route('**/api/state',r=>r.fulfill({status:503,body:'synthetic unavailable'}));await refresh(p);
    assert.deepEqual(await nav(p),before);assert.equal(await p.locator('#search').inputValue(),'board');assert(await p.locator('#error').isVisible());
    await p.unroute('**/api/state');await p.route('**/api/state',r=>r.fulfill({json:structuredClone(body)}));await refresh(p);await clear(p);
  });
  await c.close();
  for(const size of[{width:1440,height:900},{width:1280,height:800},{width:390,height:844}]){
    await check(size.width+': all tasks/order preserved without horizontal overflow',async()=>{
      const{c,p}=await context(size);assert.equal(await p.locator('.task-entry').count(),36);
      assert.deepEqual(await groups(p),await nav(p));
      const geometry=await p.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));assert(geometry.scrollWidth<=size.width);
      await p.screenshot({path:path.join(output,'newest-first-'+size.width+'.png')});await c.close();return geometry;
    });
  }
  await check('Browser remains GET-only, same-origin, no unexpected errors',async()=>{assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);});
}finally{
  if(browser)await browser.close();
  let stop=null;
  if(fixture)stop=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'../board.py'),'--config',path.join(root,'config.json'),'--runtime',path.join(root,'stress'),'stop'],{env:{...process.env,PYTHONUTF8:'1'}})).stdout);
  await fs.writeFile(path.join(output,'RESULTS.json'),JSON.stringify({scope:'author synthetic sorting regression; not independent audit',fixture,root,stop,results,errors,blocked,passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length},null,2));
}
console.log(JSON.stringify({passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length,output}));
if(results.some(x=>x.result==='FAIL'))process.exitCode=1;
