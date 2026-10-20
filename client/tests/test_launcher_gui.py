"""Drives the real Tk launcher window (needs a desktop session; skipped otherwise)."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import folklore_launcher as fl  # noqa: E402
import launcher_core as core  # noqa: E402
from test_launcher import write_build  # noqa: E402

try:
    import tkinter as tk
    _probe = tk.Tk()
    _probe.destroy()
    HAVE_TK = True
except Exception:  # no display or Tk
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, 'Tk display not available')
class LauncherWindow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_dir = Path(self.temp.name).resolve()
        self.path = self.root_dir / 'settings.json'
        self.windows = []

    def tearDown(self):
        for app in self.windows:
            try:
                app.shutdown()
            except tk.TclError:
                pass

    def open(self, geometry=None, art_dir=None):
        root = tk.Tk()
        app = fl.App(root, self.path, art_dir=art_dir)
        self.windows.append(app)
        if geometry:
            root.geometry(geometry)
        self.pump(root, app)
        return root, app

    def wait_background(self, root, app, key_theme=None):
        for _ in range(150):
            self.pump(root, app, 2)
            key = getattr(app, '_bg_key', None)
            if key and (key_theme is None or key[0] == key_theme) and not app._fade_after:
                return key
        self.fail('background never rendered')

    def pump(self, root, app, rounds=60):
        import time
        for _ in range(rounds):
            root.update()
            time.sleep(0.02)

    def test_invalid_input_blocks_save_and_recovers(self):
        root, app = self.open()
        app.v['width'].set('0')
        self.assertIn('Output width', app.settings_msg.cget('text'))
        self.assertIn('disabled', app.settings_save.state())
        app.v['width'].set('3440')
        app.v['height'].set('1440')
        self.assertNotIn('disabled', app.settings_save.state())
        app.v['hud_scale'].set('')
        self.assertIn('HUD size', app.settings_msg.cget('text'))
        app.revert()
        self.assertEqual(app.settings_msg.cget('text'), 'Not saved yet.')
        self.assertFalse(self.path.exists(), 'nothing is written until Save')

    def test_save_and_reload_round_trip(self):
        root, app = self.open()
        app.v['width'].set('2560')
        app.v['height'].set('1440')
        app.v['render_scale'].set('2')
        app.v['ambient_occlusion'].set('cacao')
        self.assertTrue(app.save())
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored['schema_version'], core.SCHEMA_VERSION)
        self.assertEqual((stored['display']['width'], stored['display']['render_scale'],
                          stored['graphics']['ambient_occlusion']), (2560, 2, 'cacao'))
        app.shutdown()
        root, app = self.open()
        self.assertEqual((app.v['width'].get(), app.v['render_scale'].get()), ('2560', '2'))
        self.assertEqual(app.settings_msg.cget('text'), 'Saved.')
        self.assertEqual(app.v['preset'].get(), '2560 × 1440 (16:9)')

    def test_preview_strength_control_round_trip(self):
        root, app = self.open()
        app.caps = core.Capabilities(origin='build-record', native_settings_version=9,
                                     graphics={k: tuple(v) for k, v in core.GRAPHICS_CHOICES.items()})
        app.apply_capabilities()
        self.assertIn('disabled', app.graphics_boxes['path_strength'].state())
        app.graphics_boxes['path_tracing'].set(fl.PATH_TRACING_LABELS['diffuse-preview'])
        app._graphics_selected('path_tracing')
        self.assertNotIn('disabled', app.graphics_boxes['path_strength'].state())
        app.graphics_boxes['path_strength'].set(fl.PATH_STRENGTH_LABELS['60'])
        app._graphics_selected('path_strength')
        self.assertTrue(app.save())
        self.assertEqual(json.loads(self.path.read_text())['graphics']['path_strength'], '60')
        app.shutdown()
        root, app = self.open()
        self.assertEqual(app.v['path_strength'].get(), '60')

    def test_accepted_build_disables_display_update_controls_but_keeps_values(self):
        config = core.default_config()
        config['display'].update(hud_scale=75, camera_aspect='output', width=3440, height=1440)
        core.save_config(self.path, config)
        root, app = self.open()
        self.assertEqual(app.caps.origin, 'accepted-default')
        self.assertIn('disabled', app.hud_spin.state())
        self.assertIn('disabled', app.camera_radios['output'].state())
        self.assertIn('disabled', app.window_radios[1].state())
        self.assertIn('saved 75%', app.hints['hud_scale'].cget('text'))
        self.assertTrue(app.save())
        self.assertEqual(json.loads(self.path.read_text())['display']['hud_scale'], 75)

    def test_display_build_record_enables_its_controls(self):
        exe, _, _ = write_build(self.root_dir / 'build')
        config = core.default_config()
        config['paths']['binary'] = str(exe)
        core.save_config(self.path, config)
        root, app = self.open()
        for _ in range(100):
            if app.caps.origin == 'build-record':
                break
            self.pump(root, app, 5)
        self.assertEqual(app.caps.origin, 'build-record')
        self.assertNotIn('disabled', app.hud_spin.state())
        self.assertNotIn('disabled', app.camera_radios['output'].state())
        app.v['width'].set('7680')
        app.v['height'].set('1440')
        app.v['camera_aspect'].set('output')
        self.assertIn('32:9', app.settings_msg.cget('text'))
        self.assertIn('disabled', app.settings_save.state())

    def test_unreadable_settings_open_with_defaults_and_stay_untouched(self):
        self.path.write_bytes(b'{"schema_version": 2, broken')
        with mock.patch.object(fl.App, 'check_setup'):
            root, app = self.open()
        self.assertIn('could not be read', app.status.get())
        self.assertEqual(self.path.read_bytes(), b'{"schema_version": 2, broken')
        self.assertTrue(app.save())
        self.assertEqual(self.path.with_name('settings.unreadable.json').read_bytes(),
                         b'{"schema_version": 2, broken')

    def test_newer_settings_are_read_only(self):
        newer = core.default_config()
        newer.update(schema_version=core.SCHEMA_VERSION + 1, future=True)
        raw = json.dumps(newer).encode()
        self.path.write_bytes(raw)
        root, app = self.open()
        self.assertTrue(app.read_only)
        self.assertFalse(app.save())
        self.assertIn('disabled', app.settings_save.state())
        self.assertEqual(self.path.read_bytes(), raw)

    def test_window_fits_its_monitor(self):
        root, app = self.open()
        m = app._target_monitor()
        if m is None:
            self.skipTest('no monitor information')
        x, y = root.winfo_rootx(), root.winfo_rooty()
        self.assertGreaterEqual(x, m.work[0])
        self.assertLessEqual(x + root.winfo_width(), m.work[2])
        self.assertLessEqual(y + root.winfo_height(), m.work[3])
        for page in ('setup', 'settings', 'advanced'):
            app.show(page)
            self.pump(root, app, 10)
            self.assertLessEqual(app.pages[page].winfo_reqwidth(), root.winfo_width() + 2, page)
            # taller content scrolls inside the window instead of being cut off
            overflow = max(0, app.pages[page].winfo_reqheight() - app.panel_room)
            self.assertEqual(app.panel_overflow, overflow, page)
            if overflow:
                app._scroll_panel(-10 ** 6)
                self.assertEqual(app.panel_offset, -overflow)
                app._scroll_panel(10 ** 6)
                self.assertEqual(app.panel_offset, 0)

    def test_default_home_shows_everything(self):
        root, app = self.open()
        self.wait_background(root, app)
        self.assertLessEqual(app.home_level, 2, 'default size keeps all decoration')
        for item in (app.hwin['thumb0'], app.htext['eyebrow'][-1], app.htext['subtitle'][-1]):
            self.assertNotEqual(app.canvas.itemcget(item, 'state'), 'hidden')

    def test_theme_choice_persists_only_appearance(self):
        config = core.default_config()
        core.save_config(self.path, config)
        root, app = self.open()
        self.wait_background(root, app, 'doolin')
        self.assertIn('selected', app.thumb_buttons['doolin'].state())
        app.v['width'].set('1920')            # an unsaved settings edit must not be saved by a theme click
        app.v['height'].set('1080')
        app.thumb_buttons['ember'].invoke()
        self.wait_background(root, app, 'ember')
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored['appearance']['theme'], 'ember')
        self.assertEqual((stored['display']['width'], stored['display']['height']), (1280, 720))
        self.assertIn('selected', app.thumb_buttons['ember'].state())
        self.assertNotIn('selected', app.thumb_buttons['doolin'].state())
        self.assertEqual(app.v_theme.get(), 'ember')
        self.assertEqual(app.canvas.itemcget(app.htext['scene_name'][-1], 'text'), 'Ember Procession')
        app.shutdown()
        root, app = self.open()
        self.assertEqual(app.theme, 'ember')

    def test_keyboard_escape_focus_return_and_thumbnails(self):
        root, app = self.open()
        root.focus_force()
        app.settings_button.focus_set()
        self.pump(root, app, 5)
        app.settings_button.event_generate('<Return>')
        self.pump(root, app, 5)
        self.assertEqual(app.current_page, 'settings')
        self.assertEqual(root.focus_lastfor(), app.page_back['settings'])
        root.event_generate('<Escape>')
        self.pump(root, app, 5)
        self.assertEqual(app.current_page, 'home')
        self.assertEqual(root.focus_lastfor(), app.settings_button)
        thumb = app.thumb_buttons['doolin']
        thumb.focus_set()
        self.pump(root, app, 3)
        thumb.event_generate('<Right>')
        self.pump(root, app, 3)
        self.assertEqual(root.focus_lastfor(), app.thumb_buttons['faery'])
        self.assertEqual(app.canvas.itemcget(app.htext['scene_name'][-1], 'text'), 'The Lantern Court')
        app.thumb_buttons['faery'].event_generate('<Return>')
        self.pump(root, app, 5)
        self.assertEqual(app.theme, 'faery')
        app.thumb_buttons['faery'].event_generate('<Left>')
        self.pump(root, app, 3)
        self.assertEqual(root.focus_lastfor(), app.thumb_buttons['doolin'])
        app.show('settings', tab=app.appearance_tab)
        self.pump(root, app, 5)
        self.assertEqual(str(app.settings_tabs.select()), str(app.appearance_tab))
        app.theme_radios['undersea'].invoke()
        self.assertEqual(app.theme, 'undersea')

    def test_missing_art_leaves_a_usable_launcher(self):
        root, app = self.open(art_dir=self.root_dir / 'no-art-here')
        self.wait_background(root, app)
        self.assertEqual(app.art.themes, {})
        self.assertIn('unavailable', app.status.get() + ' '.join(app.art.errors))
        self.assertIn('Artwork problem', app.diagnostics_text())
        app.show('settings')
        self.pump(root, app, 5)
        app.v['width'].set('1600')
        app.v['height'].set('900')
        self.assertTrue(app.save())
        self.assertEqual(json.loads(self.path.read_text())['display']['width'], 1600)

    def test_corrupt_painting_falls_back_to_ink(self):
        import shutil
        art = self.root_dir / 'art'
        shutil.copytree(Path(fl.theme.ART_DIR), art)
        (art / '01-doolin-threshold.png').write_bytes(b'\x89PNG\r\n\x1a\n broken')
        root, app = self.open(art_dir=art)
        self.wait_background(root, app, 'doolin')
        if fl.theme.HAVE_PIL:
            self.assertEqual(app._bg_image.getpixel((app._bg_image.width - 3, 3)), fl.theme.INK_RGB)
            self.assertTrue(any('01-doolin-threshold.png' in e for e in app.art.errors))
        app.thumb_buttons['faery'].invoke()
        self.wait_background(root, app, 'faery')
        self.assertNotEqual(app._bg_image.getpixel((app._bg_image.width - 3, 3)), fl.theme.INK_RGB)

    def test_launch_stops_and_releases_decoration(self):
        root, app = self.open()
        self.wait_background(root, app)
        app.thumb_buttons['undersea'].invoke()   # starts a fade
        self.pump(root, app, 1)
        child = mock.Mock(pid=4321)
        child.poll.return_value = None
        app._launched(True, child, core.default_config())
        self.assertIsNone(app._fade_after)
        self.assertEqual(app.art.cached, ())
        self.assertIsNone(app._bg_image)
        pending = root.tk.splitlist(root.tk.call('after', 'info'))
        scripts = [str(root.tk.call('after', 'info', p)[0]) for p in pending]
        self.assertFalse([sc for sc in scripts if 'step' in sc], scripts)

    def test_long_profile_path_is_flagged_in_setup(self):
        deep = self.root_dir / ('p' * max(1, core.ACCEPTED_PROFILE_PATH_MAX - len(str(self.root_dir)) - 9)) / 'dev_hdd0'
        deep.mkdir(parents=True)
        config = core.default_config()
        config['paths']['hdd0'] = str(deep)
        core.save_config(self.path, config)
        root, app = self.open()
        for _ in range(100):
            self.pump(root, app, 2)
            state = app.setup_state.get('hdd0')
            if state and 'characters' in state[1]:
                break
        self.assertEqual(app.setup_state['hdd0'][0], 'bad')
        self.assertIn('longer than 122 characters', app.setup_state['hdd0'][1])
        self.assertIn('disabled', app.play_button.state())

    def test_reduced_motion_switches_instantly(self):
        with mock.patch.dict(fl.theme.os.environ, {'FOLKLORE_REDUCED_MOTION': '1'}):
            root, app = self.open()
        self.assertTrue(app.reduced_motion)
        self.wait_background(root, app)
        app.thumb_buttons['faery'].invoke()
        for _ in range(100):
            self.pump(root, app, 2)
            self.assertIsNone(app._fade_after)
            if app._bg_key and app._bg_key[0] == 'faery':
                break

    def test_without_pillow_the_launcher_still_works(self):
        with mock.patch.object(fl.theme, 'HAVE_PIL', False):
            root, app = self.open('1100x700')
            self.wait_background(root, app)
            self.assertIsNotNone(app._native_photo)
            self.assertEqual(app.canvas.itemcget(app.fallback_shade, 'state'), 'normal')
            app.show('settings', tab=app.appearance_tab)
            self.pump(root, app, 5)
            self.assertNotIn('disabled', app.settings_save.state())

    def test_home_layout_at_common_shapes(self):
        """No overlap between the column and the theme row; column stays left of the characters."""
        for geometry in ('1280x720', '1600x900', '1024x768', '2560x1080', '3200x900', '760x900', '900x700', '720x560'):
            with self.subTest(geometry=geometry):
                root, app = self.open(geometry)
                self.wait_background(root, app)
                c = app.canvas
                W, H = c.winfo_width(), c.winfo_height()
                boxes = {tag: c.bbox(app.hwin[tag]) for tag in ('card', 'play', 'settings', 'setup')}
                boxes['title'] = c.bbox(app.htext['title'][-1])
                for tag, (x0, y0, x1, y1) in boxes.items():
                    self.assertGreaterEqual(x0, 0, tag)
                    self.assertLessEqual(x1, W, tag)
                    self.assertLessEqual(y1, H, tag)
                lowest = max(b[3] for b in boxes.values())
                if c.itemcget(app.hwin['thumb0'], 'state') != 'hidden':
                    self.assertLess(lowest, c.bbox(app.hwin['thumb0'])[1], geometry)
                self.assertLess(lowest, c.coords(app.htext['footer_rule'][0])[1])
                if W / H >= 1.45:
                    self.assertLessEqual(boxes['card'][2], W * 0.46, geometry)
                self.assertGreaterEqual(app.play_button.winfo_height(), app.px(44) - 2)
                app.shutdown()
                self.windows.remove(app)


if __name__ == '__main__':
    unittest.main()
