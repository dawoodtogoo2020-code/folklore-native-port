"""Open the real launcher window and save screenshots of its pages (manual evidence, not a unit test).

capture_launcher_screens.py --config ABS_JSON --out ABS_DIR [--prefix NAME] [--fixture-display ABS_DIR]
    [--monitor N] [--tk-scaling F]

--fixture-display builds a synthetic builder output whose checkpoint records a source
capability file declaring the display features, and selects its stand-in executable, so
the screenshots show the capability gating through the real discovery path. The stand-in
is not a game and is never launched.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import folklore_launcher as fl  # noqa: E402
import launcher_display  # noqa: E402

DISPLAY_RECORD = {
    'format': 'folklore-launcher-capabilities', 'version': 1,
    'comment': 'Synthetic fixture declaring the display branch contract (DISPLAY-CAPABILITIES.md).',
    'native_settings_version': 7,
    'cli': {'output_size': True, 'render_scale': [1, 4], 'anisotropy': ['auto', '1', '2', '4', '8', '16'],
            'anti_aliasing': ['off', 'fxaa'], 'upscaler': ['off', 'fsr1'], 'ambient_occlusion': ['off', 'cacao'],
            'hud_scale': [50, 100], 'camera_aspect': ['original', 'output']},
    'environment': {'PS3RECOMP_VK_DISPLAY': True, 'PS3RECOMP_VK_FULLSCREEN': True}}


def make_display_fixture(root):
    root = Path(root)
    source, output = root / 'source', root / 'output'
    (source / 'native').mkdir(parents=True, exist_ok=True)
    (output / 'native-build').mkdir(parents=True, exist_ok=True)
    record = source / 'native' / 'launcher-capabilities.json'
    record.write_text(json.dumps(DISPLAY_RECORD, indent=2) + '\n', encoding='utf-8')
    exe = output / 'native-build' / 'FolkloreBoot.exe'
    exe.write_bytes(b'synthetic stand-in, not a game executable\n')
    checkpoint = {'state': 'built', 'binary': str(exe), 'binary_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
                  'paths': {'source': str(source.resolve())},
                  'steps': [{'name': 'configure', 'exit_code': 0, 'command': ['cmake', '-DFOLKLORE_CAMERA_ASPECT_BRIDGE=ON']}],
                  'build_source_snapshot': {str(record.resolve()): hashlib.sha256(record.read_bytes()).hexdigest()}}
    (output / 'checkpoint.json').write_text(json.dumps(checkpoint, indent=2), encoding='utf-8')
    return exe


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--prefix', default='launcher')
    p.add_argument('--fixture-display', type=Path)
    p.add_argument('--dpi', type=int, help='simulate this monitor DPI on the target monitor work area')
    p.add_argument('--pages', default='home,setup,settings,graphics,unavailable,appearance,advanced')
    p.add_argument('--geometry', help='window size WxH in physical pixels')
    p.add_argument('--theme', help='appearance theme for this capture (not saved)')
    p.add_argument('--art-dir', type=Path, help='alternative art folder (fallback tests)')
    p.add_argument('--no-pillow', action='store_true', help='simulate a Python without Pillow')
    p.add_argument('--set', action='append', default=[], help='KEY=VALUE for a settings variable before capture')
    args = p.parse_args()
    from PIL import ImageGrab
    if args.no_pillow:
        fl.theme.HAVE_PIL = False
    mode = launcher_display.enable_dpi_awareness()
    import tkinter as tk
    root = tk.Tk()
    app = fl.App(root, args.config, art_dir=args.art_dir)
    if args.fixture_display:
        app.path_vars['binary'].set(str(make_display_fixture(args.fixture_display)))
    if args.dpi:
        # Rebuild the pages at the simulated DPI exactly as a monitor change would size them.
        app.shutdown()
        root = tk.Tk()
        fl.launcher_display.list_monitors = lambda real=app.monitors: [
            launcher_display.Monitor(m.index, m.device, m.primary, m.bounds, m.work, args.dpi) for m in real]
        app = fl.App(root, args.config, art_dir=args.art_dir)
        if args.fixture_display:
            app.path_vars['binary'].set(str(make_display_fixture(args.fixture_display)))
    args.out.mkdir(parents=True, exist_ok=True)
    report = {'dpi_awareness': mode, 'monitors': [m.label() for m in app.monitors], 'shots': {}}

    def settle(ms=1500):
        end = root.tk.call('clock', 'milliseconds') + ms
        while root.tk.call('clock', 'milliseconds') < end:
            root.update()

    if args.geometry:
        root.geometry(args.geometry)
    if args.theme:
        app.apply_theme(args.theme, persist=False)
    settle(3000)
    for item in args.set:
        key, value = item.split('=', 1)
        app.v[key].set(value)
        if key in fl.GRAPHICS_CHOICES:
            app.apply_capabilities()
    settle(300)
    tabs = {'settings': 0, 'graphics': 1, 'unavailable': 2, 'appearance': 3}
    for name in args.pages.split(','):
        if name in tabs:
            app.show('settings')
            app.settings_tabs.select(tabs[name])
        else:
            app.show(name)
        settle(1800 if name in ('setup', 'home') else 900)
        root.lift()
        root.attributes('-topmost', True)
        settle(200)
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        shot = args.out / f'{args.prefix}-{name}.png'
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(shot)
        overflow = [str(c) for c in root.winfo_children() if c.winfo_reqwidth() > w + 2 or c.winfo_reqheight() > h + 2]
        report['shots'][name] = {'file': str(shot), 'window': [x, y, w, h], 'overflowing_children': overflow,
                                 'requested': [root.winfo_reqwidth(), root.winfo_reqheight()]}
        root.attributes('-topmost', False)
    report['capabilities'] = {'origin': app.caps.origin, 'detail': app.caps.detail}
    report['settings_message'] = app.settings_msg.cget('text')
    report['tk_scaling'] = float(root.tk.call('tk', 'scaling'))
    m = app._target_monitor()
    report['target_monitor'] = m.label() if m else None
    report['target_work_area'] = m.work if m else None
    print(json.dumps(report, indent=2))
    root.destroy()


if __name__ == '__main__':
    main()
