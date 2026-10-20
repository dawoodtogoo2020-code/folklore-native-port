"""Assemble private startup AOT inputs. Never publish generated game bytes."""
import argparse
import hashlib
import json
import sys
import re
import struct
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def array(name, data):
    return 'static const unsigned char ' + name + '[]={' + ','.join(map(str, data)) + '};\n'


def relative_table_targets(raw, decode, site):
    """Recognize bounded signed-relative jump tables, refusing other dispatch."""
    def ins(pc):
        if pc < 0 or pc + 4 > len(raw):
            raise ValueError('table instruction outside image')
        i = decode(struct.unpack_from('>I', raw, pc)[0], pc)
        return i.mnemonic, [int(n) for n in re.findall(r'\$r(\d+)', i.operands)], i.operands
    mn, regs, _ = ins(site)
    if mn != 'bi' or len(regs) != 1:
        raise ValueError('not an indirect table branch')
    result = regs[0]
    # Fixed final dispatch sequence, with a real unsigned bounds branch.
    branch = ins(site - 4)
    add = ins(site - 8)
    rotate = ins(site - 12)
    load = ins(site - 16)
    if (branch[0] != 'brnz' or add[0] != 'a' or len(add[1]) != 3 or
            add[1][:2] != [result,result] or rotate[0] != 'rotqby' or
            len(rotate[1]) != 3 or rotate[1][0] != result or load[0] != 'lqx' or
            load[1] != [rotate[1][1],add[1][2],rotate[1][2]]):
        raise ValueError('unrecognized relative table dispatch')
    table_register = add[1][2]
    selector = rotate[1][2]
    compare_register = branch[1][0]
    prefix = [ins(pc) for pc in range(max(0,site-40),site-16,4)]
    # The only admitted prefix writes here are exactly ILA table, SHLI index,
    # A table,table,r126 and CLGTI bounds, plus nonwriting hints.
    ila = [x for x in prefix if x[0]=='ila' and x[1]==[table_register]]
    shifts = [x for x in prefix if x[0]=='shli' and x[1] and x[1][0]==selector and int(x[2].split(',')[-1],0)==2]
    adds = [x for x in prefix if x[0]=='a' and x[1]==[table_register,table_register,126]]
    bounds = [x for x in prefix if x[0]=='clgti' and x[1] and x[1][0]==compare_register]
    if len(ila)!=1 or len(shifts)!=1 or len(adds)!=1 or len(bounds)!=1 or shifts[0][1][1]!=bounds[0][1][1]:
        raise ValueError('unrecognized table base/selector/bounds')
    prefix = prefix[prefix.index(ila[0]):]
    allowed = {id(x) for x in (ila[0],shifts[0],adds[0],bounds[0])}
    if any(id(x) not in allowed and x[0] not in ('nop','lnop') for x in prefix):
        raise ValueError('unqualified table prefix operation')
    if [x for x in prefix if x[0] not in ('nop','lnop')] != [ila[0],shifts[0],adds[0],bounds[0]]:
        raise ValueError('unqualified table prefix ordering')
    start = int(ila[0][2].split(',')[-1],0)
    count = int(bounds[0][2].split(',')[-1],0)+1
    if start & 3 or not 1<=count<=256 or start<0 or start+4*count>len(raw):
        raise ValueError('invalid table extent')
    targets = [start+struct.unpack_from('>i',raw,start+4*i)[0] for i in range(count)]
    if any(t&3 or t<0 or t+4>len(raw) or start<=t<start+4*count for t in targets):
        raise ValueError('invalid relative table destination')
    return targets


def indirect_targets(raw, decode, branch):
    if branch['kind']=='bi' and branch['registers']==[0]:
        return []
    if branch['kind']=='bi':
        try:
            return relative_table_targets(raw,decode,branch['site'])
        except ValueError:
            return interleaved_table_targets(raw,decode,branch['site'])
    if branch['kind']!='bisl' or len(branch['registers'])!=2 or branch['registers'][0]!=0:
        raise ValueError('additional image needs explicit indirect-flow recovery')
    target_register=branch['registers'][1]
    offset=None
    for pc in range(branch['site']-4,max(-1,branch['site']-68),-4):
        ins=decode(struct.unpack_from('>I',raw,pc)[0],pc)
        regs=[int(n) for n in re.findall(r'\$r(\d+)',ins.operands)]
        if ins.mnemonic.startswith('br') or ins.mnemonic in ('bi','bisl','stop'):
            break
        if regs and regs[0]==target_register:
            if offset is None and ins.mnemonic=='a' and regs==[target_register,target_register,126]:
                offset=-1
            elif offset==-1 and ins.mnemonic=='ila' and regs==[target_register]:
                offset=int(ins.operands.split(',')[1],0)
                break
            else:
                break
    if offset is None or offset<0 or offset&3 or offset+4>len(raw):
        raise ValueError('unresolved image-relative indirect call')
    return [offset]


def interleaved_table_targets(raw,decode,site):
    """Same bounded table with interleaved nonpointer work and a PC getter."""
    minimum=max(0,site-128)
    def at(pc):
        i=decode(struct.unpack_from('>I',raw,pc)[0],pc)
        return i,[int(n) for n in re.findall(r'\$r(\d+)',i.operands)]
    def definition(reg,before):
        for pc in range(before-4,minimum-1,-4):
            i,regs=at(pc)
            if (i.mnemonic not in ('stqd','stqx','stqa','stqr','wrch','fscrwr','bi','bisl','br','bra','brz','brnz','brhz','brhnz','hbr','hbra','hbrr')
                    and regs and regs[0]==reg):return pc,i,regs
        raise ValueError('unresolved interleaved table register')
    def require(item,mnemonic,regs):
        if item[1].mnemonic!=mnemonic or item[2]!=regs:raise ValueError('unrecognized interleaved table dataflow')
    _,branch_regs=at(site)
    result=branch_regs[0]
    add=definition(result,site)
    if add[1].mnemonic!='a' or add[2][:2]!=[result,result] or len(add[2])!=3:
        raise ValueError('unrecognized interleaved table result')
    table=add[2][2]
    rotate=definition(result,add[0])
    if rotate[1].mnemonic!='rotqby' or len(rotate[2])!=3:raise ValueError('unrecognized interleaved table rotate')
    value,index=rotate[2][1:]
    load=definition(value,rotate[0]);require(load,'lqx',[value,table,index])
    shift=definition(index,load[0])
    if shift[1].mnemonic!='shli' or len(shift[2])!=2 or int(shift[1].operands.split(',')[-1],0)!=2:
        raise ValueError('unqualified interleaved table index')
    selector=shift[2][1]
    tail=[(pc,*at(pc)) for pc in range(add[0]+4,site,4) if at(pc)[0].mnemonic not in ('nop','lnop')]
    if not tail or tail[0][1].mnemonic not in ('brnz','brz'):raise ValueError('unqualified table bounds branch')
    guards=[tail[0]]
    if guards[0][1].mnemonic=='brnz':
        if len(tail)!=1:raise ValueError('work after fallthrough table bounds branch')
    else:
        # In-range branch to a shared dispatch tail. The out-of-range path
        # must end in an unconditional r0 return before that tail; it cannot
        # fall into the table with an unchecked selector.
        join=int(guards[0][1].operands.split(',')[-1],0)
        if join<=guards[0][0]+4 or join>site or join&3:raise ValueError('invalid shared table tail')
        if any(at(pc)[0].mnemonic not in ('nop','lnop') for pc in range(join,site,4)):
            raise ValueError('work inside shared table tail')
        last=at(join-4)
        if last[0].mnemonic!='bi' or last[1]!=[0]:raise ValueError('table default path can reach dispatch')
        for pc in range(guards[0][0]+4,join-4,4):
            i,_=at(pc)
            if i.mnemonic.startswith('br') or i.mnemonic in ('bi','bisl','iret','stop','stopd'):
                raise ValueError('unqualified default table control flow')
    bound=definition(guards[0][2][0],guards[0][0])
    require(bound,'clgti',[guards[0][2][0],selector])
    if not shift[0]<bound[0]<load[0]:raise ValueError('unqualified table prefix ordering')
    def optional_definition(reg,before):
        try:return definition(reg,before)[0]
        except ValueError:return None
    if optional_definition(selector,shift[0])!=optional_definition(selector,bound[0]):
        raise ValueError('table selector changed before bounds check')
    if definition(index,rotate[0])[0]!=shift[0]:
        raise ValueError('table index changed before rotation')
    table_add=definition(table,load[0])
    if definition(table,add[0])[0]!=table_add[0]:
        raise ValueError('table base changed before target addition')
    if table_add[1].mnemonic!='a' or table_add[2][:2]!=[table,table] or len(table_add[2])!=3:
        raise ValueError('unqualified interleaved table base')
    base=table_add[2][2]
    ila=definition(table,table_add[0]);require(ila,'ila',[table])
    if base!=126:
        delta=definition(base,table_add[0])
        if delta[1].mnemonic!='sf' or delta[2][::2]!=[base,base]:raise ValueError('unqualified PC relocation delta')
        pc_reg=delta[2][1]
        constant=definition(pc_reg,delta[0]);require(constant,'ila',[pc_reg])
        link=definition(base,delta[0]);require(link,'brsl',[base])
        if int(constant[1].operands.split(',')[-1],0)!=link[0]+4 or int(link[1].operands.split(',')[-1],0)!=link[0]+4:
            raise ValueError('unqualified PC getter')
    for pc in range(ila[0]+4,site,4):
        i,_=at(pc)
        if i.mnemonic.startswith('br') and pc!=guards[0][0]:raise ValueError('branch inside table dataflow')
    start=int(ila[1].operands.split(',')[-1],0)
    count=int(bound[1].operands.split(',')[-1],0)+1
    if start&3 or not 1<=count<=256 or start<0 or start+count*4>len(raw):raise ValueError('invalid table extent')
    targets=[start+struct.unpack_from('>i',raw,start+4*n)[0] for n in range(count)]
    if any(t&3 or t<0 or t+4>len(raw) or start<=t<start+4*count for t in targets):raise ValueError('invalid table destination')
    return targets


def close_indirect_flow(raw, decode, branch_target, walker, resolver=indirect_targets):
    seeds={0}
    for _ in range(len(raw)//4+1):
        reached,cfg=walker(raw,decode,branch_target,sorted(seeds))
        if cfg['unknown_decode_sites'] or cfg['outside_image_targets']:
            raise ValueError('additional image has unknown or external flow')
        discovered=set()
        for branch in cfg['indirect']:
            discovered.update(resolver(raw,decode,branch))
        if not discovered-seeds:
            return reached,cfg
        seeds.update(discovered)
    raise ValueError('indirect closure did not converge')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', type=Path, required=True)
    p.add_argument('--previous', type=Path, required=True)
    p.add_argument('--recovery', type=Path, required=True)
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--extra-capture', type=Path, action='append', default=[], help='Further captured raw images to lift and admit')
    p.add_argument('--generic-only', action='store_true', help='replace previous narrow profiles with whole-image AOT registrations')
    a = p.parse_args()
    if a.out.exists():
        p.error('output already exists')
    image = (a.capture / 'spurs-job-45017080-78B93C8168610ECC-03.image.bin').read_bytes()
    desc = (a.capture / 'spurs-job-45017080-78B93C8168610ECC-03.desc.bin').read_bytes()
    if sha(image) != '3eee051c8a43fb010503792f592fc820d9caaaa7d57ce0de8662d2a75a6b42fd' or sha(desc) != 'e85625f42f2f9a1d8bbb83aaf62b4fd4adb92e1d05098c919fc2b1ab05672700':
        p.error('capture identity mismatch')
    generation = json.loads((a.recovery / 'generation.private.json').read_text())
    if (generation['base'] != 0x8000 or generation['unsupported_mnemonics'] or
            generation['missing_direct_targets'] or generation['strict_chunk_failure_sites']):
        p.error('incomplete strict translation')
    payload = {}
    previous = json.loads((a.previous / 'profile-manifest.json').read_text())
    for name, expected in previous['files'].items():
        data = (a.previous / name).read_bytes()
        if sha(data) != expected:
            p.error('previous input drift: ' + name)
        payload[name] = data
    # Refresh ABI headers from the SDK used to compile, preserving the generated
    # source's relative include layout. No protected input path is changed.
    for directory in ('include/ps3emu', 'runtime/spu', 'runtime/platform'):
        for path in (a.sdk / directory).glob('*.h'):
            payload[directory + '/' + path.name] = path.read_bytes()
    for name in ('image03_generated.c', 'image03_generated.h'):
        data = (a.recovery / name).read_bytes()
        if sha(data) != generation['generated'][name]['sha256']:
            p.error('generated input drift')
        payload['cases/image03/' + name] = data
    old = previous['native_translation_unit']
    payload[old] = payload[old].replace(b'int folklore_register_native_jobs(void)', b'int folklore_register_previous_jobs(void)')
    targets = ',\n'.join('{0x%X,image03_b8000_spu_func_%08X}' % (0x8000 + lo, 0x8000 + lo)
                          for lo, _ in generation['chunks_relative'])
    unit = '#include "../../runtime/spu/spurs_native_registry.h"\n#include "image03_generated.c"\n'
    unit += array('image03_bytes', image) + array('image03_desc', desc)
    unit += 'static const spurs_native_target image03_targets[]={\n' + targets + '\n};\n'
    unit += '''int folklore_register_previous_jobs(void);
int folklore_register_native_jobs(void)
{
 if(folklore_register_previous_jobs())return -1;
 spurs_native_definition d={0};const spurs_native_profile*p=0;
 d.image=image03_bytes;d.image_size=sizeof image03_bytes;
 d.descriptor_template=image03_desc;d.descriptor_size=sizeof image03_desc;
 d.entry=image03_b8000_spu_func_00008000;d.entry_pc=0x8000;
 d.layout=(spurs_native_layout){0x8000,0x6000,0x48F0,128,0x18000,0x4000,0x3FFF0};
 d.input.kind=SPURS_NATIVE_INPUT_GUEST_DMA;
 d.targets=(spurs_native_targets){image03_targets,sizeof image03_targets/sizeof *image03_targets};
'''
    unit += ' d.source_sha256="' + sha(payload['cases/image03/image03_generated.c']) + '";\n'
    unit += ' d.build_sha256="' + sha(image + desc + unit.encode()) + '";\n'
    unit += ' spurs_native_status s=spurs_native_registry_register(&d,&p);\n return s==SPURS_NATIVE_OK||s==SPURS_NATIVE_DUPLICATE?0:-1;\n}\n'
    selected = 'cases/image03/private_startup_jobs.c'
    extras = [] if a.generic_only else [old]
    extra_functions = []
    seen_images = set()
    for extra_index, extra_capture in enumerate(a.extra_capture):
        sys.path[:0] = [str(Path(__file__).parent / 'native-lane'), str(a.sdk / 'tools')]
        from recover_image03 import walk, generate_blocks
        from spu_disasm import spu_decode
        from spu_lifter import SPULifter
        raw = extra_capture.read_bytes()
        identity = sha(raw)
        if identity in seen_images:
            continue
        seen_images.add(identity)
        descriptor = extra_capture.with_name(extra_capture.name.replace('.image.bin', '.desc.bin')).read_bytes()
        if len(descriptor) != 128 or len(raw) != int.from_bytes(descriptor[8:10], 'big') * 16:
            p.error('additional descriptor/image size mismatch')
        try:
            reached,cfg=close_indirect_flow(raw,spu_decode,SPULifter._branch_target,walk)
        except ValueError as error:
            p.error(str(error))
        analysis = a.out.parent / (a.out.name + '-analysis-' + identity[:12])
        result = generate_blocks(raw, reached, cfg, analysis, 0x8000)
        if result['unsupported_mnemonics'] or result['missing_direct_targets'] or result['strict_chunk_failure_sites']:
            p.error('additional image has unsupported translation sites')
        prefix = 'image_' + sha(raw)[:12] + '_'
        directory = 'cases/image_' + identity[:12]
        for name in ('image03_generated.c', 'image03_generated.h'):
            payload[directory + '/' + name] = (analysis / name).read_text().replace('image03_b8000_', prefix).encode()
        extra_targets = ',\n'.join('{0x%X,%sspu_func_%08X}' % (0x8000 + lo, prefix, 0x8000 + lo)
                                   for lo, _ in result['chunks_relative'])
        extra_unit = '#include "../../runtime/spu/spurs_native_registry.h"\n#include "image03_generated.c"\n'
        extra_unit += array('extra_image', raw) + array('extra_desc', descriptor)
        extra_unit += 'static const spurs_native_target targets[]={\n' + extra_targets + '\n};\n'
        function = 'folklore_register_image_' + identity[:12]
        extra_functions.append(function)
        extra_unit += 'int ' + function + '(void) {\n'
        extra_unit += '''
 spurs_native_definition d={0};const spurs_native_profile*p=0;
 d.image=extra_image;d.image_size=sizeof extra_image;
 d.descriptor_template=extra_desc;d.descriptor_size=sizeof extra_desc;
 d.input.kind=SPURS_NATIVE_INPUT_GUEST_DMA;
 d.targets=(spurs_native_targets){targets,sizeof targets/sizeof *targets};
'''
        stack_base=max(0x18000,(0x8000+len(raw)+1023)&~1023)
        stack_size=int.from_bytes(descriptor[28:30],'big')*16
        if stack_size<48 or stack_base+stack_size>0x3FFF0:
            p.error('additional image has no disjoint raw-job stack layout')
        extra_unit += ' d.layout=(spurs_native_layout){0x8000,0x6000,0x48F0,128,0x%X,0x%X,0x3FFF0};\n' % (stack_base,stack_size)
        extra_unit += ' d.entry=' + prefix + 'spu_func_00008000;d.entry_pc=0x8000;\n'
        extra_unit += ' d.source_sha256="' + sha(raw) + '";d.build_sha256="' + sha(payload[directory + '/image03_generated.c']) + '";\n'
        extra_unit += ' spurs_native_status s=spurs_native_registry_register(&d,&p);return s==SPURS_NATIVE_OK||s==SPURS_NATIVE_DUPLICATE?0:-1;\n}\n'
        extra_path = directory + '/private_extra_jobs.c'
        payload[extra_path] = extra_unit.encode();extras.append(extra_path)
    unit = ''.join('int '+name+'(void);\n' for name in extra_functions) + unit.replace(
        'if(folklore_register_previous_jobs())return -1;',
        'if('+('0' if a.generic_only else 'folklore_register_previous_jobs()')+''.join('||'+name+'()' for name in extra_functions)+')return -1;')
    if a.generic_only:
        payload={name:data for name,data in payload.items() if not name.startswith('cases/b8000/')}
    payload[selected] = unit.encode()
    a.out.mkdir(parents=True)
    for name, data in payload.items():
        target = a.out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = {'schema': 'folklore-private-native-jobs-v1', 'native_translation_unit': selected,
                'extra_translation_units': extras, 'files': {n: sha(d) for n, d in sorted(payload.items())},
                'fallback_count': 0, 'image03_instruction_count': generation['instruction_count'],
                'runtime_tested': False}
    (a.out / 'profile-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'package': str(a.out), 'translation_units': [selected, *extras], 'fallback_count': 0}))


if __name__ == '__main__':
    main()
