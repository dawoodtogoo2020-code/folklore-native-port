import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import stat
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('build_owned_backup',
    Path(__file__).resolve().parents[1] / 'scripts/build_owned_backup.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuilderConfig(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        values = {n: self.root / n for n in ('elf', 'sdk', 'metadata', 'ppu_manifest',
                   'spu_manifest', 'toolchain', 'prefix', 'shader_dir', 'source', 'output')}
        for name in ('sdk', 'metadata', 'toolchain', 'prefix', 'shader_dir', 'source'):
            values[name].mkdir()
        values['elf'].write_bytes(b'synthetic')
        (values['metadata'] / 'EBOOT.functions.bounded.json').write_text('[]')
        (values['shader_dir'] / 'manifest.json').write_text('{}')
        (values['source'] / 'native').mkdir()
        (values['source'] / 'native/CMakeLists.txt').write_text('')
        for name in ('clang.exe', 'clang++.exe', 'cmake.exe', 'ninja.exe'):
            (values['toolchain'] / name).write_bytes(b'fixture only')
        values['ppu_manifest'].write_text(json.dumps({'files': [{'name': f'unit{i}.cpp'} for i in range(26)]}))
        values['spu_manifest'].write_text(json.dumps({'files': {f'cases/{i}': '0'*64 for i in range(27)}}))
        self.args = argparse.Namespace(**values, jobs=2, resume=False, prepare_only=True)

    def generator_fixture(self):
        scripts = self.args.source / 'scripts'
        scripts.mkdir()
        (scripts / 'native-lane').mkdir()
        for name in ('build_owned_backup.py', 'regenerate_ppu.py', 'regenerate_spu.py',
                     'prepare_startup_aot.py', 'prepare_camera_aspect.py', 'prepare_frame_limiter.py',
                     'native-lane/recover_image03.py'):
            (scripts / name).write_bytes(b'generator fixture')
        tools = self.args.sdk / 'tools'
        tools.mkdir()
        for name in ('ppu_lifter.py', 'ppu_disasm.py', 'elf_parser.py', 'spu_lifter.py', 'spu_disasm.py'):
            (tools / name).write_bytes(b'tool fixture')
        (self.args.metadata / 'EBOOT.imports.json').write_text('[]')
        return scripts

    def test_launcher_edit_keeps_generation_fingerprint(self):
        scripts = self.generator_fixture()
        launcher = self.args.source / 'launcher.py'
        launcher.write_text('first')
        with mock.patch.object(builder, 'HERE', scripts), mock.patch.object(builder.subprocess, 'check_output', return_value=b'historical blob'):
            before = builder.generation_inputs(vars(self.args))
            launcher.write_text('unrelated launcher improvement')
            self.assertEqual(before, builder.generation_inputs(vars(self.args)))

    def test_generator_edit_invalidates_fingerprint(self):
        scripts = self.generator_fixture()
        with mock.patch.object(builder, 'HERE', scripts), mock.patch.object(builder.subprocess, 'check_output', return_value=b'historical blob'):
            before = builder.generation_inputs(vars(self.args))
            (scripts / 'regenerate_ppu.py').write_text('changed translator control')
            self.assertNotEqual(before, builder.generation_inputs(vars(self.args)))

    def test_receipt_traversal_rejected_before_output_read(self):
        root = self.root / 'ppu'
        root.mkdir()
        (root / 'receipt.json').write_text(json.dumps({'state': 'verified', 'overlay_files': [
            {'name': '../../outside', 'actual': '0'*64, 'match': True}]}))
        with mock.patch.object(builder, 'sha') as hashing:
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                builder.check_verified(root, 'ppu')
            hashing.assert_not_called()

    def test_spu_receipt_traversal_rejected_before_output_read(self):
        root = self.root / 'spu'
        (root / 'profile').mkdir(parents=True)
        (root / 'profile/profile-manifest.json').write_text(json.dumps({'files': {'../../outside': '0'*64}}))
        (root / 'receipt.json').write_text(json.dumps({'state': 'verified', 'files': []}))
        with mock.patch.object(builder, 'sha') as hashing:
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                builder.check_verified(root, 'spu')
            hashing.assert_not_called()

    def test_valid_config_is_read_only(self):
        if self.root.drive.casefold() != 'd:':
            self.skipTest('D: output policy')
        paths = builder.validate(self.args)
        self.assertEqual(paths['output'], self.args.output)
        self.assertFalse(self.args.output.exists())

    def test_relative_input_rejected(self):
        self.args.sdk = Path('relative-sdk')
        with self.assertRaisesRegex(ValueError, 'absolute'):
            builder.validate(self.args)

    def test_non_d_output_rejected(self):
        self.args.output = Path('C:/private-build')
        with self.assertRaisesRegex(ValueError, 'D:'):
            builder.validate(self.args)

    def test_output_reparse_attribute_rejected(self):
        self.args.output.mkdir()
        original = Path.lstat
        def attributes(path, *args, **kwargs):
            if path == self.args.output:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'lstat', attributes):
            with self.assertRaisesRegex(ValueError, 'reparse'):
                builder.plain_output(self.args.output)

    def test_writable_file_reparse_rejected_before_checkpoint_read(self):
        self.args.output.mkdir()
        self.args.resume = True
        checkpoint = self.args.output / 'checkpoint.json'
        checkpoint.write_text('{"not_read":true}')
        original = Path.lstat
        for name in ('checkpoint.json', 'regenerate-ppu.log', 'regenerate-spu.log',
                     'configure.log', 'build.log'):
            leaf = self.args.output / name
            if not leaf.exists():
                leaf.write_bytes(b'unchanged')
            def attributes(path, *args, **kwargs):
                if path == leaf:
                    return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
                return original(path, *args, **kwargs)
            with self.subTest(name=name), mock.patch.object(Path, 'lstat', attributes):
                with mock.patch.object(builder, 'load') as reading:
                    with self.assertRaisesRegex(ValueError, 'reparse'):
                        builder.execute(self.args)
                    reading.assert_not_called()
            self.assertEqual(checkpoint.read_text(), '{"not_read":true}')

    def test_mutable_directory_reparse_rejected_before_traversal(self):
        self.args.output.mkdir()
        original = Path.lstat
        for name in ('tmp', 'native-build'):
            leaf = self.args.output / name
            leaf.mkdir()
            def attributes(path, *args, **kwargs):
                if path == leaf:
                    return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                return original(path, *args, **kwargs)
            with self.subTest(name=name), mock.patch.object(Path, 'lstat', attributes):
                with mock.patch.object(builder.os, 'scandir') as scanning:
                    with self.assertRaisesRegex(ValueError, 'reparse'):
                        builder.validate_writable_children(self.args.output)
                    # tmp can be visited when checking native-build, but the
                    # guarded reparse directory itself must never be opened.
                    self.assertNotIn(mock.call(leaf), scanning.call_args_list)

    def test_existing_nested_junction_rejected_without_touching_target(self):
        if os.name != 'nt':
            self.skipTest('Windows junction test')
        target = self.root / 'junction-target'
        target.mkdir()
        sentinel = target / 'preserve.txt'
        sentinel.write_text('preserve target')
        parent = self.args.output / 'native-build/nested'
        parent.mkdir(parents=True)
        link = parent / 'external'
        created = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if created.returncode:
            self.skipTest('Windows did not permit a temporary junction')
        # Remove only the fixture's junction entry, never traverse its target.
        self.addCleanup(os.rmdir, link)
        with self.assertRaisesRegex(ValueError, 'reparse'):
            builder.validate(self.args)
        self.assertEqual(sentinel.read_text(), 'preserve target')

    def test_overlap_rejected(self):
        if self.root.drive.casefold() != 'd:':
            self.skipTest('D: output policy')
        self.args.output = self.args.sdk / 'out'
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            builder.validate(self.args)

    def test_missing_tool_rejected(self):
        if self.root.drive.casefold() != 'd:':
            self.skipTest('D: output policy')
        (self.args.toolchain / 'clang.exe').unlink()
        with self.assertRaisesRegex(ValueError, 'development tool'):
            builder.validate(self.args)

    def test_manifest_traversal_rejected(self):
        if self.root.drive.casefold() != 'd:':
            self.skipTest('D: output policy')
        values = {f'cases/{i}': '0'*64 for i in range(26)}
        values['cases/../../escape'] = '0'*64
        self.args.spu_manifest.write_text(json.dumps({'files': values}))
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            builder.validate(self.args)

    def test_resume_input_drift_rejected_before_actions(self):
        if self.root.drive.casefold() != 'd:':
            self.skipTest('D: output policy')
        self.args.resume = True
        self.args.output.mkdir()
        (self.args.output / 'checkpoint.json').write_text(json.dumps({'state': 'prepared', 'inputs': {}}))
        with mock.patch.object(builder, 'generation_inputs', return_value={'changed': 'hash'}):
            with self.assertRaisesRegex(ValueError, 'Resume inputs differ'):
                builder.execute(self.args)

    def test_changed_verified_ppu_output_rejected(self):
        root = self.root / 'ppu'
        (root / 'overlay').mkdir(parents=True)
        (root / 'overlay/unit.cpp').write_bytes(b'changed')
        (root / 'receipt.json').write_text(json.dumps({'state': 'verified', 'overlay_files': [
            {'name': 'unit.cpp', 'actual': '0'*64, 'match': True}]}))
        with self.assertRaisesRegex(ValueError, 'PPU output changed'):
            builder.check_verified(root, 'ppu')

    def test_configuration_uses_only_new_generated_paths(self):
        paths = vars(self.args).copy()
        profile = self.args.output / 'spu/profile'
        profile.mkdir(parents=True)
        (profile / 'profile-manifest.json').write_text(json.dumps({
            'native_translation_unit': 'cases/main.c', 'extra_translation_units': ['cases/extra.c']}))
        command = builder.configure_command(paths, 'python-fixture')
        self.assertIn('-DRECOMP_DIR=' + str(self.args.output / 'ppu/overlay'), command)
        self.assertIn('-DFOLKLORE_PRIVATE_NATIVE_JOB_SOURCE=' + str(profile / 'cases/main.c'), command)
        self.assertIn('-DPython3_EXECUTABLE=python-fixture', command)
        self.assertIn('-DFOLKLORE_CAMERA_ASPECT_BRIDGE=ON', command)


if __name__ == '__main__':
    unittest.main()
