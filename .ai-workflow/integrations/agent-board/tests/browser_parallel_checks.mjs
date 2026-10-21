// Author regression: synthetic SQLite, real loopback service, GET-only browser.
// Parallel and handed-off requests share one run set for counts, options and filters.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
const exec=promisify(execFile),here=path.dirname(fileURLToPath(import.meta.url));
const python=process.env.AGENT_BOARD_PYTHON||(process.platform==='win32'?'py':'python3'),pythonArgs=python==='py'?['-3','-B']:['-B'];
const output=path.resolve(process.argv[2]),root=await fs.mkdtemp(path.join(os.tmpdir(),'board-parallel-ui-'));
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
const ids=p=>rows(p).evaluateAll(n=>n.map(x=>x.dataset.taskId).sort());
const ready=p=>p.waitForFunction(()=>document.querySelector('#tasks').getAttribute('aria-busy')==='false');
const detailText=async(p,id)=>{await p.evaluate(id=>openTask(id,null,false),id);await p.waitForFunction(id=>detailValue?.id===id,id);const text=await p.locator('#detail-body').innerText();await p.locator('#close-detail').click();return text;};
async function filter(p,{status='',agent='',cat=''}){
  await p.locator('#clear-filters').click();
  if(status)await p.locator('#status').selectOption(status);
  if(agent)await p.locator('#agent').selectOption(agent);
  if(cat)await p.locator('#attention-filter').selectOption(cat);
  return ids(p);
}
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
  fixture=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'parallel_fixture.py'),root])).stdout);
  await fs.writeFile(path.join(output,'FIXTURE.json'),JSON.stringify({fixture,root},null,2));
  const original=await(await fetch(fixture.url+'/api/state')).json();
  assert.equal(original.service,'agent-board-v1');assert.equal(original.instance_id,fixture.instance_id);
  assert.equal(original.tasks.length,5);assert.equal(original.outbox.unreadable,1);assert.equal(original.outbox.pending,0);
  assert.deepEqual(original.tasks.filter(t=>t.human_input_required).map(t=>t.id).sort(),['ended-ask','handoff-ask','peer-ask']);
  assert.equal(original.tasks.find(t=>t.id==='fresh-peer').display_expired,false);
  browser=await chromium.launch({headless:true,executablePath:process.env.AGENT_BOARD_CHROME||undefined});
  for(const size of [{width:1440,height:900},{width:390,height:844}]){
    const{c,p}=await page(size);
    await check(size.width+': requests from parallel, handed-off and ended runs are counted and labelled',async()=>{
      assert.equal(await p.locator('#attention-count').innerText(),'3');
      assert.equal(await p.locator('#attention .inbox-row').count(),3);
      // Handed-off and ended askers fold as possibly stale; nothing closes by itself.
      assert.deepEqual(await p.locator('#attention .stale-inbox .inbox-row').evaluateAll(n=>n.map(x=>x.dataset.taskId).sort()),['ended-ask','handoff-ask']);
      assert((await p.locator('#attention > .inbox .inbox-row[data-task-id="peer-ask"]').innerText()).includes('Parallel run asks you to pick A or B'));
      for(const id of ['peer-ask','handoff-ask','ended-ask'])assert.equal(await row(p,id).locator('.ask-tag').innerText(),'Needs you 1');
      assert((await detailText(p,'handoff-ask')).includes('OldAgent / old-run (source handed off)'));
      assert((await detailText(p,'ended-ask')).includes('GoneAgent / gone-run (run ended, not withdrawn)'));
      await p.locator('#attention .stale-inbox > summary').click();
      await p.locator('#attention .inbox-row[data-task-id="handoff-ask"] .inbox-more').click();
      assert((await p.locator('#attention .inbox-row[data-task-id="handoff-ask"] .inbox-detail').innerText()).includes(' (source handed off)'));
      assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await p.screenshot({path:path.join(output,'parallel-default-'+size.width+'.png')});
    });
    await check(size.width+': fresh parallel work keeps a stale-owner task on the default list',async()=>{
      assert.equal(await rows(p).count(),5);assert.equal(await p.locator('#old-count').innerText(),'');
      assert.equal(await row(p,'fresh-peer').locator('.old-label').count(),0);
    });
    await check(size.width+': unreadable backlog file is shown, not hidden',async()=>{
      assert(await p.locator('#outbox-banner').isVisible());
      const text=await p.locator('#outbox-banner').innerText();
      assert(text.includes('1 queued file unreadable'),text);assert(!text.includes('not yet confirmed as delivered'),text);
    });
    await c.close();
  }
  const{c,p}=await page();
  await check('Joint filters match one run across owner and parallel runs',async()=>{
    const agents=await p.locator('#agent option').evaluateAll(n=>n.map(x=>x.value).filter(Boolean));
    for(const a of ['GoneAgent','NewAgent','OldAgent','OwnerAgent','PeerAgent'])assert(agents.includes(a),a);
    assert.deepEqual(await filter(p,{cat:'human'}),['ended-ask','handoff-ask','peer-ask']);
    assert.deepEqual(await filter(p,{agent:'PeerAgent'}),['fresh-peer','peer-ask']);
    assert.deepEqual(await filter(p,{agent:'PeerAgent',cat:'human'}),['peer-ask']);
    assert.deepEqual(await filter(p,{agent:'PeerAgent',status:'running'}),['fresh-peer']);
    assert.deepEqual(await filter(p,{agent:'PeerAgent',status:'waiting_input'}),['peer-ask']);
    assert.deepEqual(await filter(p,{agent:'OwnerAgent',cat:'human'}),[]);
    assert.deepEqual(await filter(p,{agent:'OldAgent',cat:'human'}),['handoff-ask']);
    assert.deepEqual(await filter(p,{agent:'NewAgent',cat:'human'}),[]);
    assert.deepEqual(await filter(p,{agent:'GoneAgent',status:'ended',cat:'human'}),['ended-ask']);
    await p.locator('#clear-filters').click();
  });
  await check('Parallel list and detail keep the ended run whose request is open',async()=>{
    const r=row(p,'ended-ask');
    await r.locator('.task-name').click();await p.locator('#detail-body .detail-section').first().waitFor();
    const text=await p.locator('#detail-body').innerText();
    assert(text.includes('Parallel execution runs')&&text.includes('GoneAgent')&&text.includes('gone-run'),text);
    assert(text.includes(' (run ended, not withdrawn)'),text);
    await p.keyboard.press('Escape');assert(await p.locator('#detail').isHidden());
  });
  await check('GET-only same-origin / no browser errors / nothing changed',async()=>{
    assert.deepEqual(network,[]);assert.deepEqual(errors,[]);
    const last=await(await fetch(fixture.url+'/api/state')).json();assert.equal(last.event_count,original.event_count);
    assert.equal(last.outbox.unreadable,1);
  });
  await c.close();
}finally{
  if(browser)await browser.close();
  if(fixture)stop=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'../board.py'),'--config',path.join(root,'config.json'),'--runtime',path.join(root,'runtime'),'stop'])).stdout);
  await fs.writeFile(path.join(output,'RESULTS.json'),JSON.stringify({scope:'author synthetic parallel-run Chrome checks; not independent audit',fixture,root,stop,results,errors,network,passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length},null,2));
}
console.log(JSON.stringify({passed:results.filter(x=>x.result==='PASS').length,failed:results.filter(x=>x.result==='FAIL').length,output}));
if(results.some(x=>x.result==='FAIL'))process.exitCode=1;
