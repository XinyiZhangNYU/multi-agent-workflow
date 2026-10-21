"""Bounded own-process metrics; never queries business-process command lines."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import board
from model import now
from server import atomic_json


def sample(pid):
    if os.name!='nt': return {'unmeasured':'Windows process sampler only'}
    class PMC(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in ['peak_working_set','working_set','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile']]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True); psapi=ctypes.WinDLL('psapi',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(PMC),wintypes.DWORD]
    handle=kernel.OpenProcess(0x0400|0x0010,False,pid)
    if not handle: return {'unmeasured':'process access unavailable'}
    try:
        t=[wintypes.FILETIME() for _ in range(4)]; mem=PMC();mem.cb=ctypes.sizeof(mem)
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(v) for v in t]) or not psapi.GetProcessMemoryInfo(handle,ctypes.byref(mem),mem.cb):
            return {'unmeasured':'process counters unavailable'}
        ticks=lambda v:(v.dwHighDateTime<<32)+v.dwLowDateTime
        return {'cpu_seconds':(ticks(t[2])+ticks(t[3]))/1e7,'working_set_bytes':mem.working_set,'peak_working_set_bytes':mem.peak_working_set}
    finally:kernel.CloseHandle(handle)


def sizes(runtime):
    return {name:sum(p.stat().st_size for p in runtime.glob(pattern) if p.is_file()) for name,pattern in [('sqlite_bytes','board.sqlite3*'),('log_bytes','service.log*'),('outbox_bytes','outbox/*/*.json')]}


live=Path(sys.argv[1]); output=Path(sys.argv[2]); result={'measured_at':now(),'method':'Windows own PID GetProcessTimes/GetProcessMemoryInfo; idle 10 seconds; isolated 100 progress POSTs; no model calls'}
info=board.health(live); before=sample(info['pid']);begin=time.perf_counter();time.sleep(10);after=sample(info['pid']);wall=time.perf_counter()-begin
result['live_idle']={'pid':info['pid'],'wall_seconds':wall,'before':before,'after':after,'files':sizes(live)}
if 'cpu_seconds' in before and 'cpu_seconds' in after:result['live_idle']['cpu_delta_seconds']=after['cpu_seconds']-before['cpu_seconds']
root=Path(tempfile.mkdtemp(prefix='agent-board-cost-'));(root/'.ai-workflow').mkdir();(root/'.ai-workflow/WORKFLOW.md').write_text('isolated metrics')
config={'project_root':str(root),'project':'Synthetic','port':0};cfg=root/'config.json';atomic_json(cfg,config);runtime=root/'runtime'
start=board.start(SimpleNamespace(config=str(cfg),port=0),config,runtime)
try:
    for event in [board.envelope('task','load',{'title':'Synthetic load','project':'Synthetic','workstream':'metrics'}),board.envelope('run','load',{'role':'execution','agent':'script','client':'test'},'load-run',1),board.envelope('claim','load',{'expected_version':0},'load-run',2)]:board.send(runtime,event,config,queue=False)
    before=sample(start['pid']);begin=time.perf_counter()
    for seq in range(3,103):board.send(runtime,board.envelope('progress','load',{'phase':'Synthetic '+str(seq)},'load-run',seq),config,queue=False)
    wall=time.perf_counter()-begin;after=sample(start['pid'])
    result['synthetic_load']={'startup_ms':start['startup_ms'],'events':board.request(runtime,'/api/state')['event_count'],'progress_posts':100,'wall_seconds':wall,'before':before,'after':after,'files':sizes(runtime)}
    if 'cpu_seconds' in before and 'cpu_seconds' in after:result['synthetic_load']['cpu_delta_seconds']=after['cpu_seconds']-before['cpu_seconds']
finally:
    board.request(runtime,'/api/stop',{'instance_id':start['instance_id']})
    result['synthetic_runtime']=str(root)
result['resident_processes_per_instance']=1;result['new_runtime_dependencies']=0
result['limits']='Small workload, point samples and observed peak; not a full capacity benchmark. No subscription/token percentages measured.'
atomic_json(output,result);print(json.dumps(result,ensure_ascii=False,indent=2))
