"""Synthetic jump-table recovery checks. No game inputs or execution."""
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_startup_aot import relative_table_targets,close_indirect_flow,indirect_targets


class TableTests(unittest.TestCase):
    def setUp(self):
        self.raw=bytearray(4096)
        self.code={
            0x670:('brnz','$r2, 0x854'),0x674:('ai','$r2, $r84, -2'),
            0x678:('ila','$r3, 0x6A0'),0x67C:('shli','$r4, $r2, 2'),
            0x680:('a','$r3, $r3, $r126'),0x684:('clgti','$r2, $r2, 6'),
            0x688:('lqx','$r5, $r3, $r4'),0x68C:('rotqby','$r4, $r5, $r4'),
            0x690:('a','$r4, $r4, $r3'),0x694:('brnz','$r2, 0x6C0'),0x698:('bi','$r4')}
        self.targets=[0xB7C,0xBB4,0xBE4,0xC14,0xC44,0xC84,0xCDC]
        for n,target in enumerate(self.targets):struct.pack_into('>i',self.raw,0x6A0+4*n,target-0x6A0)

    def decode(self,word,pc):
        mnemonic,operands=self.code.get(pc,('nop',''))
        return SimpleNamespace(mnemonic=mnemonic,operands=operands)

    def test_bounded_table_recovers_all_alternatives(self):
        self.assertEqual(relative_table_targets(self.raw,self.decode,0x698),self.targets)

    def test_bad_table_entries_rejected(self):
        for target in (-4,0x6A0,0x6A4,4096,0xB7D):
            struct.pack_into('>i',self.raw,0x6A0,target-0x6A0)
            with self.assertRaises(ValueError):relative_table_targets(self.raw,self.decode,0x698)

    def test_changed_control_or_base_rejected(self):
        for pc,replacement in ((0x694,('brz','$r2, 0x6C0')),(0x680,('a','$r3, $r3, $r125')),
                               (0x684,('clgti','$r2, $r2, 256')),(0x67C,('shli','$r4, $r2, 3'))):
            old=self.code[pc];self.code[pc]=replacement
            with self.assertRaises(ValueError):relative_table_targets(self.raw,self.decode,0x698)
            self.code[pc]=old

    def test_bounds_cannot_clobber_selector_before_shift(self):
        self.code[0x67C],self.code[0x684]=self.code[0x684],self.code[0x67C]
        with self.assertRaisesRegex(ValueError,'ordering'):
            relative_table_targets(self.raw,self.decode,0x698)
        with self.assertRaises(ValueError):
            indirect_targets(self.raw,self.decode,{'site':0x698,'kind':'bi','registers':[4]})

    def test_new_indirect_sites_are_checked_after_adding_seeds(self):
        calls=[]
        def walker(raw,decode,target,seeds):
            calls.append(seeds)
            return {},{'unknown_decode_sites':[],'outside_image_targets':[],
                       'indirect':[{'site':0}]+([{'site':16}] if 16 in seeds else [])}
        def resolver(raw,decode,branch):
            if branch['site']==16:raise ValueError('unqualified second-pass branch')
            return [16]
        with self.assertRaisesRegex(ValueError,'second-pass'):
            close_indirect_flow(bytes(64),None,None,walker,resolver)
        self.assertEqual(calls,[[0],[0,16]])

    def test_indirect_recovery_reaches_fixed_point(self):
        def walker(raw,decode,target,seeds):
            return {pc:None for pc in seeds},{'unknown_decode_sites':[],'outside_image_targets':[],
                    'indirect':[{'site':pc} for pc in seeds]}
        def resolver(raw,decode,branch):return [min(32,branch['site']+16)]
        reached,_=close_indirect_flow(bytes(64),None,None,walker,resolver)
        self.assertEqual(set(reached),{0,16,32})

    def test_interleaved_table_with_checked_pc_getter(self):
        self.code={
            0x20:('ila','$r2, 0x28'),0x24:('brsl','$r79, 0x28'),
            0x28:('shlqbyi','$r8, $r3, 0'),0x2C:('ila','$r3, 0x80'),
            0x30:('stqd','$r1, -48($r1)'),0x34:('ai','$r1, $r1, -48'),
            0x38:('sf','$r79, $r2, $r79'),0x3C:('ai','$r2, $r6, -2'),
            0x40:('shli','$r6, $r6, 2'),0x44:('shli','$r9, $r2, 2'),
            0x48:('a','$r3, $r3, $r79'),0x4C:('clgti','$r2, $r2, 11'),
            0x50:('lqx','$r5, $r6, $r8'),0x54:('lqx','$r10, $r3, $r9'),
            0x58:('cwx','$r4, $r6, $r8'),0x5C:('shufb','$r5, $r7, $r5, $r4'),
            0x60:('rotqby','$r4, $r10, $r9'),0x64:('stqx','$r5, $r6, $r8'),
            0x68:('a','$r4, $r4, $r3'),0x6C:('brnz','$r2, 0xC4'),0x74:('bi','$r4')}
        targets=[0x200+4*n for n in range(12)]
        for n,target in enumerate(targets):struct.pack_into('>i',self.raw,0x80+4*n,target-0x80)
        branch={'site':0x74,'kind':'bi','registers':[4]}
        self.assertEqual(indirect_targets(self.raw,self.decode,branch),targets)
        self.code[0x20]=('ila','$r2, 0x2C')
        with self.assertRaises(ValueError):indirect_targets(self.raw,self.decode,branch)
        self.code[0x20]=('ila','$r2, 0x28')
        saved=self.code[0x48];self.code[0x48]=('ai','$r2, $r2, 1')
        with self.assertRaisesRegex(ValueError,'selector changed'):indirect_targets(self.raw,self.decode,branch)
        self.code[0x48]=saved
        saved=self.code[0x5C];self.code[0x5C]=('ai','$r9, $r9, 4')
        with self.assertRaisesRegex(ValueError,'index changed'):indirect_targets(self.raw,self.decode,branch)
        self.code[0x5C]=saved
        self.code[0x64]=('ai','$r3, $r3, 16')
        with self.assertRaisesRegex(ValueError,'base changed'):indirect_targets(self.raw,self.decode,branch)

    def test_shared_dispatch_requires_default_return(self):
        self.code[0x694]=('brz','$r2, 0x6B0')
        self.code[0x698]=('il','$r3, -1')
        self.code[0x6AC]=('bi','$r0')
        self.code[0x6B4]=('bi','$r4')
        # Move the synthetic table away from the default/dispatch code.
        self.code[0x678]=('ila','$r3, 0x700')
        for n,target in enumerate(self.targets):struct.pack_into('>i',self.raw,0x700+4*n,target-0x700)
        branch={'site':0x6B4,'kind':'bi','registers':[4]}
        self.assertEqual(indirect_targets(self.raw,self.decode,branch),self.targets)
        self.code[0x6AC]=('nop','')
        with self.assertRaises(ValueError):indirect_targets(self.raw,self.decode,branch)


if __name__=='__main__':unittest.main()
