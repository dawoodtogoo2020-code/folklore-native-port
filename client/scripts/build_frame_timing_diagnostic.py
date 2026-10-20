"""Relink the frozen AO diagnostic with only the opt-in host timing adapter."""
import argparse, hashlib, json, subprocess
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--coordinator-work',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
args=p.parse_args()
repo=Path(__file__).resolve().parents[1]
cw=args.coordinator_work; out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
settings=json.loads((cw/'windows-ao-settings/receipt.json').read_text())
frozen=json.loads((cw/'windows-ao-dataflow/receipt.json').read_text())
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
base_binary=cw/'windows-ao-dataflow/FolkloreBoot.exe'
assert sha(base_binary)=='3305a7f346e1bcd1bf2176108bed9c9f2ce0cb432bb337da97fc6a22d96775e4'
compile_command=next(s['command'] for s in settings['steps'] if s['name']=='compile-boot')[:]
original=Path(compile_command[compile_command.index('-c')+1])
assert sha(original)==settings['sources']['native/boot_main.cpp']
source=original.read_text()
edits=[('#include <thread>','#include <thread>\n#include "frame_timing_probe.h"'),
       ('    ready.store(1);\n    auto next = std::chrono::steady_clock::now();','    FrameTimingProbe frame_timing;\n    ready.store(1);\n    auto next = std::chrono::steady_clock::now();'),
       ('                present();','                const auto present_begin = frame_timing.active() ? frame_timing.tick() : 0;\n                present();\n                const auto present_end = frame_timing.active() ? frame_timing.tick() : 0;'),
       ('                if (!deferred) {\n                    unsigned count = presented.fetch_add(1) + 1;','                if (!deferred) {\n                    frame_timing.record(after, present_begin, present_end);\n                    unsigned count = presented.fetch_add(1) + 1;')]
for old,new in edits:
    assert source.count(old)==1,old
    source=source.replace(old,new)
diagnostic=out/'boot_main_timing.cpp';diagnostic.write_text(source)
compile_command[compile_command.index('-c')+1]=str(diagnostic)
compile_command[compile_command.index('-o')+1]=str(out/'boot-timing.obj')
compile_command.extend(['-I'+str(original.parent),'-I'+str(repo/'native')])
link=next(s['command'] for s in frozen['steps'] if s['name']=='link')[:]
assert link.count(str(cw/'windows-ao-settings/boot.obj'))==1
link[link.index(str(cw/'windows-ao-settings/boot.obj'))]=str(out/'boot-timing.obj')
link[link.index('-o')+1]=str(out/'FolkloreBoot.exe')
link=[('-Wl,--out-implib,'+str(out/'FolkloreBoot.dll.a')) if arg.startswith('-Wl,--out-implib,') else arg for arg in link]
cwd=cw/'windows-fxaa-title/build-folklore-gnu'
for arg in link:
    if arg.startswith('CMakeFiles/') or (not arg.startswith('-') and arg.endswith('.a')):
        assert (cwd/arg).is_file(),arg
steps=[]
for label,command in [('compile',compile_command),('link',link)]:
    with (out/(label+'.log')).open('wb') as log:
        result=subprocess.run(command,cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
    steps.append(dict(name=label,command=command,cwd=str(cwd),exit=result.returncode))
    if result.returncode:
        print((out/(label+'.log')).read_text(errors='replace')[-5000:]);raise SystemExit(result.returncode)
receipt=dict(scope='Frozen3305 renderer/archive/settings/AOT objects, only boot adapter QPC instrumentation changed',base_binary_sha256='3305a7f346e1bcd1bf2176108bed9c9f2ce0cb432bb337da97fc6a22d96775e4',binary=str(out/'FolkloreBoot.exe'),binary_sha256=sha(out/'FolkloreBoot.exe'),original_boot_sha256=sha(original),diagnostic_boot_sha256=sha(diagnostic),header_sha256=sha(repo/'native/frame_timing_probe.h'),frozen_runtime_sha256=sha(cw/'windows-ao-dataflow/libps3recomp_runtime.a'),steps=steps)
assert sha(base_binary)==receipt['base_binary_sha256']
(out/'build-manifest.json').write_text(json.dumps(receipt,indent=2));print(json.dumps({k:v for k,v in receipt.items() if k!='steps'}))
