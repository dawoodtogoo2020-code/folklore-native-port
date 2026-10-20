"""Synthetic control-flow and private frozen-metadata mutation cases only."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType,SimpleNamespace
import unittest

PROJECT=Path(__file__).resolve().parents[3]
SOURCE=PROJECT/'scripts/native-lane/native-image03-verifier-005/verify_bundle.py'
SPEC=importlib.util.spec_from_file_location('verifier005',SOURCE)
V=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(V)
FROZEN=Path(os.environ.get('FOLKLORE_VERIFIER_FIXTURE_ROOT',str(PROJECT.parent/'native-image-recovery-002')))
FOLDER=FROZEN/'analysis-v4';BINDINGS=FROZEN/'input-bindings.json'
SDK=Path('Q:/folklore/work/ps3recomp-strict')
OUT=Path(os.environ.get('FOLKLORE_TEST_OUTPUT_ROOT',str(PROJECT.parent/'native-image03-verifier-005')))

def ins(mn,target=None,operands='',raw=0):return SimpleNamespace(mnemonic=mn,target=target,operands=operands,raw=raw)

class SyntheticFlow(unittest.TestCase):
    def flow(self,instructions,seeds=(0,),tables=()):
        image=bytes(4+max(instructions))
        return V.recompute(image,lambda word,pc:instructions.get(pc,ins('???')),lambda x:x.target,seeds,set(tables))
    def test_conditional_both_arms(self):
        seen,cfg,falls=self.flow({0:ins('brz',8),4:ins('stop'),8:ins('bi')})
        self.assertEqual(set(seen),{0,4,8});self.assertEqual(falls,[dict(site=0,target=4)])
        self.assertEqual(V.partitions(seen,cfg),[[0,4],[4,8],[8,12]])
    def test_unconditional_skips_data(self):
        seen,cfg,falls=self.flow({0:ins('br',8),4:ins('???'),8:ins('stop')})
        self.assertEqual(set(seen),{0,8});self.assertFalse(falls)
    def test_direct_call_has_continuation(self):
        seen,cfg,falls=self.flow({0:ins('brsl',8,raw=79),4:ins('stop'),8:ins('bi')})
        self.assertEqual(cfg['calls'],[dict(site=0,target=8,link_register=79,call_next=False)])
        self.assertEqual(set(seen),{0,4,8})
    def test_call_next_and_loop(self):
        seen,cfg,falls=self.flow({0:ins('brsl',4),4:ins('br',4)})
        self.assertEqual(len(seen),2);self.assertTrue(cfg['calls'][0]['call_next'])
    def test_indirect_call_records_unknown_dynamic_target(self):
        seen,cfg,falls=self.flow({0:ins('bisl',operands='$r0, $r85'),4:ins('stop')})
        self.assertEqual(cfg['indirect'],[dict(site=0,kind='bisl',registers=[0,85])]);self.assertEqual(len(seen),2)
    def test_indirect_tail_does_not_guess(self):
        seen,cfg,falls=self.flow({0:ins('bi'),4:ins('???')});self.assertEqual(set(seen),{0})
    def test_misaligned(self):
        with self.assertRaisesRegex(V.Rejected,'decoded-target-range-alignment'):self.flow({0:ins('br',2),4:ins('stop')})
    def test_outside(self):
        with self.assertRaisesRegex(V.Rejected,'decoded-target-range-alignment'):self.flow({0:ins('br',12),4:ins('stop')})
    def test_invalid_seed(self):
        with self.assertRaisesRegex(V.Rejected,'decoded-target-range-alignment'):self.flow({0:ins('stop')},seeds=(-4,))
    def test_unknown_decode(self):
        with self.assertRaisesRegex(V.Rejected,'decoded-unknown-instruction'):self.flow({0:ins('.word')})
    def test_unknown_direct_target(self):
        with self.assertRaisesRegex(V.Rejected,'decoded-direct-target-unknown'):self.flow({0:ins('br')})
    def test_noninteger_target(self):
        with self.assertRaisesRegex(V.Rejected,'decoded-target-range-alignment'):self.flow({0:ins('br',False)})
    def test_fallthrough_outside(self):
        with self.assertRaisesRegex(V.Rejected,'decoded-target-range-alignment'):self.flow({0:ins('nop')})
    def test_table_target(self):
        with self.assertRaisesRegex(V.Rejected,'decoded-target-table-data'):self.flow({0:ins('br',8),8:ins('stop')},tables=(8,))
    def test_duplicate_json_key(self):
        OUT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=OUT) as temp:
            p=Path(temp)/'duplicate.json';p.write_text('{"site":0,"site":4}',encoding='utf-8')
            with self.assertRaisesRegex(V.Rejected,'duplicate-json-key'):V.load_json(p)

class FrozenMutations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis=V.load_json(FOLDER/'structure.private.json');cls.generation=V.load_json(FOLDER/'generated/generation.private.json')
        binding=V.load_json(BINDINGS)
        cls.image=next(Path(x['path']).read_bytes() for x in binding['inputs'] if x['path'].endswith('.image.bin'))
        cls.desc=next(Path(x['path']).read_bytes() for x in binding['inputs'] if x['path'].endswith('.desc.bin'))
    def validate(self,a=None,g=None,image=None,desc=None):
        with V.tools_exact(SDK,self.analysis['tool_bindings']) as (d,l,p):
            return V.verify_metadata(self.image if image is None else image,self.desc if desc is None else desc,
                                     self.analysis if a is None else a,self.generation if g is None else g,d.spu_decode,l,p)
    def mutate(self,change,category):
        a=deepcopy(self.analysis);g=deepcopy(self.generation);change(a,g)
        a['offline_generation']=deepcopy(g)
        with self.assertRaisesRegex(V.Rejected,category):self.validate(a,g)
    def test_valid_control(self):
        r=V.verify_bundle(BINDINGS,FOLDER,SDK)
        self.assertEqual((r['code_instructions'],r['direct_edges'],r['direct_calls'],r['native_chunk_entries']),(8453,907,50,1083))
        self.assertEqual(r['unknown_direct_targets'],0);self.assertFalse(r['compiled'] or r['registered'] or r['executed'])
    def test_reproduced_outside_edge(self):self.mutate(lambda a,g:a['cfg']['direct_edges'][0].update(target=len(self.image)+4),'recorded-cfg-mismatch')
    def test_misaligned_edge(self):self.mutate(lambda a,g:a['cfg']['direct_edges'][0].update(target=2),'recorded-cfg-mismatch')
    def test_edge_into_table(self):self.mutate(lambda a,g:a['cfg']['direct_edges'][0].update(target=0xA0),'recorded-cfg-mismatch')
    def test_invalid_edge_source(self):self.mutate(lambda a,g:a['cfg']['direct_edges'][0].update(site=-4),'recorded-cfg-mismatch')
    def test_edge_source_is_data(self):self.mutate(lambda a,g:a['cfg']['direct_edges'][0].update(site=0xA0),'recorded-cfg-mismatch')
    def test_missing_edge(self):self.mutate(lambda a,g:a['cfg']['direct_edges'].pop(),'recorded-cfg-mismatch')
    def test_duplicate_edge(self):self.mutate(lambda a,g:a['cfg']['direct_edges'].append(deepcopy(a['cfg']['direct_edges'][0])),'recorded-cfg-mismatch')
    def test_altered_kind(self):self.mutate(lambda a,g:a['cfg']['direct_edges'][0].update(kind='brnz'),'recorded-cfg-mismatch')
    def test_altered_inrange_target(self):self.mutate(lambda a,g:a['cfg']['direct_edges'][0].update(target=0x20),'recorded-cfg-mismatch')
    def test_unknown_declaration(self):self.mutate(lambda a,g:a['cfg'].update(unknown_decode_sites=[0]),'recorded-cfg-mismatch')
    def test_outside_declaration(self):self.mutate(lambda a,g:a['cfg'].update(outside_image_targets=[len(self.image)+4]),'recorded-cfg-mismatch')
    def test_untrusted_extra_seed(self):self.mutate(lambda a,g:a['cfg']['seeds'].append(0x8520),'recorded-cfg-mismatch')
    def test_missing_range(self):self.mutate(lambda a,g:a['cfg']['ranges_half_open'].pop(),'recorded-cfg-mismatch')
    def test_duplicate_range(self):self.mutate(lambda a,g:a['cfg']['ranges_half_open'].append(a['cfg']['ranges_half_open'][0]),'recorded-cfg-mismatch')
    def test_float_count(self):self.mutate(lambda a,g:a['cfg'].update(instruction_count=8453.0),'recorded-cfg-mismatch')
    def test_analysis_image_hash(self):self.mutate(lambda a,g:a.update(image_sha256='0'*64),'analysis-capture-identity')
    def test_analysis_descriptor_hash(self):self.mutate(lambda a,g:a.update(descriptor_sha256='0'*64),'analysis-capture-identity')
    def test_analysis_image_size(self):self.mutate(lambda a,g:a.update(image_size=len(self.image)+4),'analysis-capture-identity')
    def test_descriptor_field(self):self.mutate(lambda a,g:a['descriptor'].update(requested_stack_size=0),'analysis-descriptor')
    def test_table_target_rebinding(self):self.mutate(lambda a,g:a['qualified_relative_tables'][0]['targets'].__setitem__(0,0x20),'recorded-table-mismatch')
    def test_generated_chunk_missing(self):self.mutate(lambda a,g:g['chunks_relative'].pop(),'generation-chunks_relative')
    def test_generated_chunk_duplicate(self):self.mutate(lambda a,g:g['chunks_relative'].append(g['chunks_relative'][0]),'generation-chunks_relative')
    def test_generated_base(self):self.mutate(lambda a,g:g.update(base=0x18000),'generation-proposed-base')
    def test_generated_entry(self):self.mutate(lambda a,g:g.update(entry_relative=4),'generation-entry_relative')
    def test_generated_direct_declaration(self):self.mutate(lambda a,g:g.update(missing_direct_targets=[0x8000]),'generation-direct-targets')
    def test_generated_dynamic_sites(self):self.mutate(lambda a,g:g['dynamic_dispatch_sites_relative'].pop(),'generation-dynamic_dispatch_sites_relative')
    def test_image_bytes_drift(self):
        image=bytearray(self.image);image[0]^=1
        with self.assertRaisesRegex(V.Rejected,'capture-identity'):self.validate(image=image)
    def test_descriptor_bytes_drift(self):
        desc=bytearray(self.desc);desc[-1]^=1
        with self.assertRaisesRegex(V.Rejected,'capture-identity'):self.validate(desc=desc)
    def test_generated_bytes_drift(self):
        OUT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=OUT) as temp:
            folder=Path(temp);(folder/'generated').mkdir()
            (folder/'structure.private.json').write_bytes((FOLDER/'structure.private.json').read_bytes())
            (folder/'generated/generation.private.json').write_bytes((FOLDER/'generated/generation.private.json').read_bytes())
            for name in V.GENERATED:shutil.copyfile(FOLDER/'generated'/name,folder/'generated'/name)
            p=folder/'generated/image03_generated.c';p.write_bytes(p.read_bytes()+b'\n')
            with self.assertRaisesRegex(V.Rejected,'generated-artifact-drift'):V.verify_bundle(BINDINGS,folder,SDK)
    def test_binding_size_drift(self):
        OUT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=OUT) as temp:
            b=V.load_json(BINDINGS);b['inputs'][0]['bytes']+=1
            p=Path(temp)/'bindings.json';p.write_text(json.dumps(b),encoding='utf-8')
            with self.assertRaisesRegex(V.Rejected,'bound-input-drift'):V.verify_bundle(p,FOLDER,SDK)

class Imports(unittest.TestCase):
    def setUp(self):self.declarations=V.load_json(FOLDER/'structure.private.json')['tool_bindings']
    def test_declared_import_path_drift(self):
        self.declarations[0]['path']=str(OUT/'spu_disasm.py')
        with self.assertRaisesRegex(V.Rejected,'tool-declaration-identity'):
            with V.tools_exact(SDK,self.declarations):pass
    def test_sdk_path_and_declarations_rebound(self):
        for item in self.declarations:item['path']=str(OUT/'copied-sdk/tools'/Path(item['path']).name)
        with self.assertRaisesRegex(V.Rejected,'tool-sdk-identity'):
            with V.tools_exact(OUT/'copied-sdk',self.declarations):pass
    def test_declared_import_hash_drift(self):
        self.declarations[0]['sha256']='0'*64
        with self.assertRaisesRegex(V.Rejected,'tool-declaration-identity'):
            with V.tools_exact(SDK,self.declarations):pass
    def test_missing_dependency(self):
        self.declarations.pop()
        with self.assertRaisesRegex(V.Rejected,'tool-declarations'):
            with V.tools_exact(SDK,self.declarations):pass
    def test_duplicate_dependency(self):
        self.declarations[-1]=self.declarations[0]
        with self.assertRaisesRegex(V.Rejected,'tool-declaration-identity'):
            with V.tools_exact(SDK,self.declarations):pass
    def test_cached_wrong_path(self):
        fake=ModuleType('spu_disasm');fake.__file__=str(OUT/'wrong.py');sys.modules['spu_disasm']=fake
        try:
            with self.assertRaisesRegex(V.Rejected,'tool-cached-module'):
                with V.tools_exact(SDK,self.declarations):pass
            self.assertIs(sys.modules['spu_disasm'],fake)
        finally:sys.modules.pop('spu_disasm',None)
    def test_cached_claiming_correct_path(self):
        fake=ModuleType('spu_lifter');fake.__file__=str(SDK/'tools/spu_lifter.py');sys.modules['spu_lifter']=fake
        try:
            with self.assertRaisesRegex(V.Rejected,'tool-cached-module'):
                with V.tools_exact(SDK,self.declarations):pass
        finally:sys.modules.pop('spu_lifter',None)
    def test_actual_dependency_identity_and_restore(self):
        before=list(sys.path)
        with V.tools_exact(SDK,self.declarations) as (d,l,p):
            self.assertIs(l.spu_decode,d.spu_decode)
            self.assertEqual(Path(d.__file__).resolve(),(SDK/'tools/spu_disasm.py').resolve())
        self.assertEqual(sys.path,before);self.assertNotIn('spu_disasm',sys.modules);self.assertNotIn('spu_lifter',sys.modules)
    def test_exception_restores_import_state(self):
        before=list(sys.path)
        with self.assertRaisesRegex(RuntimeError,'synthetic'):
            with V.tools_exact(SDK,self.declarations):raise RuntimeError('synthetic')
        self.assertEqual(sys.path,before);self.assertNotIn('spu_disasm',sys.modules)
    def test_file_hash_drift_before_use(self):
        OUT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=OUT) as temp:
            p=Path(temp)/'fake.py';p.write_text('raise RuntimeError("MUST_NOT_RUN")',encoding='utf-8')
            with self.assertRaisesRegex(V.Rejected,'pinned-file-hash-drift'):V.pinned_bytes(p,'0'*64)

if __name__=='__main__':unittest.main(verbosity=2)
