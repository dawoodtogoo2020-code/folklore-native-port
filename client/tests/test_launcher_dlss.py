"""Launcher-to-runtime DLSS contract, including unavailable/mismatched runtimes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import launcher_core as core


class DLSSContract(unittest.TestCase):
    def setUp(self):
        self.config = core.default_config()
        record = Path(__file__).resolve().parents[1] / core.CAPABILITY_FILE
        self.caps = core.Capabilities(**core.parse_capability_record(record.read_bytes(), True, True))

    def test_schema7_preserves_choices_and_disables_dlss_by_default(self):
        old = copy.deepcopy(self.config)
        old['schema_version'] = 7
        del old['graphics']['dlss']
        del old['graphics']['sharpen']
        old['display']['width'] = 3440
        new, version = core.migrate_config(old)
        core.validate_config(new)
        self.assertEqual(version, 7)
        self.assertEqual(new['display'], old['display'])
        self.assertEqual(new['graphics']['dlss'], 'off')
        self.assertEqual(new['graphics']['sharpen'], '0')
        self.assertNotIn('dlss', old['graphics'])

    def test_exact_modes_reach_game_and_use_final_compositor(self):
        self.config['graphics'].update(path_tracing='hybrid', sharpen='37')
        for mode in core.GRAPHICS_CHOICES['dlss']:
            self.config['graphics']['dlss'] = mode
            args = core.player_arguments(self.config, self.caps)
            self.assertIn('--dlss=' + mode, args)
            self.assertIn('--path-tracing=hybrid', args)
            self.assertIn('--sharpen=37', args)
            self.assertIn('--upscaler=' + ('off' if mode == 'off' else 'fsr1'), args)
            self.assertEqual(sum(a.startswith('--upscaler=') for a in args), 1)

    def test_old_builds_receive_no_new_flags(self):
        args = core.player_arguments(self.config, core.ACCEPTED)
        self.assertFalse(any(a.startswith(('--dlss=', '--sharpen=')) for a in args))
        self.config['graphics']['dlss'] = 'quality'
        with self.assertRaisesRegex(ValueError, 'dlss'):
            core.player_arguments(self.config, core.ACCEPTED)

    def test_dlss_and_ao_conflict_is_rejected(self):
        self.config['graphics'].update(dlss='quality', ambient_occlusion='cacao')
        with self.assertRaisesRegex(ValueError, 'DLSS requires'):
            core.validate_config(self.config)

    def test_runtime_requires_release_receipt_and_matching_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binary = root / 'FolkloreBoot.exe'
            self.assertFalse(core.dlss_runtime_available(binary))
            for name in ('nvngx_dlss.dll', 'rsx_dlss_vk.dll'):
                (root / name).write_bytes(name.encode())
            record = {'schema_version': 1, 'variant': 'release',
                      'sha256': {n: core.sha256_file(root / n)
                                 for n in ('nvngx_dlss.dll', 'rsx_dlss_vk.dll')}}
            receipt = root / 'dlss-runtime.json'
            receipt.write_text(json.dumps(record))
            self.assertTrue(core.dlss_runtime_available(binary))
            record['variant'] = 'development'
            receipt.write_text(json.dumps(record))
            self.assertFalse(core.dlss_runtime_available(binary))
            record['variant'] = 'release'
            receipt.write_text(json.dumps(record))
            (root / 'nvngx_dlss.dll').write_bytes(b'changed')
            self.assertFalse(core.dlss_runtime_available(binary))

    def test_installed_build_uses_local_snapshot_without_developer_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binary = root / 'FolkloreBoot.exe'
            binary.write_bytes(b'local-test-executable')
            data = (Path(__file__).resolve().parents[1] / core.CAPABILITY_FILE).read_bytes()
            (root / 'launcher-capabilities.json').write_bytes(data)
            receipt = {'schema_version': 1, 'binary_sha256': core.sha256_file(binary),
                       'capability_sha256': core.sha256_file(root / 'launcher-capabilities.json'),
                       'camera_bridge': True, 'frame_bridge': True, 'source_commits': {}}
            (root / 'installed-build.json').write_text(json.dumps(receipt))
            caps = core.discover_capabilities(binary)
            self.assertEqual(caps.origin, 'build-record')
            self.assertTrue(caps.frame_rate_60)
            self.assertIn('hybrid', caps.graphics['path_tracing'])
            self.assertEqual(caps.graphics['dlss'], ('off',))
            binary.write_bytes(b'different-build')
            self.assertEqual(core.discover_capabilities(binary).origin, 'accepted-default')


if __name__ == '__main__':
    unittest.main()
