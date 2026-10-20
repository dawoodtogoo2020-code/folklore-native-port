import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

LAUNCHER = Path(__file__).resolve().parents[1] / 'launcher'
MODULE = LAUNCHER / 'folklore_launcher.py'
sys.path.insert(0, str(LAUNCHER))
spec = importlib.util.spec_from_file_location('folklore_launcher', MODULE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
import launcher_core as core  # noqa: E402  (same module object the launcher imports)

REPO = Path(__file__).resolve().parents[1]
DISPLAY_RECORD = {
    'format': 'folklore-launcher-capabilities', 'version': 1, 'comment': 'test',
    'native_settings_version': 7,
    'cli': {'output_size': True, 'render_scale': [1, 4], 'anisotropy': ['auto', '1', '2', '4', '8', '16'],
            'anti_aliasing': ['off', 'fxaa'], 'upscaler': ['off', 'fsr1'], 'ambient_occlusion': ['off', 'cacao'],
            'hud_scale': [50, 100], 'camera_aspect': ['original', 'output']},
    'environment': {'PS3RECOMP_VK_DISPLAY': True, 'PS3RECOMP_VK_FULLSCREEN': True}}
DISPLAY_CAPS = core.Capabilities(origin='build-record', hud_scale=(50, 100), camera_aspects=('original', 'output'),
                                 monitor_select=True, fullscreen=True, native_settings_version=7)
SCHEMA1 = {'schema_version': 1,
           'paths': {'binary': '', 'elf': '', 'disc': '', 'hdd0': '', 'dev_flash': ''},
           'graphics': {'width': 2560, 'height': 1080, 'render_scale': 2, 'anisotropy': '16',
                        'anti_aliasing': 'fxaa', 'upscaler': 'fsr1', 'ambient_occlusion': 'cacao'}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = launcher.default_config()
        for name in ('binary', 'elf'):
            p = self.root / (name + ' space & token.exe')
            p.write_bytes(b'synthetic test only')
            self.config['paths'][name] = str(p)
        for name in ('disc', 'hdd0', 'dev_flash'):
            p = self.root / 'profile' / 'dev_hdd0' if name == 'hdd0' else self.root / name
            p.mkdir(parents=True)
            self.config['paths'][name] = str(p)
        (self.root / 'disc' / 'PS3_GAME').mkdir()
        (self.root / 'disc' / 'PS3_DISC.SFB').write_bytes(b'synthetic')


class ConfigValidation(Fixture):
    def test_defaults_validate(self):
        launcher.validate_config(launcher.default_config())

    def test_normalized_copy_is_independent(self):
        result = launcher.validate_config(self.config)
        result['display']['width'] = 1
        self.assertEqual(self.config['display']['width'], 1280)

    def test_invalid_types_values_and_unknown_keys(self):
        for key, value in [('schema_version', 1), ('schema_version', 2), ('schema_version', core.SCHEMA_VERSION + 1), ('schema_version', True),
                           ('unexpected', 1)]:
            with self.subTest(key=key, value=value):
                c = copy.deepcopy(self.config)
                c[key] = value
                with self.assertRaises(ValueError):
                    launcher.validate_config(c)
        cases = {'display': {'width': [0, 8193, -1, True, '1280', 1.5, None], 'height': [0, 8193, False, '720'],
                             'render_scale': [0, 5, True, 1.5], 'hud_scale': [49, 101, 75.0, '75', True],
                             'monitor': [-1, 64, '0', None], 'window_mode': ['exclusive', '', None],
                             'camera_aspect': ['wide', 'Output', None], 'unknown': [1]},
                 'graphics': {'anisotropy': ['3', 16, None], 'anti_aliasing': ['taa', 'FXAA', False],
                              'upscaler': ['dlss', 'fsr2', 0], 'ambient_occlusion': ['ssao', 'rt', None],
                              'path_tracing': ['full', 'on', None],
                              'unknown': [1]},
                 'build': {'output': [3, None], 'unknown': ['']},
                 'appearance': {'theme': ['Doolin', 'random', '', None, 1], 'unknown': ['x']}}
        for section, fields in cases.items():
            for key, values in fields.items():
                for value in values:
                    with self.subTest(section=section, key=key, value=value):
                        c = copy.deepcopy(self.config)
                        c[section][key] = value
                        with self.assertRaises(ValueError):
                            launcher.validate_config(c)

    def test_supported_options_and_boundaries(self):
        valid = {'display': {'width': [1, 8192], 'height': [1, 4096], 'render_scale': [1, 2, 3, 4],
                             'hud_scale': [50, 75, 100], 'monitor': [0, 63],
                             'window_mode': ['windowed', 'fullscreen'], 'camera_aspect': ['original', 'output']},
                 'graphics': {k: list(v) for k, v in core.GRAPHICS_CHOICES.items()}}
        for section, fields in valid.items():
            for key, values in fields.items():
                for v in values:
                    with self.subTest(key=key, value=v):
                        c = copy.deepcopy(self.config)
                        c[section][key] = v
                        launcher.validate_config(c)

    def test_preview_conflicts_with_ambient_occlusion(self):
        c = copy.deepcopy(self.config)
        c['graphics'].update(path_tracing='diffuse-preview', ambient_occlusion='rayquery')
        with self.assertRaisesRegex(ValueError, 'requires ambient occlusion Off'):
            launcher.validate_config(c)

    def test_preview_requires_proven_build_and_emits_selected_denoiser(self):
        c = copy.deepcopy(self.config)
        c['graphics']['path_tracing'] = 'diffuse-preview'
        with self.assertRaisesRegex(ValueError, 'path tracing'):
            core.player_arguments(c, core.ACCEPTED)
        record = (REPO / core.CAPABILITY_FILE).read_bytes()
        caps = core.Capabilities(origin='build-record', native_settings_version=9,
                                 graphics=core.parse_capability_record(record, False)['graphics'])
        args = core.player_arguments(c, caps)
        self.assertIn('--path-tracing=diffuse-preview', args)
        self.assertIn('--ray-denoiser=spatial', args)
        self.assertIn('--path-strength=35', args)
        c['graphics']['ray_denoiser'] = 'off'
        self.assertIn('--ray-denoiser=off', core.player_arguments(c, caps))
        for strength in ('20', '35', '60', '100'):
            c['graphics']['path_strength'] = strength
            self.assertIn(f'--path-strength={strength}', core.player_arguments(c, caps))
        prior = core.Capabilities(origin='build-record', native_settings_version=8,
                                  graphics={**caps.graphics, 'path_strength': ('35',)})
        self.assertFalse(any(a.startswith('--path-strength=') for a in core.player_arguments(c, prior)))

    def test_rayquery_ao_emits_spatial_or_off_denoiser_only_for_new_build(self):
        c = copy.deepcopy(self.config)
        c['graphics']['ambient_occlusion'] = 'rayquery'
        with self.assertRaisesRegex(ValueError, 'ambient occlusion'):
            core.player_arguments(c, core.ACCEPTED)
        record = (REPO / core.CAPABILITY_FILE).read_bytes()
        caps = core.Capabilities(origin='build-record', native_settings_version=8,
                                 graphics=core.parse_capability_record(record, False)['graphics'])
        args = core.player_arguments(c, caps)
        self.assertIn('--ambient-occlusion=rayquery', args)
        self.assertIn('--ray-denoiser=spatial', args)
        self.assertNotIn('--path-tracing=diffuse-preview', args)
        self.assertFalse(any(a.startswith('--path-strength=') for a in args))
        c['graphics']['ray_denoiser'] = 'off'
        self.assertIn('--ray-denoiser=off', core.player_arguments(c, caps))

    def test_schema4_preserves_choices_and_adds_preview_off(self):
        old = copy.deepcopy(self.config)
        old['schema_version'] = 4
        old['graphics'].pop('path_tracing')
        old['graphics'].pop('ray_denoiser')
        old['graphics'].pop('path_strength')
        old['graphics'].pop('texture_quality')
        old['graphics'].pop('dlss')
        old['graphics'].pop('sharpen')
        old.pop('textures')
        old['graphics']['ambient_occlusion'] = 'cacao'
        migrated, original = core.migrate_config(old)
        self.assertEqual(original, 4)
        self.assertEqual(migrated['graphics']['ambient_occlusion'], 'cacao')
        self.assertEqual(migrated['graphics']['path_tracing'], 'off')
        self.assertEqual(migrated['graphics']['ray_denoiser'], 'spatial')
        self.assertEqual(migrated['graphics']['path_strength'], '35')
        self.assertEqual(old['schema_version'], 4)

    def test_schema5_preserves_preview_and_adds_strength(self):
        old = copy.deepcopy(self.config)
        old['schema_version'] = 5
        old['graphics'].pop('path_strength')
        old['graphics'].pop('texture_quality')
        old['graphics'].pop('dlss')
        old['graphics'].pop('sharpen')
        old.pop('textures')
        old['graphics']['path_tracing'] = 'diffuse-preview'
        old['graphics']['ray_denoiser'] = 'off'
        migrated, version = core.migrate_config(old)
        self.assertEqual(version, 5)
        self.assertEqual(migrated['graphics']['path_tracing'], 'diffuse-preview')
        self.assertEqual(migrated['graphics']['ray_denoiser'], 'off')
        self.assertEqual(migrated['graphics']['path_strength'], '35')
        core.validate_config(migrated)
        self.assertEqual(old['schema_version'], 5)

    def test_output_pixel_ceiling_and_camera_ratio(self):
        for (w, h, camera), ok in {(8192, 4096, 'original'): True, (8192, 4097, 'original'): False,
                                   (8192, 8192, 'original'): False, (5120, 1440, 'output'): True,
                                   (5121, 1440, 'output'): False, (7680, 1440, 'original'): True,
                                   (1280, 1024, 'output'): True}.items():
            c = copy.deepcopy(self.config)
            c['display'].update(width=w, height=h, camera_aspect=camera)
            with self.subTest(w=w, h=h, camera=camera):
                if ok:
                    launcher.validate_config(c)
                else:
                    with self.assertRaises(ValueError):
                        launcher.validate_config(c)

    def test_missing_unknown_and_wrong_path_fields(self):
        for name in self.config['paths']:
            c = copy.deepcopy(self.config)
            c['paths'][name] = 123
            with self.assertRaises(ValueError):
                launcher.validate_config(c)
        c = copy.deepcopy(self.config)
        c['paths']['unknown'] = ''
        with self.assertRaises(ValueError):
            launcher.validate_config(c)

    def test_path_checks(self):
        launcher.validate_config(self.config, check_paths=True)
        for name in self.config['paths']:
            for v in ('', 'relative/path', str(self.root / 'absent')):
                with self.subTest(name=name, value=v):
                    c = copy.deepcopy(self.config)
                    c['paths'][name] = v
                    with self.assertRaises(ValueError):
                        launcher.validate_config(c, check_paths=True)

    def test_disc_marker_required(self):
        (self.root / 'disc' / 'PS3_DISC.SFB').unlink()
        with self.assertRaises(ValueError):
            launcher.build_command(self.config)

    def test_write_root_cannot_overlap_inputs(self):
        for value in (self.root, self.root / 'disc', self.root / 'disc' / 'PS3_GAME', self.root / 'dev_flash'):
            with self.subTest(value=value):
                c = copy.deepcopy(self.config)
                c['paths']['hdd0'] = str(value)
                with self.assertRaises(ValueError):
                    launcher.validate_config(c, check_paths=True)

    def test_directory_cannot_be_executable(self):
        c = copy.deepcopy(self.config)
        c['paths']['binary'] = str(self.root / 'disc')
        with self.assertRaises(ValueError):
            launcher.build_command(c)


class ConfigFiles(Fixture):
    def test_missing_config_returns_independent_defaults(self):
        path = self.root / 'missing.json'
        self.assertEqual(launcher.load_config(path), launcher.default_config())
        self.assertFalse(path.exists())

    def test_roundtrip_and_parent_creation(self):
        path = self.root / 'new' / 'config.json'
        self.assertIsNone(launcher.save_config(path, self.config))
        self.assertEqual(launcher.load_config(path), self.config)

    def test_invalid_save_does_not_replace_valid_config(self):
        path = self.root / 'config.json'
        launcher.save_config(path, self.config)
        original = path.read_bytes()
        invalid = copy.deepcopy(self.config)
        invalid['display']['width'] = 0
        with self.assertRaises(ValueError):
            launcher.save_config(path, invalid)
        self.assertEqual(path.read_bytes(), original)

    def test_invalid_load_preserves_file(self):
        path = self.root / 'config.json'
        for data in (b'{oops', b'[]', b'null', b'{}', b'x' * (1024 * 1024 + 1), b'{"schema_version": 0}'):
            with self.subTest(length=len(data)):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    launcher.load_config(path)
                self.assertEqual(path.read_bytes(), data)

    def test_schema1_migrates_without_losing_values(self):
        path = self.root / 'settings.json'
        old = copy.deepcopy(SCHEMA1)
        old['paths'] = copy.deepcopy(self.config['paths'])
        raw = json.dumps(old).encode()
        path.write_bytes(raw)
        config, info = launcher.load_config_info(path)
        self.assertEqual(info, {'exists': True, 'schema': 1, 'migrated': True})
        self.assertEqual(path.read_bytes(), raw, 'loading must not rewrite the file')
        self.assertEqual(config['paths'], old['paths'])
        self.assertEqual({k: config['display'][k] for k in ('width', 'height', 'render_scale')},
                         {'width': 2560, 'height': 1080, 'render_scale': 2})
        self.assertEqual(config['graphics'], {'anisotropy': '16', 'anti_aliasing': 'fxaa', 'upscaler': 'fsr1',
                                              'ambient_occlusion': 'cacao', 'path_tracing': 'off', 'ray_denoiser': 'spatial',
                                              'path_strength': '35', 'texture_quality': 'low', 'dlss': 'off', 'sharpen': '0'})
        self.assertEqual(config['textures'], {'pack_root': ''})
        self.assertEqual((config['display']['hud_scale'], config['display']['camera_aspect'],
                          config['display']['monitor'], config['display']['window_mode']),
                         (100, 'original', 0, 'windowed'))
        backup = launcher.save_config(path, config)
        self.assertEqual(backup, path.with_name('settings.schema1.json'))
        self.assertEqual(backup.read_bytes(), raw)
        self.assertEqual(json.loads(path.read_text())['schema_version'], core.SCHEMA_VERSION)
        self.assertEqual(json.loads(path.read_text())['appearance'], {'theme': 'doolin'})
        config['display']['hud_scale'] = 75
        self.assertIsNone(launcher.save_config(path, config), 'one schema 1 copy is enough')
        self.assertEqual(backup.read_bytes(), raw)
        self.assertEqual(launcher.load_config(path), config)

    def test_schema2_gains_default_appearance_and_keeps_everything_else(self):
        path = self.root / 'settings.json'
        old = copy.deepcopy(self.config)
        del old['appearance']
        del old['display']['frame_rate']
        del old['graphics']['path_tracing']
        del old['graphics']['ray_denoiser']
        del old['graphics']['path_strength']
        del old['graphics']['texture_quality']
        del old['graphics']['dlss']
        del old['graphics']['sharpen']
        del old['textures']
        old['schema_version'] = 2
        old['display'].update(hud_scale=75, camera_aspect='output', width=3440, height=1440)
        old['build']['output'] = 'D:\\out'
        raw = json.dumps(old).encode()
        path.write_bytes(raw)
        config, info = launcher.load_config_info(path)
        self.assertEqual(info['schema'], 2)
        self.assertEqual(config['appearance'], {'theme': 'doolin'})
        for section in ('paths', 'display', 'graphics', 'build'):
            expected = {**old[section], 'frame_rate': 30} if section == 'display' else (
                {**old[section], 'path_tracing': 'off', 'ray_denoiser': 'spatial',
                 'path_strength': '35', 'texture_quality': 'low', 'dlss': 'off', 'sharpen': '0'} if section == 'graphics' else old[section])
            self.assertEqual(config[section], expected, section)
        config['appearance']['theme'] = 'ember'
        self.assertEqual(launcher.save_config(path, config), path.with_name('settings.schema2.json'))
        self.assertEqual(path.with_name('settings.schema2.json').read_bytes(), raw)
        self.assertEqual(launcher.load_config(path)['appearance']['theme'], 'ember')
        for theme_id in core.THEMES:
            config['appearance']['theme'] = theme_id
            launcher.validate_config(config)

    def test_appearance_never_reaches_the_game(self):
        for theme_id in core.THEMES:
            c = copy.deepcopy(self.config)
            c['appearance']['theme'] = theme_id
            self.assertEqual(core.player_arguments(c, core.ACCEPTED), core.player_arguments(self.config, core.ACCEPTED))
            self.assertEqual(core.intended_environment(c, DISPLAY_CAPS), core.intended_environment(self.config, DISPLAY_CAPS))

    def test_schema1_with_unknown_fields_is_refused(self):
        path = self.root / 'settings.json'
        for mutate in (lambda d: d.update(extra=1), lambda d: d['graphics'].update(hud_scale=75),
                       lambda d: d['graphics'].pop('upscaler')):
            old = copy.deepcopy(SCHEMA1)
            mutate(old)
            path.write_text(json.dumps(old))
            with self.assertRaises(ValueError):
                launcher.load_config(path)

    def test_newer_schema_is_never_overwritten(self):
        path = self.root / 'settings.json'
        newer = launcher.default_config()
        newer.update(schema_version=core.SCHEMA_VERSION + 1, future={'frame_rate': 'whatever a newer launcher stores'})
        raw = json.dumps(newer).encode()
        path.write_bytes(raw)
        with self.assertRaises(launcher.NewerConfigError):
            launcher.load_config(path)
        with self.assertRaises(launcher.NewerConfigError):
            launcher.save_config(path, self.config)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if p.suffix == '.json'), ['settings.json'])

    def test_unreadable_file_is_kept_before_replacing(self):
        path = self.root / 'settings.json'
        path.write_bytes(b'{broken')
        backup = launcher.save_config(path, self.config)
        self.assertEqual(backup.read_bytes(), b'{broken')
        self.assertEqual(launcher.load_config(path), self.config)

    def test_check_cli_does_not_execute_binary(self):
        path = self.root / 'config.json'
        launcher.save_config(path, self.config)
        p = subprocess.run([sys.executable, str(MODULE), '--config', str(path), '--check'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn('disc marker', p.stderr)


def write_build(root, record=DISPLAY_RECORD, bridge=True, state='built', snapshot=True):
    """Synthetic owned-backup builder output (checkpoint + stand-in executable + source record)."""
    root = Path(root)
    source, output = root / 'source', root / 'output'
    (source / 'native').mkdir(parents=True, exist_ok=True)
    (output / 'native-build').mkdir(parents=True, exist_ok=True)
    exe = output / 'native-build' / 'FolkloreBoot.exe'
    exe.write_bytes(b'stand-in')
    rec = source / 'native' / 'launcher-capabilities.json'
    rec.write_text(json.dumps(record) if isinstance(record, dict) else record)
    flags = ['cmake', '-S', 'x'] + (['-DFOLKLORE_CAMERA_ASPECT_BRIDGE=ON'] if bridge else [])
    cp = {'state': state, 'binary': str(exe), 'binary_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
          'paths': {'source': str(source)}, 'steps': [{'name': 'configure', 'exit_code': 0, 'command': flags}],
          'build_source_snapshot': {str(rec): hashlib.sha256(rec.read_bytes()).hexdigest()} if snapshot else {}}
    (output / 'checkpoint.json').write_text(json.dumps(cp))
    return exe, rec, output / 'checkpoint.json'


class CapabilityDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def deployed_copy(self):
        declaration = json.loads((REPO / core.CAPABILITY_FILE).read_text(encoding='utf-8'))
        source_binary, source_record, checkpoint = write_build(self.root, record=declaration)
        installed = self.root / 'installed' / 'bin' / 'FolkloreBoot.exe'
        installed.parent.mkdir(parents=True)
        installed.write_bytes(source_binary.read_bytes())
        record = {'schema_version': 1,
                  'builder_checkpoint': str(checkpoint),
                  'builder_checkpoint_sha256': core.sha256_file(checkpoint),
                  'source_binary': str(source_binary),
                  'source_binary_sha256': core.sha256_file(source_binary),
                  'deployed_binary': str(installed),
                  'deployed_binary_sha256': core.sha256_file(installed)}
        deployment = installed.parent.parent / 'deployment.json'
        deployment.write_text(json.dumps(record), encoding='utf-8')
        return installed, source_binary, source_record, checkpoint, deployment, record

    def test_verified_deployed_copy_uses_original_build_capabilities(self):
        installed, _, _, _, _, _ = self.deployed_copy()
        caps = core.discover_capabilities(installed)
        self.assertEqual(caps.origin, 'build-record', caps.detail)
        self.assertEqual(caps.hud_scale, (50, 100))
        self.assertEqual(caps.texture_scales, (2, 4))

    def test_deployment_tampering_and_missing_source_fall_back(self):
        cases = {
            'installed binary': lambda files: files[0].write_bytes(b'changed'),
            'record hash': lambda files: files[4].write_text(files[4].read_text().replace(
                files[5]['deployed_binary_sha256'], '0' * 64)),
            'record path': lambda files: files[4].write_text(json.dumps({
                **files[5], 'deployed_binary': str(files[1])})),
            'original binary': lambda files: files[1].write_bytes(b'changed'),
            'missing original binary': lambda files: files[1].unlink(),
            'checkpoint': lambda files: files[3].write_text(files[3].read_text() + ' '),
            'source declaration': lambda files: files[2].write_text(files[2].read_text() + ' '),
        }
        for label, damage in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as folder:
                self.root = Path(folder).resolve()
                files = self.deployed_copy()
                damage(files)
                caps = core.discover_capabilities(files[0], binary_sha256=files[5]['deployed_binary_sha256'])
                self.assertEqual(caps.origin, 'accepted-default', (label, caps.detail))

    def test_no_record_uses_accepted_set(self):
        exe = self.root / 'FolkloreBoot.exe'
        exe.write_bytes(b'x')
        caps = core.discover_capabilities(exe)
        self.assertEqual(caps.origin, 'accepted-default')
        self.assertIsNone(caps.hud_scale)
        self.assertFalse(caps.camera_output or caps.monitor_select or caps.fullscreen)
        self.assertEqual(caps.binary_sha256, hashlib.sha256(b'x').hexdigest())
        self.assertEqual(core.discover_capabilities('').origin, 'accepted-default')

    def test_display_record_with_bridge(self):
        exe, _, _ = write_build(self.root)
        caps = core.discover_capabilities(exe)
        self.assertEqual(caps.origin, 'build-record', caps.detail)
        self.assertEqual((caps.hud_scale, caps.camera_aspects, caps.monitor_select, caps.fullscreen),
                         ((50, 100), ('original', 'output'), True, True))

    def test_profile_path_limit_defaults_and_can_be_declared(self):
        exe, _, _ = write_build(self.root)
        self.assertEqual(core.discover_capabilities(exe).profile_path_max, core.ACCEPTED_PROFILE_PATH_MAX)
        with tempfile.TemporaryDirectory() as d:
            record = copy.deepcopy(DISPLAY_RECORD)
            record['limits'] = {'profile_path_max': 1024}
            caps = core.discover_capabilities(write_build(d, record=record)[0])
            self.assertEqual((caps.origin, caps.profile_path_max), ('build-record', 1024))
        own = core.parse_capability_record((REPO / core.CAPABILITY_FILE).read_bytes(), bridge_built=False)
        # This source's SDK has 3a6914d; 160 is the longest dev_hdd0 path booted to
        # gameplay with it (200 already breaks Windows MAX_PATH for profile files).
        self.assertEqual(own['profile_path_max'], 160)

    def test_output_camera_requires_the_bridge_build_flag(self):
        exe, _, _ = write_build(self.root, bridge=False)
        caps = core.discover_capabilities(exe)
        self.assertEqual(caps.origin, 'build-record')
        self.assertEqual(caps.camera_aspects, ('original',))
        self.assertFalse(caps.camera_output)

    def test_any_provenance_gap_falls_back(self):
        cases = {
            'binary replaced after build': lambda exe, rec, cp: exe.write_bytes(b'other'),
            'record edited after build': lambda exe, rec, cp: rec.write_text(rec.read_text() + ' '),
            'record deleted': lambda exe, rec, cp: rec.unlink(),
            'checkpoint unreadable': lambda exe, rec, cp: cp.write_text('{'),
            'checkpoint names other exe': lambda exe, rec, cp: cp.write_text(
                cp.read_text().replace('FolkloreBoot.exe', 'Other.exe')),
        }
        for name, damage in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                exe, rec, cp = write_build(d)
                damage(exe, rec, cp)
                caps = core.discover_capabilities(exe)
                self.assertEqual(caps.origin, 'accepted-default', name)
                self.assertIsNone(caps.hud_scale)
        for kwargs in ({'state': 'configured'}, {'snapshot': False}):
            with self.subTest(**kwargs), tempfile.TemporaryDirectory() as d:
                self.assertEqual(core.discover_capabilities(write_build(d, **kwargs)[0]).origin, 'accepted-default')

    def test_invalid_records_are_refused(self):
        bad = []
        for mutate in (lambda r: r['cli'].update(hud_scale=[40, 100]), lambda r: r['cli'].update(upscaler=['off', 'dlss']),
                       lambda r: r['cli'].update(ambient_occlusion=['off', 'rayquery']), lambda r: r.update(frame_rate=60),
                       lambda r: r['cli'].update(camera_aspect=['output']), lambda r: r.update(version=2),
                       lambda r: r['environment'].update(PS3RECOMP_VK_DISPLAY='yes'), lambda r: r['cli'].update(render_scale=[1, 8])):
            record = copy.deepcopy(DISPLAY_RECORD)
            mutate(record)
            bad.append(record)
        for limits in ({'profile_path_max': 10}, {'profile_path_max': '1024'}, {'other': 1}, []):
            record = copy.deepcopy(DISPLAY_RECORD)
            record['limits'] = limits
            bad.append(record)
        bad.append('not json')
        for record in bad:
            with self.subTest(record=str(record)[:80]), tempfile.TemporaryDirectory() as d:
                caps = core.discover_capabilities(write_build(d, record=record)[0])
                self.assertEqual(caps.origin, 'accepted-default')
                self.assertIn('invalid', caps.detail)

    def test_this_source_declares_only_what_its_cli_accepts(self):
        """native/launcher-capabilities.json must track native/boot_main.cpp's options."""
        data = (REPO / core.CAPABILITY_FILE).read_bytes()
        fields = core.parse_capability_record(data, bridge_built=False)
        boot = (REPO / 'native' / 'boot_main.cpp').read_text(encoding='utf-8', errors='replace')
        usage = re.search(r'usage: FolkloreBoot[^"]*', boot).group(0)
        for flag, declared in (('--hud-scale=', fields['hud_scale']), ('--camera-aspect=', fields['camera_aspects'])):
            self.assertEqual(flag in usage, declared is not None, flag)
        # Monitor selection and fullscreen are SDK environment switches. The integrated
        # SDK (display 2cfdc38) reads PS3RECOMP_VK_DISPLAY and PS3RECOMP_VK_FULLSCREEN,
        # and this record is only trusted for builds of that SDK, so it declares both.
        self.assertTrue(fields['monitor_select'] and fields['fullscreen'])
        for key, values in fields['graphics'].items():
            self.assertIn(f'--{key.replace("_", "-")}=' +
                          ('0..100' if key == 'sharpen' else '|'.join(core.GRAPHICS_CHOICES[key])), usage)
            self.assertEqual(values, core.GRAPHICS_CHOICES[key])
        self.assertEqual(json.loads(data)['native_settings_version'],
                         int(re.search(r'"version=(\d+)', (REPO / 'native/graphics_settings.cpp').read_text()).group(1)))


class PlayerCommand(Fixture):
    def accepted_argv(self):
        return ['--contained-writes', '--vulkan', '--output-size=2560x1080', '--render-scale=2', '--anisotropy=16',
                '--anti-aliasing=fxaa', '--upscaler=fsr1', '--ambient-occlusion=cacao']

    def setUp(self):
        super().setUp()
        self.config['display'].update(width=2560, height=1080, render_scale=2)
        self.config['graphics'].update(anisotropy='16', upscaler='fsr1', ambient_occlusion='cacao')

    def test_argv_preserves_paths_and_maps_all_options(self):
        with patch.object(core, 'validate_backup', return_value={}) as validate:
            cmd = launcher.build_command(self.config)
        validate.assert_called_once_with(self.config['paths']['disc'], self.config['paths']['elf'])
        self.assertIsInstance(cmd, list)
        self.assertEqual(cmd[:5], [self.config['paths'][n] for n in ('binary', 'elf', 'disc', 'hdd0', 'dev_flash')])
        self.assertEqual(cmd[5:], self.accepted_argv())

    def test_accepted_build_never_receives_display_flags_but_keeps_stored_values(self):
        self.config['display'].update(hud_scale=75, camera_aspect='output', monitor=1, window_mode='fullscreen')
        with patch.object(core, 'validate_backup', return_value={}):
            cmd = launcher.build_command(self.config, core.ACCEPTED)
        self.assertEqual(cmd[5:], self.accepted_argv())
        env = core.player_environment(self.config, core.ACCEPTED, parent={'PS3RECOMP_VK_FULLSCREEN': '1',
                                                                          'PS3RECOMP_VK_DISPLAY': '1'})
        self.assertNotIn('PS3RECOMP_VK_DISPLAY', env)
        self.assertNotIn('PS3RECOMP_VK_FULLSCREEN', env)
        self.assertEqual(self.config['display']['hud_scale'], 75, 'stored newer preference is preserved')

    def test_display_build_receives_hud_camera_monitor_and_fullscreen(self):
        self.config['display'].update(hud_scale=75, camera_aspect='output', monitor=1, window_mode='fullscreen',
                                      width=3440, height=1440)
        with patch.object(core, 'validate_backup', return_value={}):
            cmd = launcher.build_command(self.config, DISPLAY_CAPS)
        self.assertEqual(cmd[-2:], ['--hud-scale=75', '--camera-aspect=output'])
        self.assertIn('--output-size=3440x1440', cmd)
        env = core.intended_environment(self.config, DISPLAY_CAPS)
        self.assertEqual((env['PS3RECOMP_VK_DISPLAY'], env['PS3RECOMP_VK_FULLSCREEN']), ('1', '1'))
        self.config['display']['window_mode'] = 'windowed'
        self.assertNotIn('PS3RECOMP_VK_FULLSCREEN', core.intended_environment(self.config, DISPLAY_CAPS))

    def test_display_build_without_bridge_keeps_original_camera(self):
        caps = core.Capabilities(origin='build-record', hud_scale=(50, 100), camera_aspects=('original',))
        self.config['display'].update(camera_aspect='output', hud_scale=100)
        args = core.player_arguments(self.config, caps)
        self.assertEqual(args[-2:], ['--hud-scale=100', '--camera-aspect=original'])

    def test_long_profile_path_is_refused_for_builds_without_the_host_path_fix(self):
        """Accepted SDK keeps PS3_HDD0_ROOT + "/game" in 128 bytes; a longer path stalls the game."""
        deep = self.root / ('p' * max(1, core.ACCEPTED_PROFILE_PATH_MAX - len(str(self.root)) - 9)) / 'dev_hdd0'
        deep.mkdir(parents=True)
        c = copy.deepcopy(self.config)
        c['paths']['hdd0'] = str(deep)
        with patch.object(core, 'validate_backup', return_value={}):
            if len(str(deep.resolve())) > core.ACCEPTED_PROFILE_PATH_MAX:
                with self.assertRaisesRegex(ValueError, 'longer than 122 characters'):
                    launcher.build_command(c, core.ACCEPTED)
                with patch.object(core.subprocess, 'Popen') as spawn, self.assertRaises(ValueError):
                    launcher.launch(c, core.ACCEPTED, process_check=lambda names: [])
                spawn.assert_not_called()
            fixed = core.Capabilities(profile_path_max=1024)
            launcher.build_command(c, fixed)
        core.check_profile_length('D:\\' + 'a' * 100 + '\\dev_hdd0', core.ACCEPTED)

    def test_unsupported_stored_graphics_value_is_refused(self):
        caps = core.Capabilities(graphics={**{k: tuple(v) for k, v in core.GRAPHICS_CHOICES.items()},
                                           'ambient_occlusion': ('off',)})
        with self.assertRaisesRegex(ValueError, 'ambient occlusion'):
            core.player_arguments(self.config, caps)

    def test_diagnostic_environment_never_reaches_a_player_launch(self):
        parent = {'SystemRoot': r'C:\Windows', 'PATH': r'C:\Windows\system32', 'USERPROFILE': r'C:\Users\p',
                  'AUDIO_DEVICE_BURST': '1', 'AUDIO_WAV': 'x.f32', 'AUDIO_BLOCK_TRACE': 'b.csv', 'AUDIO_PEAK': '1',
                  'AUDIO_RATE': '1', 'audio_wav': 'lower.f32', 'FOLKLORE_TIMING_PROBE': '1',
                  'FOLKLORE_GRAPHICS_TIMING_CSV': 'g.csv', 'RSX_ENGINE_HOST_PROFILE_CSV': 'r.csv',
                  'PS3RECOMP_VK_HOST_PROFILE_CSV': 'v.csv', 'PS3RECOMP_RSX_AO': 'rayquery',
                  'PS3RECOMP_FOLKLORE_HUD_SCALE': '50', 'PS3RECOMP_FOLKLORE_CAMERA_ASPECT': '3.5',
                  'PS3RECOMP_UPSCALER': 'fsr1', 'PS3_BDVD_OPEN_LATENCY_US': '8000', 'GCM_FLIP_TIMELINE': '1',
                  'SPURS_TRACE': '1', 'VK_INSTANCE_LAYERS': 'VK_LAYER_x', 'PYTHONPATH': 'x', 'LD_PRELOAD': 'x',
                  'PS3_HDD0_ROOT': r'C:\elsewhere', 'TEMP': r'C:\Temp', 'PS3_VERBOSE': '1'}
        env = core.player_environment(self.config, DISPLAY_CAPS, parent=parent)
        intended = core.intended_environment(self.config, DISPLAY_CAPS)
        self.assertEqual(set(env), {'SystemRoot', 'PATH', 'USERPROFILE', *intended})
        for name, value in intended.items():
            self.assertEqual(env[name], value)
        self.assertTrue(env['PATH'].startswith(str(Path(self.config['paths']['binary']).parent)))
        self.assertEqual(env['PS3_HDD0_ROOT'], str(Path(self.config['paths']['hdd0'])))
        self.assertEqual(env['PS3_VERBOSE'], '0')
        self.assertEqual(core.player_environment(self.config, DISPLAY_CAPS, parent={})['PS3_VERBOSE'], '0')

    def test_bad_backup_rejected_before_launch(self):
        with patch.object(core.subprocess, 'Popen') as spawn:
            with self.assertRaises(ValueError):
                launcher.launch(self.config, process_check=lambda names: [])
        spawn.assert_not_called()

    def test_launches_are_serialized(self):
        with patch.object(core, 'validate_backup', return_value={}), patch.object(core.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'already running'):
                launcher.launch(self.config, process_check=lambda names: [4242])
        spawn.assert_not_called()

    def test_launch_uses_private_temp_no_shell_and_records_receipt(self):
        with patch.dict(core.os.environ, {'AUDIO_WAV': 'x.f32', 'RSX_ENGINE_HOST_PROFILE_CSV': 'p.csv'}), \
                patch.object(core, 'validate_backup', return_value={}), \
                patch.object(core.subprocess, 'Popen') as spawn:
            launcher.launch(self.config, DISPLAY_CAPS, process_check=lambda names: [])
        args, kwargs = spawn.call_args
        self.assertFalse(kwargs['shell'])
        self.assertIsInstance(args[0], list)
        profile = self.root / 'profile'
        self.assertEqual(kwargs['env']['TEMP'], str(profile / 'tmp'))
        self.assertEqual(kwargs['env']['TMP'], kwargs['env']['TEMP'])
        self.assertNotIn('AUDIO_WAV', kwargs['env'])
        self.assertNotIn('RSX_ENGINE_HOST_PROFILE_CSV', kwargs['env'])
        self.assertTrue((profile / 'tmp').is_dir())
        receipt = json.loads((profile / 'launcher-last-launch.json').read_text())
        self.assertEqual(receipt['command'], args[0])
        self.assertEqual(receipt['set_environment'], core.intended_environment(self.config, DISPLAY_CAPS))
        self.assertIn('start', (profile / 'launcher-game.log').read_text())
        if sys.platform == 'win32':
            self.assertEqual(kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)

    def test_launch_creates_enabled_shader_cache_before_spawn(self):
        cache = self.root / 'profile' / 'shader-cache'
        def spawn(*args, **kwargs):
            self.assertTrue(cache.is_dir())
        with patch.object(core, 'build_command', return_value=['test.exe']), \
                patch.object(core, 'player_environment', return_value={}), \
                patch.object(core.subprocess, 'Popen', side_effect=spawn):
            launcher.launch(self.config, core.Capabilities(shader_cache=True),
                            process_check=lambda names: [])

    def test_linked_profile_outputs_are_refused(self):
        profile = self.root / 'profile'
        target = self.root / 'elsewhere'
        target.mkdir()
        for name in core.PROFILE_OUTPUTS:
            link = profile / name
            if sys.platform == 'win32':  # junctions need no extra privilege
                subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)
            else:
                link.symlink_to(target, target_is_directory=True)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'links or junctions'):
                launcher.validate_config(self.config, check_paths=True)
            with patch.object(core.subprocess, 'Popen') as spawn, self.assertRaises(ValueError):
                launcher.launch(self.config, process_check=lambda names: [])
            spawn.assert_not_called()
            link.rmdir() if sys.platform == 'win32' else link.unlink()
            self.assertTrue(target.is_dir())


class SetupHelpers(Fixture):
    def test_create_profile(self):
        hdd0 = core.create_profile(self.root / 'new profile')
        self.assertEqual(hdd0.name, 'dev_hdd0')
        self.assertTrue(hdd0.is_dir())
        with self.assertRaises(ValueError):
            core.create_profile(self.root / 'new profile')
        with self.assertRaises(ValueError):
            core.create_profile(Path('relative'))

    def test_copy_profile_reads_source_only(self):
        source = self.root / 'profile' / 'dev_hdd0'
        save = source / 'home' / '00000001' / 'savedata' / 'BCUS98147-SAVE' / 'DATA.BIN'
        save.parent.mkdir(parents=True)
        save.write_bytes(b'save bytes')
        before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
        copied = core.copy_profile(source, self.root / 'clone')
        self.assertEqual((copied / save.relative_to(source)).read_bytes(), b'save bytes')
        self.assertEqual(before, {p: p.read_bytes() for p in source.rglob('*') if p.is_file()})
        with self.assertRaisesRegex(ValueError, 'already exists'):
            core.copy_profile(source, self.root / 'clone')
        with self.assertRaisesRegex(ValueError, 'outside'):
            core.copy_profile(source, source / 'nested')
        with self.assertRaises(ValueError):
            core.copy_profile(self.root / 'disc', self.root / 'other')

    def test_candidates_from_recipe(self):
        elf = self.root / 'disc' / 'PS3_GAME' / 'USRDIR' / 'EBOOT.elf'
        elf.parent.mkdir(parents=True)
        elf.write_bytes(b'x')
        out = self.root / 'out'
        (out / 'native-build').mkdir(parents=True)
        (out / 'native-build' / 'FolkloreBoot.exe').write_bytes(b'x')
        empty = launcher.default_config()
        found = core.candidate_paths(empty, {'elf': str(elf), 'output': str(out)})
        self.assertEqual(found, {'elf': str(elf), 'disc': str(self.root / 'disc'),
                                 'binary': str(out / 'native-build' / 'FolkloreBoot.exe')})
        self.assertEqual(core.candidate_paths(self.config, {'elf': str(elf), 'output': str(out)}), {})
        empty['paths']['disc'] = str(self.root / 'disc')
        self.assertEqual(core.candidate_paths(empty)['elf'], str(elf))

    def test_recipe_loader(self):
        recipe = {k: str(self.root / k) for k in ('elf', *core.BUILD_NAMES)}
        path = self.root / 'recipe.json'
        path.write_text(json.dumps(recipe))
        self.assertEqual(core.load_recipe(path), recipe)
        for bad in ({**recipe, 'extra': 'x'}, {**recipe, 'sdk': 'relative'}):
            path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                core.load_recipe(path)


if __name__ == '__main__':
    unittest.main()
