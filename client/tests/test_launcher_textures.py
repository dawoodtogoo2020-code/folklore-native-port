import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'launcher'))
import launcher_core as core  # noqa: E402
import folklore_launcher as ui  # noqa: E402


class TextureLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = core.default_config()
        self.caps = core.Capabilities(origin='build-record', texture_scales=(2, 4))
        self.pack = self.root / 'texture-pack'
        self.pack.mkdir()
        for tier in ('medium', 'high'):
            (self.pack / tier).mkdir()
        self.config['textures']['pack_root'] = str(self.pack)
        self.index = self.pack / 'pack-index.json'
        self.index_data = {'schema_version': 1, 'tiers': {
            'medium': {'scale': 2, 'path': 'medium', 'complete': True},
            'high': {'scale': 4, 'path': 'high', 'complete': True}}}
        self.write_index()

    def write_index(self):
        self.index.write_text(json.dumps(self.index_data), encoding='utf-8')

    def test_schema_six_migrates_without_losing_fields(self):
        old = copy.deepcopy(self.config)
        old['schema_version'] = 6
        old['graphics'].pop('texture_quality')
        old['graphics'].pop('dlss')
        old['graphics'].pop('sharpen')
        old.pop('textures')
        old['display']['width'] = 3440
        old['graphics']['path_tracing'] = 'diffuse-preview'
        old['graphics']['path_strength'] = '100'
        old['appearance']['theme'] = 'ember'
        old['build']['source'] = 'D:\\existing-source'
        migrated, version = core.migrate_config(old)
        self.assertEqual(version, 6)
        self.assertEqual(migrated['schema_version'], core.SCHEMA_VERSION)
        self.assertEqual(migrated['graphics']['texture_quality'], 'low')
        self.assertEqual(migrated['textures'], {'pack_root': ''})
        self.assertEqual(migrated['graphics']['path_tracing'], 'diffuse-preview')
        self.assertEqual(migrated['graphics']['path_strength'], '100')
        for section in ('paths', 'display', 'build', 'appearance'):
            self.assertEqual(migrated[section], old[section])
        self.assertEqual(old['schema_version'], 6)
        core.validate_config(migrated)

    def test_low_clears_inherited_pack_variables(self):
        self.config['graphics']['texture_quality'] = 'low'
        self.assertIsNone(core.resolve_texture_pack(self.config, core.ACCEPTED))
        env = core.player_environment(self.config, core.ACCEPTED,
                                      parent={'PATH': 'dummy', 'FOLKLORE_TEXTURE_PACK': 'bad',
                                              'FOLKLORE_TEXTURE_SCALE': '4'})
        self.assertNotIn('FOLKLORE_TEXTURE_PACK', env)
        self.assertNotIn('FOLKLORE_TEXTURE_SCALE', env)

    def test_medium_and_high_set_exact_environment(self):
        for quality, scale in (('medium', 2), ('high', 4)):
            with self.subTest(quality=quality):
                self.config['graphics']['texture_quality'] = quality
                directory, actual = core.resolve_texture_pack(self.config, self.caps)
                self.assertEqual((directory, actual), (self.pack / quality, scale))
                env = core.player_environment(self.config, self.caps,
                                              parent={'FOLKLORE_TEXTURE_PACK': 'inherited',
                                                      'FOLKLORE_TEXTURE_SCALE': '9'})
                self.assertEqual(env['FOLKLORE_TEXTURE_PACK'], str(directory))
                self.assertEqual(env['FOLKLORE_TEXTURE_SCALE'], str(scale))

    def test_old_binary_and_missing_pack_refused_without_fallback(self):
        self.config['graphics']['texture_quality'] = 'high'
        with self.assertRaisesRegex(ValueError, 'verified build'):
            core.intended_environment(self.config, core.ACCEPTED)
        self.config['textures']['pack_root'] = ''
        with self.assertRaisesRegex(ValueError, 'Choose a texture pack root'):
            core.intended_environment(self.config, self.caps)
        self.config['textures']['pack_root'] = str(self.pack)
        self.index_data['tiers']['high']['complete'] = False
        self.write_index()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            core.intended_environment(self.config, self.caps)
        self.assertIn('incomplete', core.texture_availability(self.config, self.caps)['high'])

    def test_pack_path_traversal_and_symlink_refused(self):
        self.config['graphics']['texture_quality'] = 'medium'
        for path in ('../outside', str(self.root / 'outside'), ''):
            self.index_data['tiers']['medium']['path'] = path
            self.write_index()
            with self.subTest(path=path), self.assertRaises(ValueError):
                core.resolve_texture_pack(self.config, self.caps)
        link = self.pack / 'linked'
        try:
            link.symlink_to(self.pack / 'medium', target_is_directory=True)
        except (OSError, NotImplementedError):
            pass  # Windows without symlink privilege; traversal checks above still run.
        else:
            self.index_data['tiers']['medium']['path'] = 'linked'
            self.write_index()
            with self.assertRaisesRegex(ValueError, 'link'):
                core.resolve_texture_pack(self.config, self.caps)

    def test_capability_record_requires_both_texture_flags(self):
        record = json.loads((ROOT / 'native' / 'launcher-capabilities.json').read_text(encoding='utf-8'))
        parsed = core.parse_capability_record(json.dumps(record).encode(), bridge_built=True)
        self.assertEqual(parsed['texture_scales'], (2, 4))
        record['environment']['FOLKLORE_TEXTURE_SCALE'] = False
        with self.assertRaisesRegex(ValueError, 'both environment flags'):
            core.parse_capability_record(json.dumps(record).encode(), bridge_built=True)

    def test_ui_labels_and_availability_reason(self):
        self.assertEqual(set(ui.TEXTURE_LABELS), {'low', 'medium', 'high'})
        self.assertFalse(any('Texture quality and texture packs' == title for title, _ in ui.NOT_AVAILABLE))
        app = ui.App.__new__(ui.App)
        app.config = copy.deepcopy(self.config)
        app.caps = core.ACCEPTED
        app.v = {'texture_quality': Mock(), 'pack_root': Mock()}
        app.v['texture_quality'].get.return_value = 'high'
        app.v['pack_root'].get.return_value = str(self.pack)
        app.texture_radios = {quality: Mock() for quality in ui.TEXTURE_LABELS}
        app.texture_hint = Mock()
        app.validate_settings = Mock()
        app.update_texture_controls()
        app.texture_radios['low'].state.assert_called_with(['!disabled'])
        app.texture_radios['high'].state.assert_called_with(['disabled'])
        self.assertIn('verified build', app.texture_hint.configure.call_args.kwargs['text'])


if __name__ == '__main__':
    unittest.main()

