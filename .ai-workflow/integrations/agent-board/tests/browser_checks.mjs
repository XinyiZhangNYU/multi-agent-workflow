// Basic browser checks against an owned synthetic fixture on a free loopback port.
// The Playwright module is provided explicitly; this script never installs it and never touches a live board.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
const exec=promisify(execFile);
const here=path.dirname(fileURLToPath(import.meta.url));
const evidence=path.resolve(process.argv[2]);
const cli=path.resolve(here,'../board.py');
const python=process.env.AGENT_BOARD_PYTHON||(process.platform==='win32'?'py':'python3');
const pythonArgs=python==='py'?['-3','-B']:['-B'];
const {chromium}=await import(process.env.AGENT_BOARD_PLAYWRIGHT_MODULE);
await fs.mkdir(evidence,{recursive:true});
const fixtureRoot=await fs.mkdtemp(path.join(os.tmpdir(),'agent-board-browser-'));
const args=[...pythonArgs,cli,'--config',path.join(fixtureRoot,'config.json'),'--runtime',path.join(fixtureRoot,'runtime')];
const runCLI=async(...tail)=>JSON.parse((await exec(python,[...args,...tail],{env:{...process.env,PYTHONUTF8:'1'}})).stdout);
const checks=[],errors=[];
let browser,fixture;
try{
  fixture=JSON.parse((await exec(python,[...pythonArgs,path.join(here,'browser_fixture.py'),fixtureRoot],{env:{...process.env,PYTHONUTF8:'1'}})).stdout);
  browser=await chromium.launch({headless:true,executablePath:process.env.AGENT_BOARD_CHROME||undefined});
  const context=await browser.newContext({viewport:{width:1440,height:1000},timezoneId:'America/New_York'});
  await context.route('**/*',route=>new URL(route.request().url()).origin===fixture.url?route.continue():route.abort());
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(fixture.url+'/?view=list');await page.locator('#tasks .task-entry').first().waitFor();
  assert.equal(await page.locator('#tasks .task-entry').count(),1);checks.push('the synthetic task is listed');
  await page.screenshot({path:path.join(evidence,'desktop-1440.png'),fullPage:true});
  await page.locator('#tasks .task-entry[data-task-id="xss"] .task-name').click();
  await page.locator('#detail-body .detail-section').first().waitFor();
  assert.equal(await page.evaluate(()=>window.XSS),undefined);
  assert.equal(await page.locator('#tasks img,#tasks script,#detail svg,#detail img,#detail script').count(),0);
  const text=await page.locator('#detail').innerText();
  assert.match(text,/1 document predates the latest execution report/);assert.match(text,/Not for the current candidate \/ history · 1/);assert.match(text,/Program observation/);
  assert.match(text,/Source time/);assert.match(text,/Read at/);
  await page.locator('#detail').screenshot({path:path.join(evidence,'synthetic-detail.png')});
  checks.push('injected text stays literal; an older document is not compared, candidate mismatch and source kinds are visible');
  await page.locator('#close-detail').click();
  const phase='Browser check report '+new Date().toISOString();
  assert.equal((await runCLI('report','xss','r','--phase',phase,'--progress','Checking filters, detail and narrow layout in a real browser.')).status,'RECORDED');
  // Observe the real scheduled refresh; no API mocking or direct DOM insertion.
  await page.waitForFunction(text=>document.querySelector('#tasks').textContent.includes(text),phase,{timeout:15000});
  checks.push('a real CLI report appears after the periodic refresh');
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(evidence,'narrow-390.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);checks.push('390 px has no horizontal overflow');
  const times=await page.evaluate(()=>[fullTime('2026-01-15T17:00:00Z'),fullTime('2026-07-15T16:00:00Z')]);
  assert.ok(times.every(t=>t.includes('12:00:00')),times.join(' | '));checks.push('local times (emulated New York zone) show noon in winter and summer');
  assert.deepEqual(errors,[]);checks.push('no browser JavaScript errors');
  await fs.writeFile(path.join(evidence,'browser-results.json'),JSON.stringify({tested_at:new Date().toISOString(),url:fixture.url,checks,errors,viewports:[1440,390],scope:'author synthetic isolated browser test'},null,2));
  console.log(JSON.stringify({passed:checks.length,checks}));
}finally{
  if(browser)await browser.close();
  if(fixture){
    const stopped=await runCLI('stop');
    // Retain the owned synthetic runtime as evidence; no cleanup of other trees.
    await fs.writeFile(path.join(evidence,'browser-fixture-location.txt'),fixtureRoot+'\nStop status: '+stopped.status+'\n');
    assert.equal(stopped.status,'STOPPED');
  }
}
