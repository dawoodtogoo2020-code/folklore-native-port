"""Configuration, capability gating and player launch for the Folklore launcher.

Nothing here downloads, decrypts or bundles game data. The launcher JSON schema
(SCHEMA_VERSION) is the launcher's own contract; the native engine's graphics
settings file has a separate version and is never written by the launcher.
Player launches pass every chosen setting as an explicit CLI flag, which the
engine ranks above its settings file and above inherited environment values.
"""
import copy
import csv
from dataclasses import dataclass, field
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

from backup_validation import validate_backup

SCHEMA_VERSION = 8
PATH_NAMES = ('binary', 'elf', 'disc', 'hdd0', 'dev_flash')
BUILD_NAMES = ('sdk', 'metadata', 'ppu_manifest', 'spu_manifest', 'toolchain', 'prefix',
               'shader_dir', 'output', 'source')
GRAPHICS_CHOICES = {'anisotropy': ('auto', '1', '2', '4', '8', '16'),
                    'anti_aliasing': ('off', 'fxaa'), 'upscaler': ('off', 'fsr1'),
                    'ambient_occlusion': ('off', 'cacao', 'rayquery'),
                    'path_tracing': ('off', 'diffuse-preview', 'hybrid'), 'ray_denoiser': ('off', 'spatial'),
                    'path_strength': ('20', '35', '60', '100'),
                    'dlss': ('off', 'quality', 'balanced', 'performance', 'ultra-performance', 'dlaa'),
                    'sharpen': tuple(str(v) for v in range(101))}
TEXTURE_QUALITIES = {'low': 1, 'medium': 2, 'high': 4}
WINDOW_MODES = ('windowed', 'fullscreen')
CAMERA_ASPECTS = ('original', 'output')
THEMES = ('doolin', 'faery', 'undersea', 'ember')   # launcher appearance only
MAX_DIMENSION = 8192
MAX_PIXELS = 33_554_432          # DISPLAY-CAPABILITIES.md output canvas ceiling
MAX_CAMERA_RATIO = 32 / 9        # widest ratio the guarded camera bridge accepts
MIN_CAMERA_RATIO = 16 / 9        # at or below this the camera stays original
HUD_LIMITS = (50, 100)
MAX_MONITOR = 63
CONFIG_LIMIT = 1024 * 1024
RECORD_LIMIT = 16 * 1024 * 1024
CAPABILITY_FILE = 'native/launcher-capabilities.json'
CAMERA_BRIDGE_FLAG = '-DFOLKLORE_CAMERA_ASPECT_BRIDGE=ON'
GAME_EXIT_OK = (0, 88)
# Builds without the host game-data path fix (SDK 3a6914d) keep PS3_HDD0_ROOT + "/game" in a
# 128-byte buffer: a longer dev_hdd0 path is silently truncated and the game stalls when it first
# opens installed data (measured: 120 characters boot, 123 stall). Records may declare more.
ACCEPTED_PROFILE_PATH_MAX = 122
PROFILE_OUTPUTS = ('dev_hdd1', 'launcher-game.log', 'launcher-last-launch.json', 'tmp', 'shader-cache')


class NewerConfigError(ValueError):
    """The file was written by a newer launcher; it must not be rewritten here."""


def default_config():
    return {'schema_version': SCHEMA_VERSION,
            'paths': dict.fromkeys(PATH_NAMES, ''),
            'display': {'monitor': 0, 'window_mode': 'windowed', 'width': 1280, 'height': 720,
                        'render_scale': 1, 'camera_aspect': 'original', 'hud_scale': 100, 'frame_rate': 30},
            'graphics': {'anisotropy': 'auto', 'anti_aliasing': 'fxaa', 'upscaler': 'off',
                         'ambient_occlusion': 'off', 'path_tracing': 'off', 'ray_denoiser': 'spatial',
                         'path_strength': '35', 'texture_quality': 'low', 'dlss': 'off', 'sharpen': '0'},
            'textures': {'pack_root': ''},
            'build': dict.fromkeys(BUILD_NAMES, ''),
            'appearance': {'theme': THEMES[0]}}


# ---------------------------------------------------------------------------
# Schema migration and validation

def require_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f'{label} has missing or unknown fields.')


def _migrate_1_to_2(old):
    """Schema 1 kept output size and render scale under graphics."""
    require_keys(old, ('schema_version', 'paths', 'graphics'), 'Schema 1 configuration')
    require_keys(old['graphics'], ('width', 'height', 'render_scale', 'anisotropy', 'anti_aliasing', 'upscaler', 'ambient_occlusion'),
                 'Schema 1 graphics')
    new = {'schema_version': 2, 'paths': copy.deepcopy(old['paths']),
           'display': {'monitor': 0, 'window_mode': 'windowed', 'camera_aspect': 'original', 'hud_scale': 100},
           'graphics': {k: old['graphics'][k] for k in ('anisotropy', 'anti_aliasing', 'upscaler', 'ambient_occlusion')},
           'build': dict.fromkeys(BUILD_NAMES, '')}
    for key in ('width', 'height', 'render_scale'):
        new['display'][key] = old['graphics'][key]
    return new


def _migrate_2_to_3(old):
    """Schema 3 adds the launcher appearance theme; every schema 2 field is kept as is."""
    require_keys(old, ('schema_version', 'paths', 'display', 'graphics', 'build'), 'Schema 2 configuration')
    new = copy.deepcopy(old)
    new['schema_version'] = 3
    new['appearance'] = {'theme': THEMES[0]}
    return new


def _migrate_3_to_4(old):
    require_keys(old, ('schema_version', 'paths', 'display', 'graphics', 'build', 'appearance'), 'Schema 3 configuration')
    require_keys(old['display'], tuple(k for k in default_config()['display'] if k != 'frame_rate'), 'Schema 3 display')
    new = copy.deepcopy(old)
    new['schema_version'] = 4
    new['display']['frame_rate'] = 30
    return new


def _migrate_4_to_5(old):
    require_keys(old, ('schema_version', 'paths', 'display', 'graphics', 'build', 'appearance'), 'Schema 4 configuration')
    require_keys(old['graphics'], ('anisotropy', 'anti_aliasing', 'upscaler', 'ambient_occlusion'), 'Schema 4 graphics')
    new = copy.deepcopy(old)
    new['schema_version'] = 5
    new['graphics']['path_tracing'] = 'off'
    new['graphics']['ray_denoiser'] = 'spatial'
    return new


def _migrate_5_to_6(old):
    require_keys(old, ('schema_version', 'paths', 'display', 'graphics', 'build', 'appearance'), 'Schema 5 configuration')
    require_keys(old['graphics'], ('anisotropy', 'anti_aliasing', 'upscaler', 'ambient_occlusion',
                                    'path_tracing', 'ray_denoiser'), 'Schema 5 graphics')
    new = copy.deepcopy(old)
    new['schema_version'] = 6
    new['graphics']['path_strength'] = '35'
    return new


def _migrate_6_to_7(old):
    require_keys(old, ('schema_version', 'paths', 'display', 'graphics', 'build', 'appearance'),
                 'Schema 6 configuration')
    require_keys(old['graphics'], ('anisotropy', 'anti_aliasing', 'upscaler', 'ambient_occlusion',
                                   'path_tracing', 'ray_denoiser', 'path_strength'), 'Schema 6 graphics')
    new = copy.deepcopy(old)
    new['schema_version'] = 7
    new['graphics']['texture_quality'] = 'low'
    new['textures'] = {'pack_root': ''}
    return new


def _migrate_7_to_8(old):
    new = copy.deepcopy(old)
    new['schema_version'] = 8
    new['graphics']['dlss'] = 'off'
    new['graphics']['sharpen'] = '0'
    return new


MIGRATIONS = {1: _migrate_1_to_2, 2: _migrate_2_to_3, 3: _migrate_3_to_4, 4: _migrate_4_to_5,
              5: _migrate_5_to_6, 6: _migrate_6_to_7, 7: _migrate_7_to_8}


def schema_of(data):
    version = data.get('schema_version') if isinstance(data, dict) else None
    if type(version) is not int or version < 1:
        raise ValueError('Unsupported configuration version.')
    return version


def migrate_config(data):
    """Return (current-schema config, schema it was read as). Never mutates data."""
    original = version = schema_of(data)
    if version > SCHEMA_VERSION:
        raise NewerConfigError(
            f'This settings file uses launcher schema {version}, newer than this launcher '
            f'(schema {SCHEMA_VERSION}). It was left unchanged.')
    data = copy.deepcopy(data)
    while version < SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
    return data, original


def _whole(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be a whole number from {low} to {high}.')


def validate_display(display):
    _whole(display['monitor'], 0, MAX_MONITOR, 'Monitor')
    _whole(display['width'], 1, MAX_DIMENSION, 'Output width')
    _whole(display['height'], 1, MAX_DIMENSION, 'Output height')
    if display['width'] * display['height'] > MAX_PIXELS:
        raise ValueError(f'Output resolution is above {MAX_PIXELS:,} pixels '
                         '(for example 8192 x 4096). Choose a smaller size.')
    _whole(display['render_scale'], 1, 4, 'Internal render scale')
    _whole(display['hud_scale'], *HUD_LIMITS, 'HUD size')
    if type(display['frame_rate']) is not int or display['frame_rate'] not in (30, 60):
        raise ValueError('Frame rate must be 30 or experimental 60.')
    for key, choices in (('window_mode', WINDOW_MODES), ('camera_aspect', CAMERA_ASPECTS)):
        if type(display[key]) is not str or display[key] not in choices:
            raise ValueError(f'Unsupported {key}: choose {", ".join(choices)}.')
    if display['camera_aspect'] == 'output' and \
            display['width'] / display['height'] > MAX_CAMERA_RATIO + 1e-9:
        raise ValueError('Match output aspect supports up to 32:9. Choose a narrower output '
                         'or the original camera.')


def validate_config(config, check_paths=False):
    defaults = default_config()
    require_keys(config, defaults, 'Configuration')
    if type(config['schema_version']) is not int or config['schema_version'] != SCHEMA_VERSION:
        raise ValueError('Unsupported configuration version.')
    for section in ('paths', 'display', 'graphics', 'textures', 'build', 'appearance'):
        require_keys(config[section], defaults[section], section.capitalize())
    if config['appearance']['theme'] not in THEMES:
        raise ValueError(f'Unsupported appearance theme: choose {", ".join(THEMES)}.')
    for section in ('paths', 'build'):
        for key, value in config[section].items():
            if not isinstance(value, str) or '\0' in value:
                raise ValueError(f'{key} must be a path string.')
    validate_display(config['display'])
    for key, choices in GRAPHICS_CHOICES.items():
        value = config['graphics'][key]
        if type(value) is not str or value not in choices:
            raise ValueError(f'Unsupported {key}: choose {", ".join(choices)}.')
    if config['graphics']['texture_quality'] not in TEXTURE_QUALITIES:
        raise ValueError('Texture quality must be low, medium or high.')
    if not isinstance(config['textures']['pack_root'], str) or '\0' in config['textures']['pack_root']:
        raise ValueError('Texture pack root must be a path string.')
    if config['graphics']['path_tracing'] != 'off' and config['graphics']['ambient_occlusion'] != 'off':
        raise ValueError('Diffuse path tracing preview requires ambient occlusion Off.')
    if config['graphics']['dlss'] != 'off' and config['graphics']['ambient_occlusion'] != 'off':
        raise ValueError('DLSS requires ambient occlusion Off.')
    if check_paths:
        check_launch_paths(config['paths'])
    return copy.deepcopy(config)


def is_link(path):
    path = Path(path)
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def overlaps(a, b):
    a, b = Path(a).resolve(), Path(b).resolve()
    return a == b or a in b.parents or b in a.parents


def check_launch_paths(raw):
    """Contained-write rules for a launch. Paths are checked, never created."""
    paths = {k: Path(v) for k, v in raw.items()}
    labels = {'binary': 'Native executable', 'elf': 'Decrypted EBOOT.elf', 'disc': 'Disc backup',
              'hdd0': 'Save profile', 'dev_flash': 'Firmware dev_flash'}
    for k, p in paths.items():
        if not raw[k]:
            raise ValueError(f'{labels[k]} is not set.')
        if not p.is_absolute() or '..' in p.parts:
            raise ValueError(f'{k} must be an absolute path without parent traversal.')
        if not (p.is_file() if k in ('binary', 'elf') else p.is_dir()):
            raise ValueError(f'{k} does not exist or has the wrong file type.')
    disc = paths['disc']
    if not (disc / 'PS3_GAME').is_dir() or not (disc / 'PS3_DISC.SFB').is_file():
        raise ValueError('Select the disc folder containing PS3_GAME and PS3_DISC.SFB.')
    for p in (paths['hdd0'], *paths['hdd0'].parents):
        if is_link(p):
            raise ValueError('The writable profile cannot use symbolic links or junctions.')
    for k in ('disc', 'elf', 'dev_flash', 'binary'):
        if overlaps(paths['hdd0'], paths[k]):
            raise ValueError(f'The writable profile overlaps {k}. Choose a separate profile folder.')
    if paths['hdd0'].name != 'dev_hdd0':
        raise ValueError('The profile folder must be named dev_hdd0.')
    profile = paths['hdd0'].parent
    if any(overlaps(profile, paths[k]) for k in ('disc', 'elf', 'dev_flash', 'binary')):
        raise ValueError('The profile parent must be separate from the game, executable and firmware.')
    for name in PROFILE_OUTPUTS:
        if is_link(profile / name):
            raise ValueError('Profile output paths cannot be symbolic links or junctions.')


# ---------------------------------------------------------------------------
# Load and atomic save

def _read_limited(path, limit, label):
    with Path(path).open('rb') as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'{label} exceeds {limit // (1024 * 1024)} MiB.')
    return data


def _parse_json(data, label):
    try:
        return json.loads(data.decode('utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as e:
        raise ValueError(f'Invalid {label} JSON.') from e


def load_config_info(path):
    """Return (config, info). info: exists, schema (as stored), migrated."""
    try:
        data = _read_limited(path, CONFIG_LIMIT, 'Configuration')
    except FileNotFoundError:
        return default_config(), {'exists': False, 'schema': None, 'migrated': False}
    config, schema = migrate_config(_parse_json(data, 'configuration'))
    return validate_config(config), {'exists': True, 'schema': schema,
                                     'migrated': schema != SCHEMA_VERSION}


def load_config(path):
    return load_config_info(path)[0]


def _stored_schema(path):
    try:
        data = _read_limited(path, CONFIG_LIMIT, 'Configuration')
    except FileNotFoundError:
        return None
    try:
        return schema_of(_parse_json(data, 'configuration'))
    except ValueError:
        return 'unreadable'


def _write_new(path, data):
    """Create path exclusively; never replaces an existing backup."""
    try:
        with open(path, 'xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return True
    except FileExistsError:
        return False


def atomic_write(path, text):
    path = Path(path)
    if is_link(path):
        raise ValueError(f'Refusing to write through a link: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.folklore-',
                                         suffix='.tmp', delete=False) as f:
            temp = Path(f.name)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp and temp.exists():
            temp.unlink()


def save_config(path, config):
    """Validate, keep one copy of an older-schema or unreadable file, then replace atomically.

    A file from a newer launcher is never overwritten.
    Returns the path of a backup copy made by this call, or None.
    """
    config = validate_config(config)
    path = Path(path)
    stored = _stored_schema(path)
    backup = None
    if isinstance(stored, int) and stored > SCHEMA_VERSION:
        raise NewerConfigError(f'{path.name} was written by a newer launcher (schema {stored}); '
                               'it was left unchanged.')
    if stored == 'unreadable' or (isinstance(stored, int) and stored < SCHEMA_VERSION):
        suffix = 'unreadable' if stored == 'unreadable' else f'schema{stored}'
        candidate = path.with_name(f'{path.stem}.{suffix}.json')
        if not is_link(candidate) and _write_new(candidate, path.read_bytes()):
            backup = candidate
    atomic_write(path, json.dumps(config, indent=2) + '\n')
    return backup


# ---------------------------------------------------------------------------
# Capability discovery

@dataclass(frozen=True)
class Capabilities:
    """Controls the selected executable is known to accept.

    origin is 'accepted-default' (conservative accepted feature set) or 'build-record'
    (declared by the built source and tied to this executable by the builder checkpoint).
    """
    origin: str = 'accepted-default'
    detail: str = 'No build capability record was found; using the accepted feature set.'
    binary_sha256: str = ''
    render_scale: tuple = (1, 4)
    graphics: dict = field(default_factory=lambda: {**{k: tuple(v) for k, v in GRAPHICS_CHOICES.items()},
                                                   'ambient_occlusion': ('off', 'cacao'),
                                                   'path_tracing': ('off',), 'ray_denoiser': ('off', 'spatial'),
                                                   'path_strength': ('35',), 'dlss': ('off',),
                                                   'sharpen': ('0',)})
    hud_scale: tuple = None              # (min, max) when --hud-scale exists
    camera_aspects: tuple = None         # values --camera-aspect accepts, when the flag exists
    monitor_select: bool = False         # PS3RECOMP_VK_DISPLAY
    fullscreen: bool = False             # PS3RECOMP_VK_FULLSCREEN with Alt+Enter toggle
    frame_rate_60: bool = False          # guarded bridge, not a stable 60 FPS claim
    shader_cache: bool = False           # persistent cache inside the writable profile
    texture_scales: tuple = ()           # verified texture adapter scales
    native_settings_version: int = 5
    profile_path_max: int = ACCEPTED_PROFILE_PATH_MAX   # characters in the resolved dev_hdd0 path

    @property
    def camera_output(self):
        return bool(self.camera_aspects) and 'output' in self.camera_aspects


ACCEPTED = Capabilities()


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _same_file(a, b):
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, ValueError):
        return False


def parse_capability_record(data, bridge_built, frame_bridge_built=False):
    """Strictly parse a source capability declaration; unknown fields are refused."""
    record = _parse_json(data, 'capability record')
    limits = record.pop('limits', {}) if isinstance(record, dict) else {}
    texture_scales = record.pop('texture_scales', None) if isinstance(record, dict) else None
    require_keys(record, ('format', 'version', 'comment', 'native_settings_version', 'cli', 'environment'),
                 'Capability record')
    require_keys(limits, tuple(limits) and ('profile_path_max',), 'Capability limits')
    profile_max = limits.get('profile_path_max', ACCEPTED_PROFILE_PATH_MAX)
    if type(profile_max) is not int or not 64 <= profile_max <= 4096:
        raise ValueError('Capability record profile_path_max must be a whole number from 64 to 4096.')
    if record['format'] != 'folklore-launcher-capabilities' or record['version'] != 1:
        raise ValueError('Unsupported capability record format.')
    cli, env = record['cli'], record['environment']
    if not isinstance(env, dict):
        raise ValueError('Capability environment must be an object.')
    frame_rate = env.pop('FOLKLORE_FRAME_RATE', False)
    shader_cache = env.pop('PS3RECOMP_SHADER_CACHE_DIR', False)
    texture_pack = env.pop('FOLKLORE_TEXTURE_PACK', False)
    texture_scale = env.pop('FOLKLORE_TEXTURE_SCALE', False)
    if type(frame_rate) is not bool or type(shader_cache) is not bool:
        raise ValueError('Frame-rate and shader-cache capability flags must be true or false.')
    if type(texture_pack) is not bool or type(texture_scale) is not bool or texture_pack != texture_scale:
        raise ValueError('Texture capability requires both environment flags as booleans.')
    if texture_scales is None:
        texture_scales = [2, 4] if texture_pack else []
    if (not isinstance(texture_scales, list) or
            any(type(v) is not int or v not in (2, 4) for v in texture_scales) or
            len(set(texture_scales)) != len(texture_scales)):
        raise ValueError('Texture scales must be a unique subset of 2 and 4.')
    if (texture_pack and not texture_scales) or (not texture_pack and texture_scales):
        raise ValueError('Texture scales require both environment flags and a nonempty scale list.')
    expected_cli = ('output_size', 'render_scale', 'anisotropy', 'anti_aliasing', 'upscaler', 'ambient_occlusion')
    if record['native_settings_version'] >= 8:
        expected_cli += ('path_tracing', 'ray_denoiser')
    if record['native_settings_version'] >= 9:
        expected_cli += ('path_strength',)
    if record['native_settings_version'] >= 10:
        expected_cli += ('dlss', 'sharpen')
    require_keys(cli, (*expected_cli, 'hud_scale', 'camera_aspect'),
                 'Capability CLI')
    require_keys(env, ('PS3RECOMP_VK_DISPLAY', 'PS3RECOMP_VK_FULLSCREEN'), 'Capability environment')
    if cli['output_size'] is not True or cli['render_scale'] != [1, 4]:
        raise ValueError('Capability record must declare output size and render scale 1..4.')
    graphics = {}
    for key, known in GRAPHICS_CHOICES.items():
        values = cli[key] if key in cli else (['off', 'spatial'] if key == 'ray_denoiser' else
                                              ['35'] if key == 'path_strength' else
                                              ['0'] if key == 'sharpen' else ['off'])
        if not isinstance(values, list) or not values or any(v not in known for v in values):
            raise ValueError(f'Capability record has unsupported {key} values.')
        if record['native_settings_version'] < 8 and key == 'ambient_occlusion' and 'rayquery' in values:
            raise ValueError('Older capability records cannot declare ray queried AO.')
        graphics[key] = tuple(v for v in known if v in values)
    hud = cli['hud_scale']
    if hud is not None:
        if (not isinstance(hud, list) or len(hud) != 2 or any(type(v) is not int for v in hud)
                or not HUD_LIMITS[0] <= hud[0] <= hud[1] <= HUD_LIMITS[1]):
            raise ValueError('Capability record HUD range must lie within 50..100.')
        hud = tuple(hud)
    camera = cli['camera_aspect']
    if camera is not None:
        if not isinstance(camera, list) or 'original' not in camera or \
                any(v not in CAMERA_ASPECTS for v in camera):
            raise ValueError('Capability record camera aspect values are invalid.')
        # The output camera exists only when the guarded bridge was compiled in.
        camera = tuple(v for v in CAMERA_ASPECTS if v in camera and (v != 'output' or bridge_built))
    if any(type(v) is not bool for v in env.values()) or type(record['native_settings_version']) is not int:
        raise ValueError('Capability record environment flags must be true or false.')
    return dict(graphics=graphics, hud_scale=hud, camera_aspects=camera,
                monitor_select=env['PS3RECOMP_VK_DISPLAY'], fullscreen=env['PS3RECOMP_VK_FULLSCREEN'],
                frame_rate_60=frame_rate and frame_bridge_built, shader_cache=shader_cache,
                texture_scales=tuple(v for v in (2, 4) if v in texture_scales),
                native_settings_version=record['native_settings_version'], profile_path_max=profile_max)


def discover_capabilities(binary, binary_sha256=None):
    """Capabilities of the executable at `binary`, from the builder's provenance.

    Trusts the owned-backup builder's exact executable and source snapshot. An installed
    copy may point back to that build through a hash-bound deployment.json beside it.
    """
    binary = Path(binary) if binary else None
    if not binary or not binary.is_file():
        return Capabilities(detail='No native executable is selected.')
    try:
        digest = binary_sha256 or sha256_file(binary)
    except OSError as e:
        return Capabilities(detail=f'Cannot read the executable: {e}')

    def fallback(reason):
        return Capabilities(binary_sha256=digest, detail=reason + ' Using the accepted feature set.')

    # An installed build carries the exact capability snapshot alongside the
    # executable. It keeps working if a developer checkout is moved or removed.
    # The local installer creates this receipt only after a successful build.
    installed = binary.parent / 'installed-build.json'
    if installed.is_file():
        try:
            record = _parse_json(_read_limited(installed, CONFIG_LIMIT, 'Installed build'), 'installed build')
            require_keys(record, ('schema_version', 'binary_sha256', 'capability_sha256',
                                  'camera_bridge', 'frame_bridge', 'source_commits'), 'Installed build')
            if record['schema_version'] != 1 or record['binary_sha256'] != sha256_file(binary):
                raise ValueError('installed executable hash differs')
            if type(record['camera_bridge']) is not bool or type(record['frame_bridge']) is not bool:
                raise ValueError('installed bridge flags must be booleans')
            data = _read_limited(binary.parent / 'launcher-capabilities.json', CONFIG_LIMIT, 'Installed capabilities')
            if hashlib.sha256(data).hexdigest() != record['capability_sha256']:
                raise ValueError('installed capability snapshot differs')
            fields = parse_capability_record(data, record['camera_bridge'], record['frame_bridge'])
            detail = 'Declared by the installed build and verified against this executable and capability snapshot.'
            if 'quality' in fields['graphics']['dlss'] and not dlss_runtime_available(binary):
                fields['graphics']['dlss'] = ('off',)
                detail += ' DLSS requires the verified release runtime and bridge.'
            return Capabilities(origin='build-record', binary_sha256=digest, detail=detail, **fields)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            return fallback(f'The installed build record is invalid ({e}).')

    checkpoint = binary.parent.parent / 'checkpoint.json'
    built_binary = binary
    built_digest = digest
    checkpoint_bytes = None
    deployment = (binary.parent.parent if binary.parent.name.lower() == 'bin' else
                  binary.parent) / 'deployment.json'
    if not checkpoint.is_file() and deployment.is_file():
        try:
            deployed = _parse_json(_read_limited(deployment, CONFIG_LIMIT, 'Deployment record'),
                                   'deployment record')
            require_keys(deployed, ('schema_version', 'builder_checkpoint', 'builder_checkpoint_sha256',
                                    'source_binary', 'source_binary_sha256', 'deployed_binary',
                                    'deployed_binary_sha256'), 'Deployment record')
            if type(deployed['schema_version']) is not int or deployed['schema_version'] != 1:
                raise ValueError('Unsupported deployment record schema.')
            for key in ('builder_checkpoint', 'source_binary', 'deployed_binary'):
                if not isinstance(deployed[key], str) or not Path(deployed[key]).is_absolute():
                    raise ValueError(f'Deployment {key} must be an absolute path.')
            for key in ('builder_checkpoint_sha256', 'source_binary_sha256', 'deployed_binary_sha256'):
                value = deployed[key]
                if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                    raise ValueError(f'Deployment {key} must be a lowercase SHA-256.')
            if not _same_file(deployed['deployed_binary'], binary):
                raise ValueError('Deployment record names a different installed executable.')
            digest = sha256_file(binary)
            if digest != deployed['deployed_binary_sha256']:
                raise ValueError('Installed executable differs from deployment record.')
            built_binary = Path(deployed['source_binary'])
            built_digest = deployed['source_binary_sha256']
            if digest != built_digest or sha256_file(built_binary) != built_digest:
                raise ValueError('Installed and original built executables differ.')
            checkpoint = Path(deployed['builder_checkpoint'])
            if not _same_file(checkpoint, built_binary.parent.parent / 'checkpoint.json'):
                raise ValueError('Deployment checkpoint is not beside the original build.')
            checkpoint_bytes = _read_limited(checkpoint, RECORD_LIMIT, 'Build checkpoint')
            if hashlib.sha256(checkpoint_bytes).hexdigest() != deployed['builder_checkpoint_sha256']:
                raise ValueError('Original build checkpoint differs from deployment record.')
        except (OSError, ValueError, KeyError, TypeError) as e:
            return fallback(f'The deployment record is invalid ({e}).')
    try:
        state = _parse_json(checkpoint_bytes if checkpoint_bytes is not None else
                            _read_limited(checkpoint, RECORD_LIMIT, 'Build checkpoint'), 'build checkpoint')
    except FileNotFoundError:
        return fallback('No owned-backup builder record sits beside this executable.')
    except (OSError, ValueError) as e:
        return fallback(f'The build record could not be read ({e}).')
    try:
        if state.get('state') != 'built' or not _same_file(state.get('binary', ''), built_binary) \
                or state.get('binary_sha256') != built_digest:
            return fallback('The build record does not describe this exact executable.')
        source = Path(state['paths']['source'])
        declaration = source / CAPABILITY_FILE
        recorded = state.get('build_source_snapshot', {}).get(str(declaration))
        if not recorded:
            return fallback('The source this executable was built from declares no launcher capabilities.')
        data = _read_limited(declaration, CONFIG_LIMIT, 'Capability record')
        if hashlib.sha256(data).hexdigest() != recorded:
            return fallback('The capability record changed after this executable was built.')
        bridge = any(step.get('name') == 'configure' and step.get('exit_code') == 0 and
                     CAMERA_BRIDGE_FLAG in step.get('command', ()) for step in state.get('steps', ()))
        frame_bridge = any(step.get('name') == 'configure' and step.get('exit_code') == 0 and
                           '-DFOLKLORE_FRAME_LIMITER_BRIDGE=ON' in step.get('command', ())
                           for step in state.get('steps', ()))
        fields = parse_capability_record(data, bridge, frame_bridge)
    except FileNotFoundError:
        return fallback('The recorded capability file is missing.')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        return fallback(f'The capability record is invalid ({e}).')
    detail = f'Declared by {declaration} and recorded by the builder for this executable.'
    if 'quality' in fields['graphics']['dlss'] and not dlss_runtime_available(binary):
        fields['graphics']['dlss'] = ('off',)
        detail += ' DLSS is unavailable: the verified release runtime and bridge must be installed beside the game.'
    return Capabilities(origin='build-record', binary_sha256=digest, detail=detail,
                        **fields)


def dlss_runtime_available(binary):
    """Require a deployment receipt for the reviewed release DLL and exact bridge.

    The installer records hashes after verifying a release runtime. Mere DLL
    presence must not enable the development (watermarked) runtime by accident.
    This local receipt is provenance, not a redistribution licence.
    """
    directory = Path(binary).resolve().parent
    try:
        record = _parse_json(_read_limited(directory / 'dlss-runtime.json', CONFIG_LIMIT,
                                          'DLSS runtime receipt'), 'DLSS runtime receipt')
        if record.get('schema_version') != 1 or record.get('variant') != 'release':
            return False
        for name in ('nvngx_dlss.dll', 'rsx_dlss_vk.dll'):
            path = directory / name
            if is_link(path) or not path.is_file() or sha256_file(path) != record.get('sha256', {}).get(name):
                return False
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Player command and environment

# Only these inherited variables reach the game. Every diagnostic switch the
# runtime reads (PS3*, RSX*, GCM*, SPU*, AUDIO_*, FOLKLORE_*, VK_* layers, ...)
# is therefore absent unless the launcher sets it on purpose below.
ENV_ALLOW = frozenset({
    'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'OS',
    'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_IDENTIFIER', 'PROCESSOR_LEVEL',
    'PROCESSOR_REVISION', 'USERPROFILE', 'USERNAME', 'USERDOMAIN', 'HOMEDRIVE', 'HOMEPATH',
    'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'ALLUSERSPROFILE', 'PUBLIC', 'COMPUTERNAME',
    'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432', 'COMMONPROGRAMFILES',
    'COMMONPROGRAMFILES(X86)', 'COMMONPROGRAMW6432', 'DRIVERDATA',
    'HOME', 'USER', 'LANG', 'XDG_RUNTIME_DIR', 'DISPLAY', 'WAYLAND_DISPLAY'})


def check_supported(config, caps):
    """Refuse stored graphics values the selected build does not accept."""
    g = config['graphics']
    for key, allowed in caps.graphics.items():
        if key == 'ray_denoiser' and g['path_tracing'] == 'off' and g['ambient_occlusion'] != 'rayquery':
            continue
        if key == 'path_strength' and (g['path_tracing'] == 'off' or caps.native_settings_version < 9):
            continue
        if g[key] not in allowed:
            raise ValueError(f'This build does not support {key.replace("_", " ")} "{g[key]}". '
                             'Choose another value in Settings.')
    if not caps.render_scale[0] <= config['display']['render_scale'] <= caps.render_scale[1]:
        raise ValueError('This build does not support that internal render scale.')


def resolve_texture_pack(config, caps):
    """Return (tier directory, scale), or refuse an unavailable selected tier."""
    quality = config['graphics']['texture_quality']
    if quality == 'low':
        return None
    scale = TEXTURE_QUALITIES[quality]
    if caps.origin != 'build-record' or scale not in caps.texture_scales:
        raise ValueError(f'{quality.title()} textures need a verified build with the texture-pack adapter.')
    raw = config['textures']['pack_root'].strip()
    if not raw:
        raise ValueError('Choose a texture pack root in Settings.')
    root = Path(raw)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('Texture pack root must be an absolute path without parent traversal.')
    if any(is_link(p) for p in (root, *root.parents)):
        raise ValueError('Texture pack root cannot contain links or junctions.')
    if not root.is_dir():
        raise ValueError('Texture pack root folder does not exist.')
    index = root / 'pack-index.json'
    if is_link(index):
        raise ValueError('Texture pack index cannot be a link or junction.')
    try:
        data = _parse_json(_read_limited(index, CONFIG_LIMIT, 'Texture pack index'), 'texture pack index')
    except FileNotFoundError as e:
        raise ValueError('Texture pack root has no pack-index.json.') from e
    require_keys(data, ('schema_version', 'tiers'), 'Texture pack index')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('Unsupported texture pack index schema.')
    require_keys(data['tiers'], ('medium', 'high'), 'Texture pack tiers')
    tier = data['tiers'][quality]
    require_keys(tier, ('scale', 'path', 'complete'), f'{quality.title()} texture tier')
    if type(tier['scale']) is not int or tier['scale'] != scale or type(tier['complete']) is not bool:
        raise ValueError(f'{quality.title()} tier has an invalid scale or completion flag.')
    if not tier['complete']:
        raise ValueError(f'{quality.title()} texture tier is incomplete.')
    relative = tier['path']
    if not isinstance(relative, str) or not relative or '\0' in relative:
        raise ValueError(f'{quality.title()} tier path is invalid.')
    subpath = Path(relative)
    if subpath.is_absolute() or '..' in subpath.parts or subpath == Path('.'):
        raise ValueError(f'{quality.title()} tier path must stay inside the pack root.')
    directory = root / subpath
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'{quality.title()} tier path contains traversal or a link.')
    current = root
    for part in subpath.parts:
        current = current / part
        if is_link(current):
            raise ValueError(f'{quality.title()} tier path contains a link or junction.')
    if not directory.is_dir():
        raise ValueError(f'{quality.title()} texture tier folder does not exist.')
    return directory.resolve(), scale


def texture_availability(config, caps):
    """A reason per tier; None means selectable."""
    available = {'low': None}
    for quality in ('medium', 'high'):
        candidate = copy.deepcopy(config)
        candidate['graphics']['texture_quality'] = quality
        try:
            resolve_texture_pack(candidate, caps)
            available[quality] = None
        except (OSError, ValueError) as error:
            available[quality] = str(error)
    return available


def effective_settings(config, caps):
    """Settings a launch with this build actually uses. Stored values for controls the
    build lacks are kept in the file but not sent (the engine then uses its originals)."""
    d = config['display']
    return {'monitor': d['monitor'] if caps.monitor_select else 0,
            'window_mode': d['window_mode'] if caps.fullscreen else 'windowed',
            'hud_scale': d['hud_scale'] if caps.hud_scale else 100,
            'camera_aspect': d['camera_aspect'] if caps.camera_output else 'original',
            'frame_rate': d['frame_rate'] if caps.frame_rate_60 else 30}


def player_arguments(config, caps):
    d, g = config['display'], config['graphics']
    check_supported(config, caps)
    eff = effective_settings(config, caps)
    args = ['--contained-writes', '--vulkan', f'--output-size={d["width"]}x{d["height"]}',
            f'--render-scale={d["render_scale"]}',
            *[f'--{k.replace("_", "-")}={g[k]}' for k in GRAPHICS_CHOICES
              if (k != 'path_tracing' or 'diffuse-preview' in caps.graphics.get('path_tracing', ()))
              and (k != 'ray_denoiser' or (caps.native_settings_version >= 8 and
                   (g['path_tracing'] != 'off' or g['ambient_occlusion'] == 'rayquery')))
              and (k != 'path_strength' or (caps.native_settings_version >= 9 and
                   g['path_tracing'] != 'off'))
              and (k not in ('dlss', 'sharpen') or caps.native_settings_version >= 10)]]
    # The display compositor's FSR1 path performs the final aspect-correct
    # presentation when DLSS is active; DLSS still performs temporal upscaling.
    if g['dlss'] != 'off':
        args = ['--upscaler=fsr1' if a.startswith('--upscaler=') else a for a in args]
    if caps.hud_scale:
        args.append(f'--hud-scale={eff["hud_scale"]}')
    if caps.camera_aspects:
        args.append(f'--camera-aspect={eff["camera_aspect"]}')
    return args


def check_profile_length(hdd0, caps):
    resolved = str(Path(hdd0).resolve())
    if len(resolved) > caps.profile_path_max:
        raise ValueError(f'This build cannot use a save profile path longer than {caps.profile_path_max} characters '
                         f'(this one has {len(resolved)}): the game would stall while loading. Copy the profile to a '
                         'shorter folder, for example D:\\Folklore\\profile\\dev_hdd0, with Build / Setup > Copy.')


def build_command(config, caps=ACCEPTED, validate_identity=True):
    c = validate_config(config, check_paths=True)
    check_profile_length(c['paths']['hdd0'], caps)
    resolve_texture_pack(c, caps)
    if validate_identity:
        validate_backup(c['paths']['disc'], c['paths']['elf'])
    return [c['paths'][k] for k in PATH_NAMES] + player_arguments(c, caps)


def intended_environment(config, caps):
    """Variables the launcher sets on purpose (values never come from the parent)."""
    paths = {k: Path(v).resolve() for k, v in config['paths'].items()}
    profile = paths['hdd0'].parent
    temp = profile / 'tmp'
    env = dict(PS3_VERBOSE='0', PS3_STRICT_BOOT='1', PS3_STRICT_FS_READONLY='0', PS3_VFS_ROOT=str(paths['disc']),
               PS3_HDD0_ROOT=str(paths['hdd0']), PS3_HDD1_ROOT=str(profile / 'dev_hdd1'),
               PS3_DEV_FLASH=str(paths['dev_flash']), PS3_STRICT_FS_WRITE_ROOT=str(profile),
               PS3_BOOT_PATH='/dev_bdvd/PS3_GAME/USRDIR/EBOOT.BIN', TEMP=str(temp), TMP=str(temp))
    eff = effective_settings(config, caps)
    if caps.monitor_select:
        env['PS3RECOMP_VK_DISPLAY'] = str(eff['monitor'])
    if caps.fullscreen and eff['window_mode'] == 'fullscreen':
        env['PS3RECOMP_VK_FULLSCREEN'] = '1'
    if eff['frame_rate'] == 60:
        env['FOLKLORE_FRAME_RATE'] = '60'
    if caps.shader_cache:
        env['PS3RECOMP_SHADER_CACHE_DIR'] = str(profile / 'shader-cache')
    selected = resolve_texture_pack(config, caps)
    if selected is not None:
        directory, scale = selected
        env['FOLKLORE_TEXTURE_PACK'] = str(directory)
        env['FOLKLORE_TEXTURE_SCALE'] = str(scale)
    return env


def player_environment(config, caps, parent=None):
    parent = os.environ if parent is None else parent
    env = {k: v for k, v in parent.items() if k.upper() in ENV_ALLOW}
    env.update(intended_environment(config, caps))
    binary_dir = str(Path(config['paths']['binary']).resolve().parent)
    path_key = next((k for k in env if k.upper() == 'PATH'), 'PATH')
    env[path_key] = binary_dir + os.pathsep + env.get(path_key, '')
    return env


def running_game_pids(image_names=('FolkloreBoot.exe',)):
    """PIDs of running game processes (Windows), used to keep launches serialized."""
    if os.name != 'nt':
        return []
    pids = []
    for name in dict.fromkeys(image_names):
        try:
            out = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {name}', '/FO', 'CSV', '/NH'],
                                 capture_output=True, text=True, timeout=10, shell=False,
                                 creationflags=subprocess.CREATE_NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for row in csv.reader(io.StringIO(out)):
            if len(row) >= 2 and row[0].casefold() == name.casefold() and row[1].isdigit():
                pids.append(int(row[1]))
    return pids


def launch(config, caps=ACCEPTED, process_check=running_game_pids):
    cmd = build_command(config, caps)
    running = process_check(('FolkloreBoot.exe', Path(cmd[0]).name))
    if running:
        raise ValueError(f'A Folklore game process is already running (PID {running[0]}). '
                         'Close it before starting another.')
    paths = {k: Path(v).resolve() for k, v in config['paths'].items()}
    profile = paths['hdd0'].parent
    env = player_environment(config, caps)
    (profile / 'dev_hdd1').mkdir(exist_ok=True)
    (profile / 'tmp').mkdir(exist_ok=True)
    if caps.shader_cache:
        (profile / 'shader-cache').mkdir(exist_ok=True)
    receipt = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
               'command': cmd, 'set_environment': intended_environment(config, caps),
               'inherited_environment_names': sorted(k for k in env if k not in intended_environment(config, caps)),
               'binary_sha256': caps.binary_sha256, 'capabilities': caps.origin,
               'capability_detail': caps.detail}
    atomic_write(profile / 'launcher-last-launch.json', json.dumps(receipt, indent=2) + '\n')
    with (profile / 'launcher-game.log').open('ab') as log:
        log.write(f'\n[launcher] {receipt["utc"]} start {json.dumps(cmd)}\n'.encode('utf-8'))
        log.flush()
        return subprocess.Popen(cmd, cwd=profile, env=env, stdout=log, stderr=subprocess.STDOUT, shell=False,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


# ---------------------------------------------------------------------------
# Setup helpers

def has_links(root):
    root = Path(root)
    if is_link(root):
        return True
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            info = os.lstat(os.path.join(base, name))
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return True
    return False


def create_profile(parent):
    """Create PARENT/dev_hdd0 for a new save profile (refuses existing or linked paths)."""
    parent = Path(parent)
    if not parent.is_absolute():
        raise ValueError('Choose an absolute folder for the profile.')
    for p in (parent, *parent.parents):
        if is_link(p):
            raise ValueError('The profile folder cannot be inside a symbolic link or junction.')
    hdd0 = parent / 'dev_hdd0'
    if hdd0.exists():
        raise ValueError(f'{hdd0} already exists. Select it as the profile instead.')
    hdd0.mkdir(parents=True)
    return hdd0


def copy_profile(source_hdd0, parent):
    """Copy an existing dev_hdd0 (saves and installed game data) into PARENT/dev_hdd0.

    The source is only read. dev_hdd1 cache, logs and temp files are not copied.
    """
    source = Path(source_hdd0)
    if source.name != 'dev_hdd0' or not source.is_dir():
        raise ValueError('Choose an existing dev_hdd0 folder to copy.')
    if has_links(source):
        raise ValueError('The profile to copy contains links or junctions; copy it by hand.')
    parent = Path(parent)
    if overlaps(parent, source) or overlaps(parent / 'dev_hdd0', source):
        raise ValueError('The new profile must be outside the profile being copied.')
    for p in (parent, *parent.parents):
        if is_link(p):
            raise ValueError('The new profile cannot be inside a symbolic link or junction.')
    target = parent / 'dev_hdd0'
    if target.exists():
        raise ValueError(f'{target} already exists; choose an empty folder.')
    parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True)
    return target


def candidate_paths(config, recipe=None):
    """Suggest existing inputs for empty fields; nothing is created or validated deeply."""
    found = {}
    p = config['paths']
    elf = p['elf'] or (recipe or {}).get('elf', '')
    if not p['elf'] and elf and Path(elf).is_file():
        found['elf'] = str(Path(elf))
    if not p['disc']:
        for base in ([Path(elf).parents[2]] if elf and len(Path(elf).parents) > 2 else []):
            if (base / 'PS3_DISC.SFB').is_file() and (base / 'PS3_GAME').is_dir():
                found['disc'] = str(base)
    disc = p['disc'] or found.get('disc')
    if not p['elf'] and 'elf' not in found and disc:
        guess = Path(disc) / 'PS3_GAME/USRDIR/EBOOT.elf'
        if guess.is_file():
            found['elf'] = str(guess)
    output = config['build']['output'] or (recipe or {}).get('output', '')
    if not p['binary'] and output:
        exe = Path(output) / 'native-build' / 'FolkloreBoot.exe'
        if exe.is_file():
            found['binary'] = str(exe)
    return found


def load_recipe(path):
    """Read the build recipe JSON used by Build-Folklore.cmd (paths only)."""
    data = _parse_json(_read_limited(path, CONFIG_LIMIT, 'Build recipe'), 'build recipe')
    require_keys(data, ('elf', *BUILD_NAMES), 'Build recipe')
    if any(not isinstance(v, str) or not Path(v).is_absolute() for v in data.values()):
        raise ValueError('Build recipe paths must be absolute strings.')
    return data


def interpreter_for_tools():
    if getattr(sys, 'frozen', False):
        python = shutil.which('python.exe' if os.name == 'nt' else 'python3')
        if not python or _same_file(python, sys.executable):
            raise ValueError('Building from a backup requires an installed Python toolchain. Playing an existing build does not.')
        return python
    """python.exe beside pythonw.exe: the builder records the interpreter's hash."""
    exe = Path(sys.executable)
    if exe.name.lower() == 'pythonw.exe' and exe.with_name('python.exe').is_file():
        return str(exe.with_name('python.exe'))
    return str(exe)
