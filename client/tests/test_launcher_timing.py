"""Player timing and persistent-cache contracts; no game launch required."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import launcher_core as core


class TimingContract(unittest.TestCase):
    def test_old_settings_keep_original_timing(self):
        old = core.default_config()
        old['schema_version'] = 3
        del old['display']['frame_rate']
        del old['graphics']['path_tracing']
        del old['graphics']['ray_denoiser']
        del old['graphics']['path_strength']
        del old['graphics']['texture_quality']
        del old['graphics']['dlss']
        del old['graphics']['sharpen']
        del old['textures']
        old['display']['width'] = 3440
        migrated, version = core.migrate_config(old)
        self.assertEqual(version, 3)
        self.assertEqual(migrated['display']['frame_rate'], 30)
        for section in ('paths', 'build', 'appearance'):
            self.assertEqual(migrated[section], old[section])
        self.assertEqual(migrated['graphics'], {**old['graphics'], 'path_tracing': 'off',
                                                'ray_denoiser': 'spatial', 'path_strength': '35',
                                                'texture_quality': 'low', 'dlss': 'off', 'sharpen': '0'})
        self.assertEqual(migrated['display']['width'], 3440)
        self.assertNotIn('frame_rate', old['display'])
        core.validate_config(migrated)

    def test_declared_60_requires_compiled_frame_bridge(self):
        data = (Path(__file__).resolve().parents[1] / core.CAPABILITY_FILE).read_bytes()
        self.assertFalse(core.parse_capability_record(data, True, False)['frame_rate_60'])
        self.assertTrue(core.parse_capability_record(data, True, True)['frame_rate_60'])
        record = json.loads(data)
        record['environment']['FOLKLORE_FRAME_RATE'] = '60'
        with self.assertRaises(ValueError):
            core.parse_capability_record(json.dumps(record).encode(), True, True)

    def test_explicit_timing_and_cache_ignore_injected_parent_values(self):
        config = core.default_config()
        root = Path(__file__).resolve().parent / 'timing-fixture'
        config['paths'] = {k: str(root / k) for k in core.PATH_NAMES}
        config['paths']['hdd0'] = str(root / 'profile' / 'dev_hdd0')
        parent = {'FOLKLORE_FRAME_RATE': '120', 'PS3RECOMP_SHADER_CACHE_DIR': 'untrusted',
                  'SPURS_JOB_TIMING': '1', 'AUDIO_BLOCK_TRACE': 'untrusted', 'PATH': ''}
        caps = core.Capabilities(frame_rate_60=True, shader_cache=True)
        env = core.player_environment(config, caps, parent)
        self.assertNotIn('FOLKLORE_FRAME_RATE', env)
        self.assertNotIn('AUDIO_BLOCK_TRACE', env)
        self.assertNotIn('SPURS_JOB_TIMING', env)
        self.assertEqual(Path(env['PS3RECOMP_SHADER_CACHE_DIR']), root / 'profile' / 'shader-cache')
        config['display']['frame_rate'] = 60
        self.assertEqual(core.player_environment(config, caps, parent)['FOLKLORE_FRAME_RATE'], '60')
        fallback = core.player_environment(config, core.ACCEPTED, parent)
        self.assertNotIn('FOLKLORE_FRAME_RATE', fallback)
        self.assertNotIn('PS3RECOMP_SHADER_CACHE_DIR', fallback)

    def test_only_supported_integer_rates_are_storable(self):
        for rate in (0, 59, 120, '60', True, None):
            config = core.default_config()
            config['display']['frame_rate'] = rate
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                core.validate_config(config)

    def test_malformed_legacy_display_is_a_validation_error(self):
        for display in (None, {}, {'frame_rate': 60}):
            old = core.default_config()
            old.update(schema_version=3, display=display)
            with self.subTest(display=display), self.assertRaises(ValueError):
                core.migrate_config(old)


if __name__ == '__main__':
    unittest.main()
