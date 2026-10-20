"""Recompute the frozen image03 direct CFG using exact pinned local tools.

Offline structural verification only. No guest interpreter, compiler, runtime,
registration, generated-source import, or original payload in diagnostics.
"""
import argparse
from collections import Counter, deque
from contextlib import contextmanager
import dataclasses
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import sysconfig

sys.dont_write_bytecode = True
IMAGE_SHA='3eee051c8a43fb010503792f592fc820d9caaaa7d57ce0de8662d2a75a6b42fd'
DESC_SHA='e85625f42f2f9a1d8bbb83aaf62b4fd4adb92e1d05098c919fc2b1ab05672700'
TOOL_SHA={
 'spu_disasm.py':'7e78a2b90167807b03b817575df2b9c335407a3482a54d8ce9e1d452f118328a',
 'spu_lifter.py':'15b378c72bacfc1c8e4101385b29f3969bf38c3f6ba9bb17d51674cc5261a862',
 'find_spu_functions.py':'15d29ad47fecbadf9659cb27937c4cf0db636db15b57a09bf986d28829d337e4'}
SDK_ROOT=Path('Q:/folklore/work/ps3recomp-strict')
RECOVERY=Path(__file__).resolve().parents[1]/'recover_image03.py'
# Bind the frozen source-pattern checker as well as its decoder dependency.
RECOVERY_SHA='f495a665099dbc6089e813eb72d408c073f2da2fba80f2323e3fb3e2beb42179'
GENERATED={
 'image03_generated.c':('522cc767ed155f7fd43f9083ec38a5a3a6a4ac661b2f5918fd2231318e6ddf7b',729936),
 'image03_generated.h':('3b445a4520216228bc790d7edf9aa49af4006542efbdd3598c81e76f899686ed',63894)}
DIRECT={'br','bra','brsl','brasl','brz','brnz','brhz','brhnz'}
UNCONDITIONAL={'br','bra'}
INDIRECT={'bi','bisl','bisled','biz','binz','bihz','bihnz','iret'}

class Rejected(ValueError):
    pass

def reject(category):raise Rejected(category)
def sha(data):return hashlib.sha256(data).hexdigest()
def equal(actual,expected,category):
    # JSON preserves integer/float/bool and duplicate list declarations.
    if json.dumps(actual,sort_keys=True,separators=(',',':'))!=json.dumps(expected,sort_keys=True,separators=(',',':')):reject(category)
def load_json(path):
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:reject('duplicate-json-key')
            result[k]=v
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=unique)
def pinned_bytes(path,expected):
    data=Path(path).read_bytes()
    if sha(data)!=expected:reject('pinned-file-hash-drift')
    return data
def _exact_module(name,path,data):
    if name in sys.modules:reject('tool-cached-module')
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    exec(compile(data,str(path),'exec'),module.__dict__)
    if Path(module.__file__).resolve()!=path or Path(module.__spec__.origin).resolve()!=path:reject('tool-module-origin')
    return module

@contextmanager
def tools_exact(sdk,declarations):
    sdk=Path(sdk).resolve()
    if sdk!=SDK_ROOT.resolve():reject('tool-sdk-identity')
    expected={name:(sdk/'tools'/name).resolve() for name in TOOL_SHA}
    if not isinstance(declarations,list) or len(declarations)!=len(expected):reject('tool-declarations')
    actual={}
    for item in declarations:
        p=Path(item['path']).resolve();name=p.name
        if name in actual or name not in expected or p!=expected[name] or item['sha256']!=TOOL_SHA[name]:reject('tool-declaration-identity')
        actual[name]=p
    if set(actual)!=set(expected):reject('tool-declarations')
    names=[Path(name).stem for name in TOOL_SHA]+['_image03_frozen_patterns']
    # Reject even a cached module claiming the right file: attributes may differ.
    if any(name in sys.modules for name in names):reject('tool-cached-module')
    raw={name:pinned_bytes(path,TOOL_SHA[name]) for name,path in expected.items()}
    recovery=RECOVERY.resolve();recovery_data=pinned_bytes(recovery,RECOVERY_SHA)
    before_path=list(sys.path);before_modules=set(sys.modules)
    allowed_roots={Path(sysconfig.get_path('stdlib')).resolve(),(Path(sys.base_prefix)/'DLLs').resolve()}
    stdlib=[p for p in before_path if p and any(Path(p).resolve().is_relative_to(root) for root in allowed_roots)]
    try:
        sys.path[:]=stdlib
        disasm=_exact_module('spu_disasm',expected['spu_disasm.py'],raw['spu_disasm.py'])
        lifter=_exact_module('spu_lifter',expected['spu_lifter.py'],raw['spu_lifter.py'])
        finder=_exact_module('find_spu_functions',expected['find_spu_functions.py'],raw['find_spu_functions.py'])
        patterns=_exact_module('_image03_frozen_patterns',recovery,recovery_data)
        if lifter.spu_decode is not disasm.spu_decode or finder.spu_decode is not disasm.spu_decode or lifter.SPUInstruction is not disasm.SPUInstruction:reject('tool-dependency-identity')
        if Path(disasm.spu_decode.__code__.co_filename).resolve()!=expected['spu_disasm.py']:reject('tool-function-origin')
        for name in set(sys.modules)-before_modules-set(names):
            module=sys.modules[name];p=getattr(module,'__file__',None)
            if p and not any(Path(p).resolve().is_relative_to(root) for root in allowed_roots):reject('tool-unpinned-dependency')
        # The tools modify sys.path on import. No later lookup may inherit it.
        sys.path[:]=stdlib
        yield disasm,lifter,patterns
        for name,path in expected.items():pinned_bytes(path,TOOL_SHA[name])
        pinned_bytes(recovery,RECOVERY_SHA)
    finally:
        sys.path[:]=before_path
        for name in names:sys.modules.pop(name,None)

def ranges(pcs):
    result=[]
    for pc in sorted(pcs):
        if result and result[-1][1]==pc:result[-1][1]+=4
        else:result.append([pc,pc+4])
    return result

def recompute(image,decode,target_of,seeds,table_words):
    """Static worklist over both conditional arms; no branch is executed."""
    pending=deque(seeds);seen={};edges=[];calls=[];indirect=[];terminals=[];refs=[];channels=[];falls=[]
    while pending:
        pc=pending.popleft()
        if type(pc) is not int or pc<0 or pc&3 or pc+4>len(image):reject('decoded-target-range-alignment')
        if pc in table_words:reject('decoded-target-table-data')
        if pc in seen:continue
        ins=decode(struct.unpack_from('>I',image,pc)[0],pc);mn=ins.mnemonic;seen[pc]=ins
        if mn in ('.word','???'):reject('decoded-unknown-instruction')
        next_pc=None
        if mn in DIRECT:
            target=target_of(ins)
            if target is None:reject('decoded-direct-target-unknown')
            if type(target) is not int or target<0 or target&3 or target+4>len(image):reject('decoded-target-range-alignment')
            if target in table_words:reject('decoded-target-table-data')
            edges.append(dict(site=pc,target=target,kind=mn));pending.append(target)
            if mn in ('brsl','brasl'):calls.append(dict(site=pc,target=target,link_register=ins.raw&127,call_next=target==pc+4))
            if mn not in UNCONDITIONAL:next_pc=pc+4
        elif mn in INDIRECT:
            indirect.append(dict(site=pc,kind=mn,registers=[int(n) for n in re.findall(r'\$r(\d+)',ins.operands)]))
            if mn not in ('bi','iret'):next_pc=pc+4
        elif mn in ('stop','stopd'):terminals.append(dict(site=pc,kind=mn))
        else:next_pc=pc+4
        if next_pc is not None:
            falls.append(dict(site=pc,target=next_pc));pending.append(next_pc)
        if mn in ('lqa','stqa','lqr','stqr'):
            targets=re.findall(r'0x([0-9A-Fa-f]+)',ins.operands)
            if targets:refs.append(dict(site=pc,target=int(targets[-1],16),kind=mn))
        if mn in ('rdch','wrch','rchcnt'):channels.append(dict(site=pc,kind=mn,channel=(ins.raw>>7)&127,register=ins.raw&127))
    cfg=dict(seeds=list(seeds),instruction_count=len(seen),ranges_half_open=ranges(seen),
             mnemonics=dict(sorted(Counter(i.mnemonic for i in seen.values()).items())),direct_edges=edges,calls=calls,indirect=indirect,
             unknown_decode_sites=[],outside_image_targets=[],terminals=terminals,static_ls_refs=refs,channels=channels)
    return seen,cfg,falls

def partitions(seen,cfg):
    leaders={lo for lo,hi in ranges(seen)}|set(cfg['seeds'])
    for edge in cfg['direct_edges']:
        leaders.add(edge['target'])
        if edge['kind'] not in UNCONDITIONAL:leaders.add(edge['site']+4)
    for edge in cfg['indirect']:leaders.add(edge['site']+4)
    result=[]
    for pc in sorted(seen):
        if result and pc==result[-1][1] and pc not in leaders:result[-1][1]+=4
        else:result.append([pc,pc+4])
    return result

def verify_cfg(declared,computed):
    equal(declared,computed,'recorded-cfg-mismatch')

def verify_metadata(image,desc,analysis,generation,decode,lifter,patterns):
    if len(image)!=36448 or sha(image)!=IMAGE_SHA or len(desc)!=128 or sha(desc)!=DESC_SHA:reject('capture-identity')
    identity=dict(image_sha256=IMAGE_SHA,image_size=len(image),descriptor_sha256=DESC_SHA)
    for key,value in identity.items():equal(analysis.get(key),value,'analysis-capture-identity')
    equal(analysis.get('schema'),'folklore-image03-offline-structure-v1','analysis-schema')
    tables=patterns.resolve_image03_tables(image,decode)
    equal(analysis.get('qualified_relative_tables'),tables,'recorded-table-mismatch')
    table_words={pc for table in tables for pc in range(table['start'],table['end'],4)}
    seeds=list(dict.fromkeys([0]+[target for table in tables for target in table['targets']]))
    seen,cfg,falls=recompute(image,decode,lifter.SPULifter._branch_target,seeds,table_words)
    verify_cfg(analysis.get('cfg'),cfg)
    blocks=partitions(seen,cfg)
    equal(generation.get('base'),0x8000,'generation-proposed-base')
    equal(generation.get('base_is_proposed_native_placement'),True,'generation-placement-assumption')
    for key,value in dict(entry_relative=0,entry_absolute=0x8000,instruction_count=len(seen),chunk_count=len(blocks),chunks_relative=blocks,dynamic_dispatch_sites_relative=cfg['indirect']).items():equal(generation.get(key),value,'generation-'+key)
    starts={lo for lo,hi in blocks}
    if not ({target for table in tables for target in table['targets']}|{0x20})<=starts:reject('indirect-candidate-chunk')
    missing=sorted({0x8000+edge['target'] for edge in cfg['direct_edges'] if edge['target'] not in starts})
    equal(generation.get('missing_direct_targets'),missing,'generation-direct-targets')
    if missing:reject('generation-direct-targets')
    relocated=[decode(struct.unpack_from('>I',image,pc)[0],0x8000+pc) for pc in sorted(seen)]
    bounds=[(0x8000+lo,0x8000+hi) for lo,hi in blocks]
    equal(generation.get('computed_r0_sites'),sorted(lifter.compute_bi_r0_jumps(relocated,bounds)),'generation-r0-sites')
    equal(generation.get('non_r0_return_sites'),sorted(lifter.compute_link_returns(relocated,bounds)),'generation-link-returns')
    # Recompute the recorded instruction-level translation inventory. Numeric
    # equivalence is not established by successful Python source translation.
    translator=lifter.SPULifter(strict_unsupported=True)
    dummy=lifter.LiftedFunction(name='inventory_only',start_addr=0,end_addr=len(image))
    failures=[];exceptions=[]
    for pc,ins in sorted(seen.items()):
        try:
            text=translator._translate(ins,dummy)
            if 'spu_lift_strict_failure' in text:failures.append(dict(site=pc,mnemonic=ins.mnemonic,reasons=re.findall(r'"([^"\n]+)"',text)))
        except Exception as error:exceptions.append(dict(site=pc,mnemonic=ins.mnemonic,error_type=type(error).__name__))
    equal(analysis.get('strict_translation_failures'),failures,'translation-inventory')
    equal(analysis.get('translation_exceptions'),exceptions,'translation-exceptions')
    if failures or exceptions:reject('unsupported-translation')
    equal(generation.get('strict_chunk_failure_sites'),[],'generation-failure-declarations')
    equal(generation.get('unsupported_mnemonics'),{},'generation-unsupported-declarations')
    equal(analysis.get('offline_generation'),generation,'analysis-generation-copy')
    descriptor=dict(binary_ea=f"0x{int.from_bytes(desc[:8],'big')&~15:08X}",binary_size=int.from_bytes(desc[8:10],'big')*16,requested_stack_size=int.from_bytes(desc[28:30],'big')*16,scratch_size=int.from_bytes(desc[30:32],'big')*16,job_type=desc[44])
    equal(analysis.get('descriptor'),descriptor,'analysis-descriptor')
    fingerprint=1469598103934665603
    for byte in image:fingerprint=((fingerprint^byte)*1099511628211)&0xFFFFFFFFFFFFFFFF
    for key,value in dict(runtime_fingerprint_recomputed=f'0x{fingerprint:016X}',elf_magic_at_zero=image[:4]==b'\x7fELF',embedded_elf_magic_count=image.count(b'\x7fELF'),trailing_zero_bytes=len(image)-len(image.rstrip(b'\0'))).items():equal(analysis.get(key),value,'analysis-'+key)
    roles=[dict(site=pc,mnemonic=seen[pc].mnemonic,registers=[int(n) for n in re.findall(r'\$r(\d+)',seen[pc].operands)]) for pc in sorted(seen) if pc<64]
    equal(analysis.get('entry_first_16_roles'),roles,'entry-roles')
    stack=[]
    for pc in (0x14,0x18):
        ins=seen[pc]
        if ins.mnemonic!='ai' or re.findall(r'\$r(\d+)',ins.operands)!=['1','1']:reject('entry-stack-role')
        stack.append(int(re.sub(r'\$r\d+','',ins.operands).replace(',',' ').strip(),0))
    if sum(stack):reject('entry-stack-balance')
    end=max(seen)+4;gaps=sorted(set(range(0,end,4))-set(seen)-table_words)
    padding=Counter(decode(struct.unpack_from('>I',image,pc)[0],pc).mnemonic for pc in gaps)
    if set(padding)-{'nop','lnop'}:reject('unclassified-envelope-gap')
    return dict(cfg=cfg,blocks=blocks,falls=falls,table_words=table_words,end=end,padding=padding,gaps=gaps,stack=stack)

def verify_bundle(bindings,folder,sdk):
    binding=load_json(bindings)
    if binding.get('schema')!='folklore-native-image-recovery-bindings-v1' or binding.get('task_id')!='native-image-recovery-002':reject('binding-schema')
    captures={};paths=set()
    for item in binding['inputs']:
        p=Path(item['path']).resolve()
        if p in paths:reject('duplicate-bound-input')
        paths.add(p);raw=p.read_bytes()
        if type(item['bytes']) is not int or len(raw)!=item['bytes'] or sha(raw)!=item['sha256']:reject('bound-input-drift')
        for suffix in ('.image.bin','.desc.bin'):
            if p.name.endswith(suffix):
                if suffix in captures:reject('duplicate-capture-role')
                captures[suffix]=raw
    if set(captures)!={'.image.bin','.desc.bin'}:reject('missing-capture-role')
    folder=Path(folder);analysis=load_json(folder/'structure.private.json');generation=load_json(folder/'generated/generation.private.json')
    with tools_exact(sdk,analysis['tool_bindings']) as (disasm,lifter,patterns):
        result=verify_metadata(captures['.image.bin'],captures['.desc.bin'],analysis,generation,disasm.spu_decode,lifter,patterns)
        equal(generation.get('generated'),{name:dict(sha256=h,bytes=n) for name,(h,n) in GENERATED.items()},'generated-declaration')
        for name,(h,n) in GENERATED.items():
            data=(folder/'generated'/name).read_bytes()
            if len(data)!=n or sha(data)!=h:reject('generated-artifact-drift')
        source=(folder/'generated/image03_generated.c').read_text(encoding='utf-8')
        functions=re.findall(r'^void image03_b[0-9a-f]+_spu_func_([0-9A-F]+)\(spu_context\* ctx\) \{',source,re.M)
        expected={f'{0x8000+lo:08X}' for lo,hi in result['blocks']}
        if len(functions)!=len(expected) or set(functions)!=expected:reject('generated-function-inventory')
        if 'TODO spu:' in source:reject('generated-compatibility-todo')
    cfg=result['cfg']
    reached={pc for lo,hi in cfg['ranges_half_open'] for pc in range(lo,hi,4)}
    return dict(schema='folklore-image03-recomputed-verification-v2',status='static_checks_passed',
                image_sha256=IMAGE_SHA,descriptor_sha256=DESC_SHA,code_instructions=cfg['instruction_count'],
                code_bytes=cfg['instruction_count']*4,direct_edges=len(cfg['direct_edges']),direct_calls=len(cfg['calls']),
                fallthrough_edges=len(result['falls']),unknown_direct_targets=sum(edge['target'] not in reached for edge in cfg['direct_edges']),
                unknown_decode_sites=sum(count for name,count in cfg['mnemonics'].items() if name in ('.word','???')),indirect_sites=len(cfg['indirect']),
                code_envelope_end_exclusive=f"0x{result['end']:X}",relative_table_bytes=len(result['table_words'])*4,
                unreached_alignment_bytes=len(result['gaps'])*4,padding_decode_counts=dict(result['padding']),
                tail_bytes_not_admitted_as_code=len(captures['.image.bin'])-result['end'],native_chunk_entries=len(result['blocks']),
                entry_wrapper_net_stack_delta=sum(result['stack']),compiled=False,registered=False,executed=False,
                tool_dependencies=[dict(path=str((SDK_ROOT/'tools'/name).resolve()),sha256=h) for name,h in TOOL_SHA.items()]+[dict(path=str(RECOVERY),sha256=RECOVERY_SHA)],
                scope='Recomputed direct-flow/partition/hash inventory under pinned decoder and bounded table assumptions; no indirect target liveness, ABI, numeric or native admission proof.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bindings',type=Path,required=True);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--sdk',type=Path,required=True);a=p.parse_args()
    try:result=verify_bundle(a.bindings,a.analysis,a.sdk)
    except (Rejected,KeyError,TypeError,ValueError,OSError) as error:
        # Never echo source data, words, arbitrary JSON or generated payloads.
        category=str(error) if isinstance(error,Rejected) else type(error).__name__
        print(json.dumps(dict(status='rejected',category=category)));return 1
    print(json.dumps(result,indent=2));return 0

if __name__=='__main__':sys.exit(main())
