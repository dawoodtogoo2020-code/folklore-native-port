"""Folklore Native launcher: first-run setup, settings, local build and play.

Run with Python 3.12+ and Tkinter: folklore_launcher.py --config ABSOLUTE_JSON
[--build-recipe ABSOLUTE_JSON] [--check | --launch].
"""
import argparse
import copy
import json
import os
from pathlib import Path
import queue
import sys
import threading

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from backup_validation import validate_disc, validate_elf  # noqa: E402
from launcher_core import (ACCEPTED, BUILD_NAMES, CAMERA_ASPECTS, GAME_EXIT_OK, GRAPHICS_CHOICES,  # noqa: E402,F401
                           HUD_LIMITS, MAX_CAMERA_RATIO, MAX_DIMENSION, MAX_PIXELS, MIN_CAMERA_RATIO,
                           PATH_NAMES, SCHEMA_VERSION, NewerConfigError, build_command, candidate_paths,
                           check_launch_paths, copy_profile, create_profile, default_config,
                           discover_capabilities, effective_settings, intended_environment, launch,
                           load_config, load_config_info, load_recipe, overlaps, player_arguments,
                           save_config, validate_config, THEMES, check_profile_length,
                           TEXTURE_QUALITIES, texture_availability)
from launcher_build import BuildRun, read_checkpoint  # noqa: E402
import launcher_display  # noqa: E402
import launcher_theme as theme  # noqa: E402

PATH_LABELS = {'disc': 'Disc backup folder', 'elf': 'Decrypted EBOOT.elf', 'dev_flash': 'Firmware dev_flash folder',
               'hdd0': 'Save profile (dev_hdd0)', 'binary': 'Native build (FolkloreBoot.exe)'}
SETUP_ORDER = ('disc', 'elf', 'dev_flash', 'hdd0', 'binary')
PATH_HELP = {
    'disc': 'Your own Folklore USA (BCUS98147) disc 1.00 backup: the folder holding PS3_DISC.SFB and PS3_GAME. Read only.',
    'elf': 'EBOOT.BIN decrypted by you (for example RPCS3: File > Decrypt PS3 binaries). The launcher never decrypts or downloads files.',
    'dev_flash': 'dev_flash from your own PS3 firmware install (for example RPCS3\'s dev_flash). Read only; never downloaded.',
    'hdd0': 'Writable folder named dev_hdd0 for saves and installed game data, separate from everything above.',
    'binary': 'FolkloreBoot.exe built on this PC from your backup. It contains game-derived code: keep it private.'}
ANISO_LABELS = {'auto': 'Auto (game decides)', '1': 'Off (1×)', '2': '2×', '4': '4×', '8': '8×', '16': '16×'}
AA_LABELS = {'off': 'Off', 'fxaa': 'FXAA'}
UPSCALER_LABELS = {'off': 'Off', 'fsr1': 'AMD FSR 1 (spatial)'}
AO_LABELS = {'off': 'Off', 'cacao': 'CACAO (screen-space)', 'rayquery': 'Ray queried AO'}
PATH_TRACING_LABELS = {'off': 'Off', 'diffuse-preview': 'Diffuse path tracing preview', 'hybrid': 'Hybrid path tracing'}
DLSS_LABELS = {'off': 'Off', 'quality': 'Quality', 'balanced': 'Balanced',
               'performance': 'Performance', 'ultra-performance': 'Ultra performance', 'dlaa': 'DLAA'}
SHARPEN_LABELS = {str(v): f'{v}%' for v in range(101)}
DENOISE_LABELS = {'off': 'Off', 'spatial': 'Spatial (depth and normal guided)'}
PATH_STRENGTH_LABELS = {'20': '20% (subtle)', '35': '35% (default)',
                        '60': '60%', '100': '100% (full preview)'}
GRAPHICS_LABELS = {'anisotropy': ANISO_LABELS, 'anti_aliasing': AA_LABELS, 'upscaler': UPSCALER_LABELS,
                   'ambient_occlusion': AO_LABELS, 'path_tracing': PATH_TRACING_LABELS,
                   'ray_denoiser': DENOISE_LABELS, 'path_strength': PATH_STRENGTH_LABELS,
                   'dlss': DLSS_LABELS, 'sharpen': SHARPEN_LABELS}
GRAPHICS_TITLES = {'anisotropy': 'Anisotropic filtering', 'anti_aliasing': 'Anti-aliasing',
                   'upscaler': 'Spatial upscaling', 'ambient_occlusion': 'Ambient occlusion',
                   'path_tracing': 'Path tracing', 'ray_denoiser': 'Ray denoiser',
                   'path_strength': 'Preview lighting strength', 'dlss': 'NVIDIA DLSS', 'sharpen': 'Sharpening'}
GRAPHICS_HELP = {'anisotropy': 'Sharper textures at glancing angles.',
                 'anti_aliasing': 'Smooths jagged edges after rendering.',
                 'upscaler': 'Upscales the internal image to the output resolution. FSR 1 is not DLSS.',
                 'ambient_occlusion': 'Adds contact shadows. CACAO is screen-space; ray queried AO is experimental.',
                 'path_tracing': 'Hybrid adds traced shadows, bounce lighting and supported reflections. Material recovery is still experimental.',
                 'ray_denoiser': 'Depth and normal guided smoothing. Hybrid also reuses lighting history; DLSS Ray Reconstruction is unavailable.',
                 'path_strength': 'Mixes the diffuse preview over authored lighting. This is not physical light intensity.',
                 'dlss': 'Requires a compatible NVIDIA GPU and the verified release runtime. Experimental; applies next launch.',
                 'sharpen': 'Final image sharpening, from 0% (off) to 100%.'}
TEXTURE_LABELS = {'low': 'Low · original', 'medium': 'Medium · 2× pack', 'high': 'High · 4× pack'}
PRESETS = ((1280, 720, '16:9'), (1600, 900, '16:9'), (1920, 1080, '16:9'), (2560, 1440, '16:9'),
           (3840, 2160, '16:9'), (1280, 800, '16:10'), (1920, 1200, '16:10'), (2560, 1080, '21:9'),
           (3440, 1440, '21:9'), (5120, 1440, '32:9'))
NOT_AVAILABLE = (
    ('Frame rate 120 / unlocked', 'Not supported. The optional 60 FPS mode is experimental; '
     'game speed, cloth and broad scene coverage remain unverified.'),
    ('RT lighting', 'Unavailable: game lights and materials are not supplied to the ray renderer.'),
    ('RT reflections', 'Unavailable: reflected surfaces lack complete game materials and lighting.'),
    ('DLSS frame generation', 'Unavailable: the game adapter needs full aligned depth, motion and presentation timing.'),
    ('Dynamic resolution', 'Not supported. Use the fixed internal render scale.'),
    ('Audio device, surround and latency', 'Audio uses the Windows default output device in stereo. No selection or latency control yet.'))
KNOWN_ISSUES = ('Known issues: stutters and uneven frame pacing, '
                'and an occasional hang at the opening movie (restart the game if it stays black).')


def ratio_text(w, h):
    r = w / h
    for label, value in (('16:9', 16 / 9), ('16:10', 16 / 10), ('4:3', 4 / 3), ('32:9', 32 / 9)):
        if abs(r - value) < 0.005:
            return label
    return f'{r * 9:.1f}:9'


def parse_dimension(text, label):
    text = text.strip()
    if not text.isdigit() or text.startswith('0'):
        raise ValueError(f'{label} must be a whole number from 1 to {MAX_DIMENSION}.')
    return int(text)


def tail(path, lines=30, limit=65536):
    try:
        with Path(path).open('rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit))
            return '\n'.join(f.read().decode('utf-8', 'replace').splitlines()[-lines:])
    except OSError as e:
        return f'(cannot read {path}: {e})'


def check_setup_item(key, paths):
    """(state, message) for one setup row; state is 'ok', 'warn' or 'bad'. May hash the ELF."""
    value = paths[key].strip()
    if not value:
        return 'bad', 'Not set. ' + PATH_HELP[key]
    p = Path(value)
    if not p.is_absolute():
        return 'bad', 'Use a full path (for example D:\\Games\\...).'
    try:
        if key == 'disc':
            if not p.is_dir():
                return 'bad', 'Folder not found.'
            validate_disc(p)
            return 'ok', 'Folklore USA BCUS98147, disc version 01.00.'
        if key == 'elf':
            if not p.is_file():
                return 'bad', 'File not found.'
            validate_elf(p)
            return 'ok', 'Matches the supported decrypted USA 1.00 EBOOT.elf (SHA-256 checked).'
        if key == 'dev_flash':
            if not p.is_dir():
                return 'bad', 'Folder not found.'
            if not (p / 'sys').is_dir():
                return 'warn', 'This folder has no sys subfolder; check that it is a dev_flash folder.'
            return 'ok', 'Firmware folder found (read only).'
        if key == 'binary':
            if not p.is_file():
                return 'bad', 'File not found. Build one below or browse to an existing FolkloreBoot.exe.'
            return 'ok', 'Executable found.'
        if key == 'hdd0':
            if p.name != 'dev_hdd0':
                return 'bad', 'The profile folder must be named dev_hdd0. Use New profile to create one.'
            if not p.is_dir():
                return 'bad', 'Folder not found. Use New profile or Copy profile.'
            full = {k: v.strip() for k, v in paths.items()}
            if all(full[k] and Path(full[k]).exists() for k in PATH_NAMES):
                check_launch_paths(full)
            return 'ok', 'Separate writable profile. Saves stay here.'
    except ValueError as e:
        return 'bad', str(e)
    except OSError as e:
        return 'bad', f'Cannot read it: {e}'
    return 'bad', 'Unknown item.'


class App:
    def __init__(self, root, config_path, recipe_path=None, art_dir=None):
        import tkinter as tk
        from tkinter import ttk, font as tkfont, messagebox
        self.tk, self.ttk, self.messagebox = tk, ttk, messagebox
        self.root, self.config_path = root, Path(config_path)
        self.recipe_path = recipe_path
        self.tasks = queue.Queue()
        self.child = self.build_run = None
        self.last_build = None
        self.read_only = False
        self.notice = ''
        self.config, info = self._load()
        self.stored_schema = info.get('schema')
        if info.get('migrated'):
            self.notice = (f'Settings were read from launcher schema {info["schema"]}. Saving writes schema '
                           f'{SCHEMA_VERSION} and keeps one copy of the old file beside it.')
        self.recipe = None
        if recipe_path:
            try:
                self.recipe = load_recipe(recipe_path)
            except (OSError, ValueError) as e:
                self.notice += f' Build recipe not loaded: {e}'
        if self.recipe and not any(self.config['build'].values()):
            for k in BUILD_NAMES:
                self.config['build'][k] = self.recipe[k]
        self.saved = copy.deepcopy(self.config)
        self.caps, self.caps_for = ACCEPTED, None
        self.setup_state = {}
        self.monitors = launcher_display.list_monitors()
        self.dpi = 96

        self.art = theme.ArtLibrary(art_dir) if art_dir else theme.ArtLibrary()
        self.theme = self.config['appearance']['theme']
        self.reduced_motion = theme.reduced_motion()
        self.current_page = None
        self.return_focus = {}
        self._bg_token = None
        self._bg_photo = self._bg_image = self._native_photo = None
        self._bg_key = None
        self._fade_after = self._layout_after = None
        self.thumb_images, self.companion_photo = {}, None
        self._make_fonts(tkfont)
        self._apply_style()

        root.title('Folklore Native')
        root.configure(background=theme.INK)
        self._place_window()
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.pages, self.page_windows = {}, {}
        self.canvas = tk.Canvas(root, highlightthickness=0, borderwidth=0, background=theme.INK)
        self.canvas.grid(sticky='nsew')
        self.bg_item = self.canvas.create_image(0, 0, anchor='nw')
        self.fallback_shade = self.canvas.create_rectangle(0, 0, 0, 0, fill=theme.INK, outline='', state='hidden')
        self.fallback_band = self.canvas.create_rectangle(0, 0, 0, 0, fill=theme.INK, outline='', state='hidden')
        self.container = self.canvas
        self._build_home()
        self._build_setup()
        self._build_settings()
        self._build_advanced()
        self.load_into_ui(self.config)
        self.show('home')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Configure>', self._on_configure)
        root.bind('<Map>', lambda _e: self._schedule_layout())
        root.bind('<Escape>', self._escape)
        root.bind_all('<MouseWheel>', self._on_wheel, add='+')
        root.bind_all('<FocusIn>', self._focus_into_view, add='+')
        for cls in ('TButton', 'TRadiobutton', 'TCheckbutton'):
            root.bind_class(cls, '<Return>', lambda e: e.widget.invoke(), add='+')
            root.bind_class(cls, '<KP_Enter>', lambda e: e.widget.invoke(), add='+')
        self.refresh_capabilities()
        self.check_setup()
        root.after(150, self._pump)

    # ------------------------------------------------------------------ helpers
    def _load(self):
        try:
            return load_config_info(self.config_path)
        except NewerConfigError as e:
            self.read_only = True
            self.notice = str(e) + ' Changes in this window are used for launching but not saved.'
        except (ValueError, OSError) as e:
            self.notice = (f'The settings file could not be read ({e}). Defaults are loaded and the file was left '
                           'unchanged; saving keeps a copy of it beside the new file.')
        return default_config(), {}

    def px(self, n):
        return round(n * max(self.dpi, 96) / 96)

    def run_bg(self, fn, done, *args):
        def work():
            try:
                result = (True, fn(*args))
            except Exception as e:  # reported to the UI, never swallowed
                result = (False, e)
            self.tasks.put((done, result))
        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        while True:
            try:
                done, (ok, value) = self.tasks.get_nowait()
            except queue.Empty:
                break
            done(ok, value)
        self._watch_child()
        self._watch_build()
        self.root.after(150, self._pump)

    def _target_monitor(self):
        index = self.config['display']['monitor']
        if 0 <= index < len(self.monitors):
            return self.monitors[index]
        return next((m for m in self.monitors if m.primary), None)

    def _place_window(self):
        m = self._target_monitor()
        if m is None:
            self.root.geometry('1120x700')
            self.root.minsize(720, 560)
            return
        self._apply_dpi(m.dpi)
        x, y, w, h, mw, mh = launcher_display.fit_geometry(m.work, m.dpi, (1120, 700), minimum=(720, 560))
        self.root.minsize(mw, mh)
        self.root.geometry(f'{w}x{h}+{x}+{y}')
        self.window_monitor = m

    def _apply_dpi(self, dpi):
        if dpi == self.dpi:
            return
        self.dpi = dpi
        self.root.tk.call('tk', 'scaling', dpi / 72)
        for f in self.fonts.values():
            f.configure(size=f.cget('size'))  # recompute pixel size at the new scaling
        if getattr(self, 'canvas', None) is not None:
            self._apply_style()
            self._load_thumbnails()
            self._schedule_layout()

    def _make_fonts(self, tkfont):
        families = set(tkfont.families(self.root))
        serif = next((f for f in theme.TITLE_FONT if f in families), 'Times')
        sans = next((f for f in theme.UI_FONT if f in families), tkfont.nametofont('TkDefaultFont').cget('family'))
        base = tkfont.nametofont('TkDefaultFont')
        base.configure(family=sans, size=10)
        for name in ('TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
            tkfont.nametofont(name).configure(family=sans, size=10)
        self.fonts = {'base': base,
                      'heading': tkfont.Font(self.root, family=sans, size=12, weight='bold'),
                      'small': tkfont.Font(self.root, family=sans, size=9),
                      'eyebrow': tkfont.Font(self.root, family=sans, size=8),
                      'button': tkfont.Font(self.root, family=sans, size=11, weight='bold'),
                      'brand': tkfont.Font(self.root, family=serif, size=13),
                      'page': tkfont.Font(self.root, family=serif, size=17),
                      'subtitle': tkfont.Font(self.root, family=serif, size=16, slant='italic'),
                      'scene': tkfont.Font(self.root, family=serif, size=18)}
        # Hero type is sized in pixels from the window width (see _layout_home).
        self.title_font = tkfont.Font(self.root, family=serif, size=-56)

    def _apply_style(self):
        """Dark ttk styling from theme-tokens.css; keyboard focus is always drawn."""
        ttk, t, px = self.ttk, theme, self.px
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=t.PANEL, foreground=t.IVORY, fieldbackground=t.FIELD,
                        bordercolor=t.RULE, lightcolor=t.PANEL, darkcolor=t.PANEL, troughcolor=t.FIELD,
                        selectbackground=t.GOLD, selectforeground=t.INK, insertcolor=t.IVORY,
                        focuscolor=t.GOLD_LIGHT, arrowcolor=t.MUTED, font=self.fonts['base'])
        style.map('.', foreground=[('disabled', t.DISABLED)], fieldbackground=[('disabled', t.PANEL)])
        style.configure('Card.TFrame', background=t.PANEL, bordercolor=t.RULE, relief='solid', borderwidth=1)
        style.configure('TLabel', background=t.PANEL, foreground=t.IVORY)
        style.configure('Heading.TLabel', font=self.fonts['heading'])
        style.configure('Page.TLabel', font=self.fonts['page'], foreground=t.IVORY)
        style.configure('Muted.TLabel', foreground=t.MUTED, font=self.fonts['small'])
        style.configure('Ok.TLabel', foreground=t.JADE)
        style.configure('Warn.TLabel', foreground=t.WARN)
        style.configure('Bad.TLabel', foreground=t.ERROR)
        style.configure('Eyebrow.TLabel', foreground=t.GOLD_LIGHT, font=self.fonts['eyebrow'])
        style.configure('TLabelframe', background=t.PANEL, bordercolor=t.RULE)
        style.configure('TLabelframe.Label', background=t.PANEL, foreground=t.GOLD_LIGHT)
        style.configure('TButton', background=t.PANEL, foreground=t.IVORY, bordercolor=t.RULE, lightcolor=t.PANEL,
                        darkcolor=t.PANEL, padding=(px(12), px(6)), focusthickness=2)
        style.map('TButton', background=[('disabled', t.PANEL), ('pressed', '#1b2a33'), ('active', '#18252e')],
                  bordercolor=[('focus', t.GOLD_LIGHT), ('active', t.GOLD)], foreground=[('disabled', t.DISABLED)])
        style.configure('Quiet.TButton', background='#0d161d', padding=(px(16), px(9)))
        style.configure('Play.TButton', font=self.fonts['button'], background=t.GOLD, foreground='#172027',
                        bordercolor='#ecd7a5', lightcolor='#d7be87', darkcolor='#b59b66', padding=(px(22), px(13)),
                        focuscolor='#172027')
        style.map('Play.TButton', background=[('disabled', '#3a3a33'), ('pressed', '#b59b66'), ('active', t.GOLD_LIGHT)],
                  foreground=[('disabled', '#8a877d')], bordercolor=[('focus', '#ffffff'), ('active', t.GOLD_LIGHT)])
        style.configure('Thumb.TButton', background=t.INK, bordercolor='#4b4f53', lightcolor=t.INK, darkcolor=t.INK,
                        padding=px(3), focusthickness=0)
        style.map('Thumb.TButton', bordercolor=[('selected', t.GOLD_LIGHT), ('focus', t.JADE), ('active', t.GOLD)],
                  background=[('selected', t.GOLD_LIGHT), ('focus', t.JADE), ('active', '#18252e')],
                  foreground=[('selected', t.INK), ('focus', t.INK)])
        for widget in ('TEntry', 'TCombobox', 'TSpinbox'):
            style.configure(widget, fieldbackground=t.FIELD, foreground=t.IVORY, bordercolor=t.RULE,
                            lightcolor=t.FIELD, darkcolor=t.FIELD, arrowcolor=t.GOLD_LIGHT, padding=px(3))
            style.map(widget, bordercolor=[('focus', t.GOLD_LIGHT)], lightcolor=[('focus', t.GOLD_LIGHT)],
                      fieldbackground=[('readonly', t.FIELD), ('disabled', t.PANEL)],
                      foreground=[('disabled', t.DISABLED)])
        for widget in ('TRadiobutton', 'TCheckbutton'):
            style.configure(widget, background=t.PANEL, foreground=t.IVORY, indicatorbackground=t.FIELD,
                            indicatorforeground=t.GOLD_LIGHT, focuscolor=t.GOLD_LIGHT)
            style.map(widget, background=[('active', t.PANEL)],
                      indicatorbackground=[('selected', t.GOLD), ('disabled', t.PANEL)],
                      foreground=[('disabled', t.DISABLED)])
        style.configure('TNotebook', background=t.PANEL, bordercolor=t.RULE, tabmargins=(0, px(4), 0, 0))
        style.configure('TNotebook.Tab', background='#0d161d', foreground=t.MUTED, bordercolor=t.RULE,
                        lightcolor=t.PANEL, padding=(px(14), px(6)))
        style.map('TNotebook.Tab', background=[('selected', t.PANEL)], foreground=[('selected', t.GOLD_LIGHT)],
                  lightcolor=[('selected', t.GOLD)])
        style.configure('Horizontal.TProgressbar', background=t.GOLD, troughcolor=t.FIELD, bordercolor=t.RULE,
                        lightcolor=t.GOLD, darkcolor=t.GOLD)
        style.configure('TScrollbar', background='#0d161d', troughcolor=t.FIELD, bordercolor=t.RULE,
                        arrowcolor=t.MUTED)
        for key, value in (('background', t.FIELD), ('foreground', t.IVORY), ('selectBackground', t.GOLD),
                           ('selectForeground', t.INK)):
            self.root.option_add(f'*TCombobox*Listbox.{key}', value)

    def _on_configure(self, event):
        if event.widget is not self.root:
            return
        self._schedule_layout()
        if not self.monitors:
            return
        try:
            hwnd = int(self.root.wm_frame(), 16)
        except (ValueError, self.tk.TclError):
            return
        m = launcher_display.monitor_of_window(hwnd, self.monitors)
        if m and m.dpi != self.dpi:
            self._apply_dpi(m.dpi)

    def show(self, name, tab=None):
        """Switch page; Back/Escape returns keyboard focus to the control that opened it."""
        previous = self.current_page
        if previous == name and tab is None:
            return
        if name != 'home':
            try:
                self.return_focus[name] = self.root.focus_get()
            except (KeyError, self.tk.TclError):
                self.return_focus[name] = None
        self.current_page = name
        self.panel_offset = 0
        home = name == 'home'
        for item in self.home_items_all:
            self.canvas.itemconfigure(item, state='normal' if home else 'hidden')
        for page, window in self.page_windows.items():
            self.canvas.itemconfigure(window, state='normal' if page == name else 'hidden')
        if name == 'settings' and tab is not None:
            self.settings_tabs.select(tab)
        if name == 'advanced':
            self.refresh_diagnostics()
        if home:
            target = self.return_focus.get(previous) or self.play_button
            if target is not None and str(target) and target.winfo_exists():
                target.focus_set()
        else:
            self.page_back[name].focus_set()
        self._layout()
        self.render_background()

    def _escape(self, _event=None):
        if self.current_page not in (None, 'home'):
            self.show('home')
            return 'break'
        return None

    def page(self, name, title=None):
        ttk = self.ttk
        frame = ttk.Frame(self.canvas, padding=self.px(22), style='Card.TFrame')
        frame.columnconfigure(0, weight=1)
        self.pages[name] = frame
        self.page_windows[name] = self.canvas.create_window(0, 0, anchor='nw', window=frame, state='hidden')
        self.page_back = getattr(self, 'page_back', {})
        if title:
            head = ttk.Frame(frame)
            head.grid(row=0, column=0, sticky='ew', pady=(0, self.px(12)))
            self.page_back[name] = ttk.Button(head, text='← Back', style='Quiet.TButton',
                                              command=lambda: self.show('home'))
            self.page_back[name].pack(side='left')
            ttk.Label(head, text=title, style='Page.TLabel').pack(side='left', padx=self.px(16))
            ttk.Label(head, text='Esc returns home', style='Muted.TLabel').pack(side='right')
        return frame

    # ------------------------------------------------------------------ home
    def _text(self, tag, font, fill, anchor='nw', text='', shadow=True, justify='left'):
        """Live canvas text over the painting, with a soft dark offset copy for legibility."""
        c, items = self.canvas, []
        if shadow:
            items.append(c.create_text(0, 0, text=text, font=font, fill='#03070b', anchor=anchor, justify=justify))
        items.append(c.create_text(0, 0, text=text, font=font, fill=fill, anchor=anchor, justify=justify))
        self.htext[tag] = items
        self.home_items_all.extend(items)
        return items

    def _window(self, tag, widget, anchor='nw'):
        item = self.canvas.create_window(0, 0, window=widget, anchor=anchor)
        self.hwin[tag] = item
        self.home_items_all.append(item)
        return widget

    def _build_home(self):
        ttk, tk, t = self.ttk, self.tk, theme
        c = self.canvas
        self.htext, self.hwin, self.home_items_all = {}, {}, []
        self._text('sigil', self.fonts['brand'], t.GOLD, text='\u2727', shadow=False)
        self._text('brand', self.fonts['brand'], t.IVORY, text='F O L K L O R E', shadow=False)
        self._window('appearance', ttk.Button(c, text='Appearance', style='Quiet.TButton',
                                              command=lambda: self.show('settings', tab=self.appearance_tab)), 'ne')
        rule = c.create_line(0, 0, 0, 0, fill=t.GOLD)
        self.htext['eyebrow_rule'] = [rule]
        self.home_items_all.append(rule)
        self._text('eyebrow', self.fonts['eyebrow'], t.GOLD_LIGHT, text='B E Y O N D   T H E   V E I L', shadow=False)
        self._text('title', self.title_font, t.IVORY, text='FOLKLORE')
        self._text('subtitle', self.fonts['subtitle'], '#d5cbb9', text='Some stories never leave you.')
        card = ttk.Frame(c, style='Card.TFrame', padding=(self.px(16), self.px(12)))
        card.columnconfigure(0, weight=1)
        self.home_state = ttk.Label(card, text='Checking setup…', style='Heading.TLabel')
        self.home_state.grid(row=0, column=0, sticky='w')
        self.home_items = ttk.Label(card, text='', justify='left')
        self.home_items.grid(row=1, column=0, sticky='w', pady=(self.px(4), 0))
        self.home_build = ttk.Label(card, text='', style='Muted.TLabel', justify='left')
        self.home_build.grid(row=2, column=0, sticky='w', pady=(self.px(6), 0))
        self.status = tk.StringVar(value=self.notice)
        self.status_label = ttk.Label(card, textvariable=self.status, justify='left')
        self.status_label.grid(row=3, column=0, sticky='w', pady=(self.px(6), 0))
        self.home_card = self._window('card', card)
        self.play_button = self._window('play', ttk.Button(c, text='Play Folklore    \u2197', style='Play.TButton',
                                                           command=self.play))
        self.settings_button = self._window('settings', ttk.Button(c, text='Settings', style='Quiet.TButton',
                                                                   command=lambda: self.show('settings')))
        self.setup_button = self._window('setup', ttk.Button(c, text='Build / Setup', style='Quiet.TButton',
                                                             command=lambda: self.show('setup')))
        self._text('themes_label', self.fonts['eyebrow'], t.GOLD_LIGHT, anchor='sw',
                   text='C H O O S E   Y O U R   A T M O S P H E R E', shadow=False)
        self.thumb_buttons = {}
        for i, tid in enumerate(THEMES):
            b = ttk.Button(c, style='Thumb.TButton', command=lambda tid=tid: self.apply_theme(tid))
            b.bind('<Left>', lambda _e, i=i: self._thumb_focus(i - 1))
            b.bind('<Right>', lambda _e, i=i: self._thumb_focus(i + 1))
            b.bind('<FocusIn>', lambda _e, tid=tid: self._preview_scene_text(tid))
            b.bind('<FocusOut>', lambda _e: self._preview_scene_text(self.theme))
            self.thumb_buttons[tid] = self._window(f'thumb{i}', b)
        self._text('scene_eyebrow', self.fonts['eyebrow'], t.GOLD_LIGHT, anchor='se',
                   text='O R I G I N A L   L A U N C H E R   A R T', shadow=False, justify='right')
        self._text('scene_name', self.fonts['scene'], t.IVORY, anchor='se', justify='right')
        self._text('scene_desc', self.fonts['small'], t.MUTED, anchor='se', justify='right')
        line = c.create_line(0, 0, 0, 0, fill=t.RULE)
        self.htext['footer_rule'] = [line]
        self.home_items_all.append(line)
        self._text('issues', self.fonts['small'], '#cfc8bb', anchor='w', text=KNOWN_ISSUES, shadow=False)
        self._window('advanced', ttk.Button(c, text='Advanced', style='Quiet.TButton',
                                            command=lambda: self.show('advanced')), 'e')
        self._load_thumbnails()

    def _thumb_focus(self, index):
        self.thumb_buttons[THEMES[index % len(THEMES)]].focus_set()
        return 'break'

    def _preview_scene_text(self, tid):
        for item in self.htext['scene_name']:
            self.canvas.itemconfigure(item, text=self.art.name(tid))
        for item in self.htext['scene_desc']:
            self.canvas.itemconfigure(item, text=self.art.description(tid))

    def _load_thumbnails(self):
        """Bounded 200 x 113 thumbnail resources, scaled for the current DPI."""
        size = (self.px(100), self.px(57))
        for tid in THEMES:
            path = self.art.path(tid, thumbnail=True)
            image = None
            try:
                if path and theme.HAVE_PIL:
                    from PIL import Image, ImageTk
                    with Image.open(path) as im:
                        image = ImageTk.PhotoImage(im.convert('RGB').resize(size, Image.LANCZOS), master=self.root)
                elif path:
                    image = self.tk.PhotoImage(file=str(path), master=self.root)
                    factor = max(1, round(image.width() / size[0]))
                    image = image.subsample(factor) if factor > 1 else image
            except (OSError, ValueError, self.tk.TclError) as e:
                self.art.errors.append(f'Thumbnail {path} could not be loaded ({e}).')
                image = None
            self.thumb_images[tid] = image
            b = self.thumb_buttons.get(tid) if hasattr(self, 'thumb_buttons') else None
            if b is not None:
                b.configure(image=image or '', text='' if image else self.art.name(tid),
                            width=0)
            radio = getattr(self, 'theme_radios', {}).get(tid)
            if radio is not None:
                radio.configure(image=image or '')

    # ------------------------------------------------------------------ layout and background
    def _schedule_layout(self):
        if self._layout_after:
            self.root.after_cancel(self._layout_after)
        self._layout_after = self.root.after(80, self._layout_and_render)

    def _layout_and_render(self):
        self._layout_after = None
        self._layout()
        self.render_background()

    def _layout(self):
        c = self.canvas
        W, H = c.winfo_width(), c.winfo_height()
        if W < 2 or H < 2:
            return
        if not theme.HAVE_PIL or self._bg_photo is None:
            c.coords(self.bg_item, W, 0)
            c.itemconfigure(self.bg_item, anchor='ne')
        else:
            c.coords(self.bg_item, 0, 0)
            c.itemconfigure(self.bg_item, anchor='nw')
        if self.current_page == 'home':
            self._layout_home(W, H)
        elif self.current_page in self.page_windows:
            m = max(self.px(12), round(min(W, H) * 0.025))
            width = min(W - 2 * m, self.px(1240))
            frame, window = self.pages[self.current_page], self.page_windows[self.current_page]
            c.itemconfigure(window, width=width)
            frame.update_idletasks()
            need, room = frame.winfo_reqheight(), H - 2 * m
            self.panel_room = room
            self.panel_overflow = max(0, need - room)
            self.panel_offset = min(0, max(-self.panel_overflow, getattr(self, 'panel_offset', 0)))
            c.itemconfigure(window, height=max(room, need))
            c.coords(window, (W - width) // 2, m + self.panel_offset)

    def _scroll_panel(self, delta):
        """Mouse wheel / keyboard focus scrolling for a panel taller than the window."""
        if self.current_page in (None, 'home') or not getattr(self, 'panel_overflow', 0):
            return
        self.panel_offset = min(0, max(-self.panel_overflow, self.panel_offset + delta))
        self._layout()

    def _on_wheel(self, event):
        self._scroll_panel(self.px(60) * (1 if event.delta > 0 else -1))

    def _focus_into_view(self, event):
        if self.current_page in (None, 'home') or not getattr(self, 'panel_overflow', 0):
            return
        widget = event.widget
        try:
            frame = self.pages[self.current_page]
            if not str(widget).startswith(str(frame)):
                return
            top = widget.winfo_rooty() - frame.winfo_rooty()
        except (KeyError, self.tk.TclError, AttributeError):
            return
        bottom = top + widget.winfo_height()
        visible_top, visible_bottom = -self.panel_offset, -self.panel_offset + self.panel_room
        if top < visible_top:
            self._scroll_panel(visible_top - top + self.px(12))
        elif bottom > visible_bottom:
            self._scroll_panel(visible_bottom - bottom - self.px(12))

    def _move(self, tag, x, y):
        for i, item in enumerate(self.htext[tag]):
            off = self.px(2) if len(self.htext[tag]) == 2 and i == 0 else 0
            self.canvas.coords(item, x + off, y + off)

    def _show_tag(self, tag, visible):
        items = self.htext.get(tag) or [self.hwin[tag]]
        for item in items:
            self.canvas.itemconfigure(item, state='normal' if visible else 'hidden')

    def _layout_home(self, W, H):
        """Home composition from the art direction: left column 6.5%..43%, title near 29%, themes at 79%."""
        c, px = self.canvas, self.px
        s = max(self.dpi, 96) / 96
        lw, lh = W / s, H / s
        narrow = lw < 760
        left = round(W * (0.08 if narrow else 0.065))
        colw = round(W * 0.84) if narrow else max(px(300), min(round(W * 0.43) - left, px(560)))
        # header
        self._move('sigil', round(W * 0.055), px(30))
        self._move('brand', round(W * 0.055) + px(30), px(32))
        c.coords(self.hwin['appearance'], W - round(W * 0.055), px(24))
        # footer
        footer = H - px(42)
        c.coords(self.htext['footer_rule'][0], left, footer, W - left, footer)
        adv = self.hwin['advanced']
        c.coords(adv, W - left, footer + px(21))
        adv_w = c.nametowidget(c.itemcget(adv, 'window')).winfo_reqwidth()
        for item in self.htext['issues']:
            c.itemconfigure(item, width=max(px(160), W - 2 * left - adv_w - px(24)))
        self._move('issues', left, footer + px(21))
        # theme thumbnails and scene title
        thumb_w = self.thumb_buttons[THEMES[0]].winfo_reqwidth()
        thumb_h = self.thumb_buttons[THEMES[0]].winfo_reqheight()
        bottom = footer - px(24)
        thumbs_top = bottom - thumb_h
        x = left
        for i, tid in enumerate(THEMES):
            c.coords(self.hwin[f'thumb{i}'], x, thumbs_top)
            x += self.thumb_buttons[tid].winfo_reqwidth() + px(10)
        self._move('themes_label', left, thumbs_top - px(10))
        right = W - left
        self._move('scene_desc', right, bottom)
        desc_h = self.fonts['small'].metrics('linespace')
        name_h = self.fonts['scene'].metrics('linespace')
        self._move('scene_name', right, bottom - desc_h - px(4))
        self._move('scene_eyebrow', right, bottom - desc_h - name_h - px(10))
        thumbs_row_w = x - left
        scene_room = right - (left + thumbs_row_w) > px(260)
        # vertical flow, collapsing decoration when space runs out
        title_px = round((min(80, max(40, lw * 0.11)) if narrow else min(106, max(48, lw * 0.063))) * s)
        card = self.home_card
        for label in (self.home_items, self.home_build, self.status_label):
            label.configure(wraplength=colw - px(34))
        c.itemconfigure(self.hwin['card'], width=colw)
        card.update_idletasks()
        card_h = card.winfo_reqheight()
        play_h = self.play_button.winfo_reqheight()
        sec_h = self.settings_button.winfo_reqheight()
        eyebrow_h = self.fonts['eyebrow'].metrics('linespace')
        sub_h = self.fonts['subtitle'].metrics('linespace')
        limit_with_thumbs = thumbs_top - px(34) - eyebrow_h
        settings_w = self.settings_button.winfo_reqwidth()
        setup_w = self.setup_button.winfo_reqwidth()
        play_w = min(colw, max(px(285), min(px(360), colw)))
        one_row = colw >= play_w + settings_w + setup_w + px(24)
        # Collapse order: move up, put actions on one row, drop eyebrow/subtitle, hide themes, shrink title.
        steps = [dict(top=0.20), dict(top=0.13), dict(top=0.12, row=one_row),
                 dict(top=0.11, deco=False, row=one_row), dict(top=0.08, deco=False, row=one_row, thumbs=False),
                 dict(top=0.06, deco=False, row=one_row, thumbs=False, small=True)]
        for level, step in enumerate(steps):
            show_eyebrow = show_sub = step.get('deco', True)
            show_thumbs = step.get('thumbs', True)
            same_row = step.get('row', False)
            size = round(title_px * 0.7) if step.get('small') else title_px
            self.title_font.configure(size=-size)
            title_h = self.title_font.metrics('linespace')
            top = max(round(H * step['top']), px(70))  # never into the header row
            needed = ((eyebrow_h + px(18)) if show_eyebrow else 0) + title_h + px(8) + \
                ((sub_h + px(14)) if show_sub else 0) + card_h + px(16) + play_h + \
                (0 if same_row else px(12) + sec_h)
            limit = limit_with_thumbs if show_thumbs else footer - px(16)
            if top + needed <= limit:
                break
        self.home_level = level
        y = top
        self._show_tag('eyebrow', show_eyebrow)
        self._show_tag('eyebrow_rule', show_eyebrow)
        if show_eyebrow:
            c.coords(self.htext['eyebrow_rule'][0], left, y + eyebrow_h // 2, left + px(28), y + eyebrow_h // 2)
            self._move('eyebrow', left + px(40), y)
            y += eyebrow_h + px(18)
        self._move('title', left, y)
        y += title_h + px(8)
        self._show_tag('subtitle', show_sub)
        if show_sub:
            self._move('subtitle', left, y)
            y += sub_h + px(14)
        c.coords(self.hwin['card'], left, y)
        y += card_h + px(16)
        c.coords(self.hwin['play'], left, y)
        c.itemconfigure(self.hwin['play'], width=play_w)
        if same_row:
            row_y = y + (play_h - sec_h) // 2
            c.coords(self.hwin['settings'], left + play_w + px(12), row_y)
            c.coords(self.hwin['setup'], left + play_w + px(12) + settings_w + px(10), row_y)
        else:
            y += play_h + px(12)
            c.coords(self.hwin['settings'], left, y)
            c.coords(self.hwin['setup'], left + settings_w + px(10), y)
        for i in range(len(THEMES)):
            self._show_tag(f'thumb{i}', show_thumbs)
        self._show_tag('themes_label', show_thumbs)
        for tag in ('scene_eyebrow', 'scene_name', 'scene_desc'):
            # Without Pillow there is no scrim, so no small text is printed over the painting.
            self._show_tag(tag, show_thumbs and scene_room and not narrow and theme.HAVE_PIL)
        self._show_tag('brand', lw >= 620)

    def render_background(self, fade=False):
        """Compose the current painting for this window off the UI thread; swap without a flash."""
        c = self.canvas
        W, H = c.winfo_width(), c.winfo_height()
        if W < 2 or H < 2 or self.current_page is None:
            return
        try:
            if self.root.state() == 'iconic':
                return  # hidden: no decorative work
        except self.tk.TclError:
            return
        kind = 'home' if self.current_page == 'home' else 'panel'
        key = (self.theme, W, H, kind)
        if key == getattr(self, '_bg_key', None):
            return
        token = self._bg_token = object()
        entry = self.art.themes.get(self.theme, {})
        if not theme.HAVE_PIL:
            self._native_background(W, H, kind)
            self._bg_key = key
            return

        def work():
            source = self.art.load(self.theme)
            return theme.compose(source, W, H, entry, kind), source is not None

        def done(ok, value):
            if token is not self._bg_token:
                return
            composed = value[0] if ok else theme.compose(None, W, H, entry, kind)
            if not ok:
                self.art.errors.append(f'Artwork could not be composed ({value}); showing a plain background.')
            elif not value[1]:
                self._report_art_problem()
            self._show_background(composed, fade and ok and value[1] and not self.reduced_motion)
            self._bg_key = key
        self.run_bg(work, done)

    def _report_art_problem(self):
        if not getattr(self, '_art_reported', False) and self.art.errors:
            self._art_reported = True
            print('[launcher] ' + self.art.errors[-1], file=sys.stderr)
            if not self.status.get():
                self.set_status('Launcher artwork is unavailable; using a plain background. Details are under Advanced.',
                                'warn')

    def _show_background(self, composed, fade):
        from PIL import ImageTk
        if self._fade_after:
            self.root.after_cancel(self._fade_after)
            self._fade_after = None
        previous = self._bg_image
        self._bg_image = composed
        self.canvas.itemconfigure(self.fallback_shade, state='hidden')
        self.canvas.itemconfigure(self.fallback_band, state='hidden')
        if not fade or previous is None or previous.size != composed.size:
            self._bg_photo = ImageTk.PhotoImage(composed, master=self.root)
            self.canvas.itemconfigure(self.bg_item, image=self._bg_photo)
            self._layout()
            return
        steps = 5
        frames = [theme.blend(previous, composed, (i + 1) / steps) for i in range(steps)]

        def step(i=0):
            self._bg_photo = ImageTk.PhotoImage(frames[i], master=self.root)
            self.canvas.itemconfigure(self.bg_item, image=self._bg_photo)
            self._fade_after = self.root.after(theme.FADE_MS // steps, step, i + 1) if i + 1 < steps else None
        step()

    def _native_background(self, W, H, kind):
        """Without Pillow: Tk's own PNG decoder at native scale, right-aligned, beside an opaque ink panel."""
        c = self.canvas
        self._bg_photo = None
        if kind == 'home':
            path = self.art.path(self.theme)
            try:
                self._native_photo = self.tk.PhotoImage(file=str(path), master=self.root) if path else None
            except self.tk.TclError as e:
                self.art.errors.append(f'Artwork {path} could not be loaded ({e}); showing a plain background.')
                self._native_photo = None
                self._report_art_problem()
            c.itemconfigure(self.bg_item, image=self._native_photo or '', anchor='ne')
            c.coords(self.bg_item, W, 0)
            c.coords(self.fallback_shade, 0, 0, round(W * 0.47), H)
            c.coords(self.fallback_band, 0, H - self.px(52), W, H)
            for shade in (self.fallback_band, self.fallback_shade):
                c.itemconfigure(shade, state='normal')
                c.tag_lower(shade)
            c.tag_lower(self.bg_item)
        else:
            c.itemconfigure(self.bg_item, image='')
            c.itemconfigure(self.fallback_shade, state='hidden')
            c.itemconfigure(self.fallback_band, state='hidden')

    def apply_theme(self, theme_id, persist=True):
        """Appearance only: never touches game, save or graphics settings."""
        if theme_id not in THEMES:
            return
        changed = theme_id != self.theme
        self.theme = theme_id
        self.config['appearance']['theme'] = theme_id
        for tid, b in self.thumb_buttons.items():
            b.state(['selected'] if tid == theme_id else ['!selected'])
        if hasattr(self, 'v_theme') and self.v_theme.get() != theme_id:
            self.v_theme.set(theme_id)
        self._preview_scene_text(theme_id)
        if persist and changed:
            self._persist_appearance()
        if changed:
            self._bg_key = None
            self.render_background(fade=True)

    def _persist_appearance(self):
        if self.read_only:
            self.set_status('Appearance changed for this session only: the settings file belongs to a newer launcher.',
                            'warn')
            return
        saved = copy.deepcopy(self.saved)
        saved['appearance']['theme'] = self.theme
        try:
            save_config(self.config_path, saved)
        except (ValueError, OSError) as e:
            self.set_status(f'Appearance not saved: {e}', 'bad')
            return
        self.saved = saved
        self.stored_schema = SCHEMA_VERSION
        self.validate_settings()

    def set_status(self, text, kind=None):
        self.status.set(text)
        self.status_label.configure(style={'ok': 'Ok.TLabel', 'bad': 'Bad.TLabel', 'warn': 'Warn.TLabel'}.get(kind, 'TLabel'))
        self._schedule_layout()

    def update_home(self):
        missing = [(k, s) for k, s in ((k, self.setup_state.get(k)) for k in SETUP_ORDER) if not s or s[0] == 'bad']
        selected_quality = self.config['graphics']['texture_quality']
        texture_reason = texture_availability(self.config, self.caps).get(selected_quality)
        if any(self.setup_state.get(k) is None for k in SETUP_ORDER):
            self.home_state.configure(text='Checking setup…', style='Heading.TLabel')
        elif missing:
            self.home_state.configure(text=f'Setup needs attention ({len(missing)})', style='Bad.TLabel')
        elif texture_reason:
            self.home_state.configure(text='Texture pack unavailable', style='Bad.TLabel')
        else:
            self.home_state.configure(text='Ready to play', style='Ok.TLabel')
        self.home_items.configure(text=texture_reason or self.home_summary(missing), style='TLabel')
        caps = self.caps
        d = self.collect_display_quiet()
        what = 'display update features available' if caps.hud_scale or caps.camera_output or caps.monitor_select \
            else 'accepted feature set'
        size = f'{d["width"]} × {d["height"]}, internal {d["render_scale"]}×' if d else ''
        self.home_build.configure(text=f'Build: {what}. Output {size}. ' + ('Settings are read only this session. ' if self.read_only else ''))
        running = self.child is not None and self.child.poll() is None
        self.play_button.state(['disabled'] if running or missing or texture_reason else ['!disabled'])
        self._schedule_layout()

    @staticmethod
    def home_summary(missing):
        """Compact home text; the full explanations stay on the Build / Setup page."""
        if not missing:
            return 'Disc backup, EBOOT.elf, firmware, save profile and build are in place.'

        def short(text, limit=90):
            text = text.split('. ')[0].rstrip('.')
            return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'
        if len(missing) <= 2:
            lines = [f'•  {PATH_LABELS[k]}: {short(s[1]) if s else "checking…"}' for k, s in missing]
        else:
            short_names = {'disc': 'disc backup', 'elf': 'EBOOT.elf', 'dev_flash': 'firmware',
                           'hdd0': 'save profile', 'binary': 'build'}
            lines = ['Needs: ' + ', '.join(short_names[k] for k, _ in missing) + '.']
        return '\n'.join(lines + ['Open Build / Setup for details.'])

    # ------------------------------------------------------------------ setup
    def _build_setup(self):
        ttk, tk = self.ttk, self.tk
        f = self.page('setup', 'Build / Setup')
        rows = ttk.Frame(f)
        rows.grid(row=1, column=0, sticky='ew')
        rows.columnconfigure(2, weight=1)
        self.path_vars, self.path_icons, self.path_msgs = {}, {}, {}
        for r, key in enumerate(SETUP_ORDER):
            base = r * 2
            icon = ttk.Label(rows, text='…', width=2, style='Heading.TLabel')
            icon.grid(row=base, column=0, sticky='nw', padx=(0, self.px(6)))
            ttk.Label(rows, text=PATH_LABELS[key]).grid(row=base, column=1, sticky='w', padx=(0, self.px(10)))
            var = tk.StringVar()
            self.path_vars[key] = var
            ttk.Entry(rows, textvariable=var).grid(row=base, column=2, sticky='ew')
            actions = ttk.Frame(rows)
            actions.grid(row=base, column=3, sticky='w', padx=(self.px(6), 0))
            ttk.Button(actions, text='Browse…', command=lambda k=key: self.browse(k)).pack(side='left')
            if key == 'hdd0':
                ttk.Button(actions, text='New…', command=self.new_profile).pack(side='left', padx=(self.px(4), 0))
                ttk.Button(actions, text='Copy…', command=self.copy_profile).pack(side='left', padx=(self.px(4), 0))
            msg = ttk.Label(rows, text='', style='Muted.TLabel', wraplength=self.px(640), justify='left')
            msg.grid(row=base + 1, column=1, columnspan=3, sticky='w', pady=(0, self.px(8)))
            self.path_icons[key], self.path_msgs[key] = icon, msg
            var.trace_add('write', lambda *_: self._schedule_setup_check())
        tools = ttk.Frame(f)
        tools.grid(row=2, column=0, sticky='ew', pady=(0, self.px(10)))
        ttk.Button(tools, text='Detect existing files', command=self.detect).pack(side='left')
        self.setup_save = ttk.Button(tools, text='Save setup', command=self.save)
        self.setup_save.pack(side='left', padx=self.px(8))
        ttk.Label(tools, text='Nothing is downloaded or bundled: every input comes from your own backup and firmware.',
                  style='Muted.TLabel').pack(side='left', padx=self.px(8))
        build = ttk.LabelFrame(f, text='Build from your backup', padding=self.px(12))
        build.grid(row=3, column=0, sticky='ew')
        build.columnconfigure(0, weight=1)
        self.build_info = ttk.Label(build, text='', style='Muted.TLabel', wraplength=self.px(800), justify='left')
        self.build_info.grid(row=0, column=0, columnspan=2, sticky='w')
        self.build_bar = ttk.Progressbar(build, mode='determinate', maximum=4)
        self.build_bar.grid(row=1, column=0, columnspan=2, sticky='ew', pady=self.px(8))
        self.build_step = ttk.Label(build, text='Not running.')
        self.build_step.grid(row=2, column=0, columnspan=2, sticky='w')
        self.build_detail = ttk.Label(build, text='', style='Muted.TLabel', wraplength=self.px(800), justify='left')
        self.build_detail.grid(row=3, column=0, columnspan=2, sticky='w')
        bb = ttk.Frame(build)
        bb.grid(row=4, column=0, columnspan=2, sticky='w', pady=(self.px(8), 0))
        self.build_buttons = {
            'build': ttk.Button(bb, text='Build', command=lambda: self.start_build(False)),
            'verify': ttk.Button(bb, text='Verify generated code only', command=lambda: self.start_build(True)),
            'cancel': ttk.Button(bb, text='Cancel build', command=self.cancel_build),
            'use': ttk.Button(bb, text='Use the new build', command=self.use_new_build)}
        for b in self.build_buttons.values():
            b.pack(side='left', padx=(0, self.px(8)))
        self.build_buttons['cancel'].state(['disabled'])
        self.build_buttons['use'].state(['disabled'])

    def browse(self, key):
        from tkinter import filedialog
        start = self.path_vars[key].get().strip()
        initial = str(Path(start).parent if key in ('binary', 'elf') else Path(start)) if start else None
        if key in ('binary', 'elf'):
            types = [('Executable', '*.exe')] if key == 'binary' else [('Decrypted ELF', '*.elf'), ('All files', '*.*')]
            chosen = filedialog.askopenfilename(parent=self.root, initialdir=initial, filetypes=types)
        else:
            chosen = filedialog.askdirectory(parent=self.root, initialdir=initial, mustexist=True)
        if chosen:
            self.path_vars[key].set(str(Path(chosen)))

    def new_profile(self):
        from tkinter import filedialog
        parent = filedialog.askdirectory(parent=self.root, title='Choose an empty folder for the new save profile')
        if not parent:
            return
        try:
            hdd0 = create_profile(Path(parent))
        except (ValueError, OSError) as e:
            self.messagebox.showerror('Cannot create profile', str(e), parent=self.root)
            return
        self.path_vars['hdd0'].set(str(hdd0))

    def copy_profile(self):
        from tkinter import filedialog
        source = filedialog.askdirectory(parent=self.root, title='Choose the dev_hdd0 folder to copy (it is only read)')
        if not source:
            return
        parent = filedialog.askdirectory(parent=self.root, title='Choose an empty folder for the copy')
        if not parent:
            return
        self.set_status('Copying the profile…')
        self.run_bg(copy_profile, self._copied, Path(source), Path(parent))

    def _copied(self, ok, value):
        if ok:
            self.path_vars['hdd0'].set(str(value))
            self.set_status(f'Profile copied to {value}. The original was not changed.', 'ok')
        else:
            self.set_status(f'Profile not copied: {value}', 'bad')

    def detect(self):
        found = candidate_paths(self.collect_quiet(), self.recipe)
        for k, v in found.items():
            self.path_vars[k].set(v)
        self.set_status('Detected: ' + ', '.join(PATH_LABELS[k] for k in found) if found else
                        'Nothing new detected. Fill the missing items with Browse.', 'ok' if found else None)

    def _schedule_setup_check(self):
        if getattr(self, '_setup_after', None):
            self.root.after_cancel(self._setup_after)
        self._setup_after = self.root.after(400, self.check_setup)

    def check_setup(self):
        self._setup_after = None
        paths = {k: v.get() for k, v in self.path_vars.items()}
        self._setup_token = token = object()
        for k in SETUP_ORDER:
            self.setup_state[k] = None
            self.path_icons[k].configure(text='…', style='Heading.TLabel')

        def work():
            return {k: check_setup_item(k, paths) for k in SETUP_ORDER}

        def done(ok, result):
            if token is not self._setup_token:
                return
            if not ok:
                result = {k: ('bad', str(result)) for k in SETUP_ORDER}
            hdd0 = paths['hdd0'].strip()
            if result['hdd0'][0] == 'ok':
                try:
                    check_profile_length(hdd0, self.caps)
                except ValueError as e:
                    result['hdd0'] = ('bad', str(e))
            for k, (state, message) in result.items():
                self.setup_state[k] = (state, message)
                self.path_icons[k].configure(text={'ok': '✓', 'warn': '!', 'bad': '✕'}[state],
                                             style={'ok': 'Ok.TLabel', 'warn': 'Warn.TLabel', 'bad': 'Bad.TLabel'}[state])
                self.path_msgs[k].configure(text=message)
            self.describe_build()
            self.update_home()
        self.run_bg(work, done)
        if self.caps_for != paths['binary'].strip():
            self.refresh_capabilities()
        self.update_build_info()

    # ------------------------------------------------------------------ build
    def update_build_info(self):
        output = self.build_vars['output'].get().strip() if hasattr(self, 'build_vars') else ''
        if not output:
            text = 'Set the builder inputs under Advanced > Build inputs (or start with --build-recipe).'
        else:
            state = read_checkpoint(output) if Path(output).exists() else None
            if state:
                text = (f'Output {output}: last state "{state.get("state")}". Build resumes only if every recorded '
                        'input still matches; otherwise the builder refuses and explains why.')
            elif Path(output).exists():
                text = f'Output {output} exists without a builder checkpoint; the builder will refuse it. Choose a new folder.'
            else:
                text = f'Fresh build into {output} (must be on D:). Regeneration and compiling take a few minutes.'
        self.build_info.configure(text=text)

    def start_build(self, prepare_only):
        if self.build_run is not None:
            return
        try:
            config = self.collect()
        except ValueError as e:
            self.build_detail.configure(text=str(e), style='Bad.TLabel')
            return
        output = config['build']['output']
        binary = config['paths']['binary']
        if output and binary and not prepare_only and Path(output).exists() and overlaps(binary, output):
            if not self.messagebox.askyesno('Rebuild in place?', 'This output folder holds the build you play with. '
                                            'The builder will recompile it in place if its inputs still match. Continue?',
                                            parent=self.root):
                return
        try:
            self.build_run = BuildRun(config, prepare_only)
        except (ValueError, OSError) as e:
            self.build_step.configure(text='Build not started.', style='Bad.TLabel')
            self.build_detail.configure(text=str(e), style='Bad.TLabel')
            return
        self.build_bar.configure(maximum=max(1, len(self.build_run.steps)), value=0)
        self.build_step.configure(text='Starting the builder…', style='TLabel')
        self.build_detail.configure(text=f'Running {self.build_run.cmd[1]}' + (' --resume' if self.build_run.resume else ''),
                                    style='Muted.TLabel')
        for k in ('build', 'verify', 'use'):
            self.build_buttons[k].state(['disabled'])
        self.build_buttons['cancel'].state(['!disabled'])

    def _watch_build(self):
        run = self.build_run
        if run is None:
            return
        self._build_tick = getattr(self, '_build_tick', 0) + 1
        result = run.poll()
        if result is None:
            if self._build_tick % 4 == 0:
                p = run.progress()
                self.build_bar.configure(value=p['done'])
                step = f'Step {min(p["done"] + 1, p["total"])} of {p["total"]}: ' if p['total'] else ''
                self.build_step.configure(text=f'{step}{p["label"]} ({int(p["elapsed"])} s)', style='TLabel')
                if p['detail']:
                    self.build_detail.configure(text=p['detail'], style='Muted.TLabel')
            return
        self.build_run = None
        self.last_build = result
        self.build_buttons['cancel'].state(['disabled'])
        for k in ('build', 'verify'):
            self.build_buttons[k].state(['!disabled'])
        ok = result['exit_code'] == 0 and not result['cancelled']
        self.build_bar.configure(value=self.build_bar.cget('maximum') if ok else run.progress()['done'])
        self.build_step.configure(text='Finished.' if ok else ('Cancelled.' if result['cancelled'] else 'Failed.'),
                                  style='Ok.TLabel' if ok else 'Bad.TLabel')
        self.build_detail.configure(text=result['message'], style='TLabel' if ok else 'Bad.TLabel')
        if ok and result.get('summary', {}).get('binary'):
            self.build_buttons['use'].state(['!disabled'])
        self.update_build_info()

    def cancel_build(self):
        if self.build_run is not None:
            self.build_step.configure(text='Stopping the builder…')
            self.build_run.cancel()

    def use_new_build(self):
        binary = (self.last_build or {}).get('summary', {}).get('binary')
        if binary:
            self.path_vars['binary'].set(binary)
            self.set_status('Selected the new build. Save setup to keep it.', 'ok')

    # ------------------------------------------------------------------ settings
    def _build_settings(self):
        ttk, tk = self.ttk, self.tk
        f = self.page('settings', 'Settings')
        f.rowconfigure(1, weight=1)
        nb = ttk.Notebook(f)
        nb.grid(row=1, column=0, sticky='nsew')
        self.settings_tabs = nb
        disp = ttk.Frame(nb, padding=self.px(14))
        gfx = ttk.Frame(nb, padding=self.px(14))
        na = ttk.Frame(nb, padding=self.px(14))
        appearance = ttk.Frame(nb, padding=self.px(14))
        nb.add(disp, text='Display')
        nb.add(gfx, text='Graphics')
        nb.add(na, text='Not available yet')
        nb.add(appearance, text='Appearance')
        self.appearance_tab = appearance
        self._build_appearance(appearance)
        disp.columnconfigure(1, weight=1)
        self.v = {k: tk.StringVar() for k in ('monitor', 'window_mode', 'preset', 'width', 'height', 'render_scale',
                                              'camera_aspect', 'hud_scale', 'frame_rate', 'texture_quality',
                                              'pack_root', *GRAPHICS_CHOICES)}
        hints = {}
        row = 0

        def label(text, r):
            ttk.Label(disp, text=text).grid(row=r, column=0, sticky='nw', padx=(0, self.px(14)), pady=(self.px(6), 0))

        def hint(key, r):
            hints[key] = ttk.Label(disp, text='', style='Muted.TLabel', wraplength=self.px(560), justify='left')
            hints[key].grid(row=r, column=1, sticky='w', pady=(0, self.px(4)))

        label('Monitor', row)
        self.monitor_box = ttk.Combobox(disp, textvariable=self.v['monitor'], state='readonly', width=48,
                                        values=[m.label() for m in self.monitors] or ['Display 1'])
        self.monitor_box.grid(row=row, column=1, sticky='w', pady=(self.px(6), 0))
        hint('monitor', row + 1)
        row += 2
        label('Window', row)
        wm = ttk.Frame(disp)
        wm.grid(row=row, column=1, sticky='w', pady=(self.px(6), 0))
        self.window_radios = [ttk.Radiobutton(wm, text=t, value=v, variable=self.v['window_mode'])
                              for v, t in (('windowed', 'Windowed'), ('fullscreen', 'Borderless fullscreen'))]
        for b in self.window_radios:
            b.pack(side='left', padx=(0, self.px(14)))
        hint('window_mode', row + 1)
        row += 2
        label('Output resolution', row)
        res = ttk.Frame(disp)
        res.grid(row=row, column=1, sticky='w', pady=(self.px(6), 0))
        self.preset_box = ttk.Combobox(res, textvariable=self.v['preset'], state='readonly', width=30)
        self.preset_box.pack(side='left')
        self.custom = ttk.Frame(res)
        self.custom.pack(side='left', padx=(self.px(10), 0))
        vcmd = (self.root.register(lambda s: s == '' or (s.isdigit() and len(s) <= 5)), '%P')
        ttk.Spinbox(self.custom, textvariable=self.v['width'], from_=1, to=MAX_DIMENSION, width=7,
                    validate='key', validatecommand=vcmd).pack(side='left')
        ttk.Label(self.custom, text=' × ').pack(side='left')
        ttk.Spinbox(self.custom, textvariable=self.v['height'], from_=1, to=MAX_DIMENSION, width=7,
                    validate='key', validatecommand=vcmd).pack(side='left')
        hint('output', row + 1)
        row += 2
        label('Internal render scale', row)
        sc = ttk.Frame(disp)
        sc.grid(row=row, column=1, sticky='w', pady=(self.px(6), 0))
        self.scale_radios = []
        for s in (1, 2, 3, 4):
            b = ttk.Radiobutton(sc, text=f'{s}×', value=str(s), variable=self.v['render_scale'])
            b.pack(side='left', padx=(0, self.px(12)))
            self.scale_radios.append(b)
        hint('render_scale', row + 1)
        row += 2
        label('Camera aspect', row)
        cam = ttk.Frame(disp)
        cam.grid(row=row, column=1, sticky='w', pady=(self.px(6), 0))
        self.camera_radios = {v: ttk.Radiobutton(cam, text=t, value=v, variable=self.v['camera_aspect'])
                              for v, t in (('original', 'Original 16:9 (default)'),
                                           ('output', 'Match output aspect (experimental)'))}
        for b in self.camera_radios.values():
            b.pack(side='left', padx=(0, self.px(14)))
        hint('camera_aspect', row + 1)
        row += 2
        label('HUD size (%)', row)
        hud = ttk.Frame(disp)
        hud.grid(row=row, column=1, sticky='w', pady=(self.px(6), 0))
        self.hud_spin = ttk.Spinbox(hud, textvariable=self.v['hud_scale'], from_=HUD_LIMITS[0], to=HUD_LIMITS[1],
                                    increment=5, width=5, validate='key', validatecommand=vcmd)
        self.hud_spin.pack(side='left')
        self.hud_buttons = [ttk.Button(hud, text=f'{p}%', width=6, command=lambda p=p: self.v['hud_scale'].set(str(p)))
                            for p in (100, 75)]
        for b in self.hud_buttons:
            b.pack(side='left', padx=(self.px(8), 0))
        hint('hud_scale', row + 1)
        row += 2
        ttk.Label(disp, text='Changes apply the next time the game starts. Resizing the game window fits the image '
                  'to it but does not add detail.', style='Muted.TLabel', wraplength=self.px(760),
                  justify='left').grid(row=row, column=0, columnspan=2, sticky='w', pady=(self.px(10), 0))
        self.hints = hints

        gfx.columnconfigure(1, weight=1)
        self.graphics_boxes, self.graphics_hints = {}, {}
        for r, key in enumerate(GRAPHICS_CHOICES):
            ttk.Label(gfx, text=GRAPHICS_TITLES[key]).grid(row=r * 2, column=0, sticky='w', padx=(0, self.px(14)),
                                                          pady=(self.px(6), 0))
            box = ttk.Combobox(gfx, state='readonly', width=26)
            box.grid(row=r * 2, column=1, sticky='w', pady=(self.px(6), 0))
            box.bind('<<ComboboxSelected>>', lambda _e, k=key: self._graphics_selected(k))
            self.graphics_boxes[key] = box
            self.graphics_hints[key] = ttk.Label(gfx, text=GRAPHICS_HELP[key], style='Muted.TLabel')
            self.graphics_hints[key].grid(row=r * 2 + 1, column=1, sticky='w')

        rate_row = len(GRAPHICS_CHOICES) * 2
        ttk.Label(gfx, text='Frame rate').grid(row=rate_row, column=0, sticky='w', padx=(0, self.px(14)))
        rates = ttk.Frame(gfx)
        rates.grid(row=rate_row, column=1, sticky='w')
        ttk.Radiobutton(rates, text='Original 30', value='30', variable=self.v['frame_rate']).pack(side='left')
        self.rate_60 = ttk.Radiobutton(rates, text='60 (experimental)', value='60', variable=self.v['frame_rate'])
        self.rate_60.pack(side='left', padx=(self.px(12), 0))
        self.rate_hint = ttk.Label(gfx, text='', style='Muted.TLabel', wraplength=self.px(520))
        self.rate_hint.grid(row=rate_row + 1, column=1, sticky='w')
        self.cache_hint = ttk.Label(gfx, text='', style='Muted.TLabel', wraplength=self.px(640))
        self.cache_hint.grid(row=rate_row + 2, column=0, columnspan=2, sticky='w', pady=(self.px(12), 0))

        ttk.Label(gfx, text='Texture quality').grid(row=rate_row + 3, column=0, sticky='w',
                                                    padx=(0, self.px(14)), pady=(self.px(12), 0))
        qualities = ttk.Frame(gfx)
        qualities.grid(row=rate_row + 3, column=1, sticky='w', pady=(self.px(12), 0))
        self.texture_radios = {}
        for quality, title in TEXTURE_LABELS.items():
            button = ttk.Radiobutton(qualities, text=title, value=quality, variable=self.v['texture_quality'],
                                     command=self.update_texture_controls)
            button.pack(side='left', padx=(0, self.px(10)))
            self.texture_radios[quality] = button
        ttk.Label(gfx, text='Texture pack root').grid(row=rate_row + 4, column=0, sticky='w',
                                                      padx=(0, self.px(14)), pady=(self.px(6), 0))
        pack_row = ttk.Frame(gfx)
        pack_row.grid(row=rate_row + 4, column=1, sticky='ew', pady=(self.px(6), 0))
        pack_row.columnconfigure(0, weight=1)
        ttk.Entry(pack_row, textvariable=self.v['pack_root'], width=52).grid(row=0, column=0, sticky='ew')
        ttk.Button(pack_row, text='Browse…', command=self.browse_texture_pack).grid(row=0, column=1,
                                                                                      padx=(self.px(6), 0))
        self.texture_hint = ttk.Label(gfx, text='', style='Muted.TLabel', wraplength=self.px(640))
        self.texture_hint.grid(row=rate_row + 5, column=0, columnspan=2, sticky='w', pady=(self.px(4), 0))

        na.columnconfigure(1, weight=1)
        ttk.Label(na, text='These are not offered because they are not working or not verified in this port.',
                  style='Muted.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, self.px(10)))
        for r, (name, reason) in enumerate(NOT_AVAILABLE, 1):
            ttk.Label(na, text=name, foreground='#7a7f86').grid(row=r, column=0, sticky='nw', padx=(0, self.px(16)),
                                                               pady=self.px(4))
            ttk.Label(na, text=reason, style='Muted.TLabel', wraplength=self.px(520),
                      justify='left').grid(row=r, column=1, sticky='w', pady=self.px(4))

        bar = ttk.Frame(f)
        bar.grid(row=2, column=0, sticky='ew', pady=(self.px(12), 0))
        bar.columnconfigure(0, weight=1)
        self.settings_msg = ttk.Label(bar, text='', wraplength=self.px(600), justify='left')
        self.settings_msg.grid(row=0, column=0, sticky='w')
        ttk.Button(bar, text='Revert', command=self.revert).grid(row=0, column=1, padx=self.px(8))
        self.settings_save = ttk.Button(bar, text='Save', command=self.save)
        self.settings_save.grid(row=0, column=2)
        for key in ('monitor', 'window_mode', 'render_scale', 'camera_aspect', 'hud_scale', 'frame_rate'):
            self.v[key].trace_add('write', lambda *_: self.validate_settings())
        for key in ('width', 'height'):
            self.v[key].trace_add('write', lambda *_: (self._sync_preset(), self.validate_settings()))
        self.v['texture_quality'].trace_add('write', lambda *_: self.validate_settings())
        self.v['pack_root'].trace_add('write', lambda *_: self.update_texture_controls())
        self.preset_box.bind('<<ComboboxSelected>>', lambda _e: self._preset_selected())
        self.monitor_box.bind('<<ComboboxSelected>>', lambda _e: self._monitor_selected())

    def _build_appearance(self, tab):
        ttk, tk = self.ttk, self.tk
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text='Launcher atmosphere', style='Heading.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(tab, text='Changes only how this launcher looks. It does not affect the game, saves or graphics.',
                  style='Muted.TLabel').grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, self.px(10)))
        self.v_theme = tk.StringVar(value=self.theme)
        self.theme_radios = {}
        for i, tid in enumerate(THEMES):
            r = ttk.Radiobutton(tab, value=tid, variable=self.v_theme, compound='left',
                                image=self.thumb_images.get(tid) or '',
                                text=f'  {self.art.name(tid)}\n  {self.art.description(tid)}',
                                command=lambda: self.apply_theme(self.v_theme.get()))
            r.grid(row=2 + i, column=0, sticky='w', pady=self.px(4))
            self.theme_radios[tid] = r
        self.companion_label = tk.Label(tab, background=theme.PANEL, borderwidth=0, highlightthickness=0)
        self.companion_label.grid(row=2, column=1, rowspan=4, sticky='e', padx=(self.px(20), 0))
        motion = ('Reduced motion is on: themes switch instantly.' if self.reduced_motion else
                  f'Theme changes cross-fade in {theme.FADE_MS} ms. Turn off Windows animation effects to switch instantly.')
        ttk.Label(tab, text=motion, style='Muted.TLabel').grid(row=6, column=0, columnspan=2, sticky='w',
                                                               pady=(self.px(12), 0))
        ttk.Label(tab, text=self.art.notice or 'Launcher artwork is unavailable.', style='Muted.TLabel',
                  wraplength=self.px(760), justify='left').grid(row=7, column=0, columnspan=2, sticky='w',
                                                                pady=(self.px(6), 0))
        self.settings_tabs.bind('<<NotebookTabChanged>>', lambda _e: self._show_companions())

    def _show_companions(self):
        """The transparent spirit group, scaled as one image on the opaque panel (loaded only when shown)."""
        if str(self.settings_tabs.select()) != str(self.appearance_tab) or self.companion_photo is not None:
            return
        try:
            if theme.HAVE_PIL:
                from PIL import ImageTk
                image = theme.companion_image(self.art.load('spirits'), self.px(330), self.px(220))
                self.companion_photo = ImageTk.PhotoImage(image, master=self.root) if image else None
            elif self.art.path('spirits'):
                self.companion_photo = self.tk.PhotoImage(file=str(self.art.path('spirits')),
                                                          master=self.root).subsample(4)
        except (OSError, ValueError, self.tk.TclError) as e:
            self.art.errors.append(f'Companion art could not be loaded ({e}).')
        self.companion_label.configure(image=self.companion_photo or '')

    def preset_items(self):
        items = []
        m = self.selected_monitor()
        if m:
            w, h = m.size
            items.append((f'Match {m.label().split(" · ")[0]} ({w} × {h})', w, h))
        items += [(f'{w} × {h} ({r})', w, h) for w, h, r in PRESETS]
        items.append(('Custom…', None, None))
        return items

    def selected_monitor(self):
        if not self.monitors:
            return None
        labels = [m.label() for m in self.monitors]
        value = self.v['monitor'].get()
        index = labels.index(value) if value in labels else 0
        return self.monitors[index] if self.caps.monitor_select else next((m for m in self.monitors if m.primary), self.monitors[0])

    def _monitor_selected(self):
        self._sync_preset()
        self.validate_settings()

    def _preset_selected(self):
        chosen = self.v['preset'].get()
        self.custom_explicit = chosen == 'Custom…'
        for name, w, h in self.preset_items():
            if name == chosen and w:
                self.v['width'].set(str(w))
                self.v['height'].set(str(h))
        self._sync_preset()

    def _sync_preset(self):
        """Show the preset matching the stored size, or Custom with editable dimensions."""
        items = self.preset_items()
        self.preset_box.configure(values=[i[0] for i in items])
        w, h = self.v['width'].get(), self.v['height'].get()
        match = next((name for name, pw, ph in items if pw and (str(pw), str(ph)) == (w, h)), None)
        # Custom stays selected only when the user picked it; otherwise follow the size.
        self.custom_mode = getattr(self, 'custom_explicit', False) or match is None
        self.v['preset'].set('Custom…' if self.custom_mode else match)
        for child in self.custom.winfo_children():
            if isinstance(child, self.ttk.Spinbox):
                child.state(['!disabled'] if self.custom_mode else ['disabled'])

    def _graphics_selected(self, key):
        labels = GRAPHICS_LABELS[key]
        value = next((v for v, t in labels.items() if t == self.graphics_boxes[key].get()), None)
        if value:
            self.v[key].set(value)
            if key in ('path_tracing', 'dlss') and value != 'off':
                self.v['ambient_occlusion'].set('off')
                self.graphics_boxes['ambient_occlusion'].set(AO_LABELS['off'])
            elif key == 'ambient_occlusion' and value != 'off':
                self.v['path_tracing'].set('off')
                self.graphics_boxes['path_tracing'].set(PATH_TRACING_LABELS['off'])
                self.v['dlss'].set('off')
                self.graphics_boxes['dlss'].set(DLSS_LABELS['off'])
            if key in ('path_tracing', 'ambient_occlusion'):
                self.graphics_boxes['path_strength'].state(
                    ['readonly', '!disabled'] if self.caps.native_settings_version >= 9 and
                    self.v['path_tracing'].get() == 'diffuse-preview' and
                    'diffuse-preview' in self.caps.graphics.get('path_tracing', ()) else ['disabled'])
        self.validate_settings()

    def browse_texture_pack(self):
        from tkinter import filedialog
        initial = self.v['pack_root'].get().strip() or str(Path.home())
        chosen = filedialog.askdirectory(parent=self.root, title='Choose texture pack root',
                                         initialdir=initial, mustexist=True)
        if chosen:
            self.v['pack_root'].set(str(Path(chosen)))

    def update_texture_controls(self):
        candidate = copy.deepcopy(self.config)
        candidate['graphics']['texture_quality'] = self.v['texture_quality'].get() or 'low'
        candidate['textures']['pack_root'] = self.v['pack_root'].get().strip()
        availability = texture_availability(candidate, self.caps)
        for quality, button in self.texture_radios.items():
            button.state(['!disabled'] if availability[quality] is None else ['disabled'])
        selected = candidate['graphics']['texture_quality']
        reason = availability.get(selected)
        if selected == 'low':
            message = 'Original game textures. Texture pack variables are cleared for this launch.'
        elif reason:
            message = f'{TEXTURE_LABELS.get(selected, selected)} unavailable: {reason} Selection remains saved; Play will refuse it.'
        else:
            message = f'{TEXTURE_LABELS[selected]} ready. Pack choice applies on the next launch.'
        self.texture_hint.configure(text=message)
        self.validate_settings()

    def apply_capabilities(self):
        """Enable only controls the selected build accepts; keep stored values untouched."""
        caps = self.caps
        h = self.hints
        self.rate_60.state(['!disabled'] if caps.frame_rate_60 else ['disabled'])
        self.rate_hint.configure(text='60 FPS is experimental. Game speed, cloth and all-scene stability are not yet verified.'
                                 if caps.frame_rate_60 else 'This build uses original 30 FPS timing; a recorded frame bridge is required for 60.')
        self.cache_hint.configure(text='Compiled shaders are retained in this save profile\'s shader-cache folder.'
                                  if caps.shader_cache else 'This build has no recorded launcher shader-cache support.')
        self.monitor_box.state(['readonly', '!disabled'] if caps.monitor_select and self.monitors else ['disabled'])
        h['monitor'].configure(text='The game window opens on this monitor.' if caps.monitor_select else
                               'This build always opens on the primary display. Monitor selection comes with the display update.')
        for b in self.window_radios:
            b.state(['!disabled'] if caps.fullscreen else ['disabled'])
        h['window_mode'].configure(text='Alt+Enter switches between windowed and fullscreen while playing.' if caps.fullscreen
                                   else 'This build opens a window. Borderless fullscreen and Alt+Enter come with the display update.')
        low, high = caps.render_scale
        for s, b in enumerate(self.scale_radios, 1):
            b.state(['!disabled'] if low <= s <= high else ['disabled'])
        cam_flag = bool(caps.camera_aspects)
        self.camera_radios['original'].state(['!disabled'] if cam_flag else ['disabled'])
        self.camera_radios['output'].state(['!disabled'] if caps.camera_output else ['disabled'])
        hud_ok = bool(caps.hud_scale)
        self.hud_spin.state(['!disabled'] if hud_ok else ['disabled'])
        if hud_ok:
            self.hud_spin.configure(from_=caps.hud_scale[0], to=caps.hud_scale[1])
        for b in self.hud_buttons:
            b.state(['!disabled'] if hud_ok else ['disabled'])
        for key, box in self.graphics_boxes.items():
            allowed = caps.graphics.get(key, ())
            box.configure(values=[GRAPHICS_LABELS[key][v] for v in allowed])
            if key == 'ray_denoiser':
                box.state(['readonly', '!disabled'] if caps.native_settings_version >= 8 and
                          ('diffuse-preview' in caps.graphics.get('path_tracing', ()) or
                           'rayquery' in caps.graphics.get('ambient_occlusion', ())) else ['disabled'])
            elif key == 'path_strength':
                box.state(['readonly', '!disabled'] if caps.native_settings_version >= 9 and
                          'diffuse-preview' in caps.graphics.get('path_tracing', ()) and
                          self.v['path_tracing'].get() == 'diffuse-preview' else ['disabled'])
            elif key in ('dlss', 'sharpen'):
                box.state(['readonly', '!disabled'] if len(allowed) > 1 else ['disabled'])
            current = self.v[key].get()
            box.set(GRAPHICS_LABELS[key].get(current, current) + ('' if current in allowed else ' (not supported by this build)'))
        self.update_texture_controls()
        self._sync_preset()
        self.validate_settings()

    def collect_display(self):
        d = copy.deepcopy(self.config['display'])
        labels = [m.label() for m in self.monitors]
        if self.v['monitor'].get() in labels:
            d['monitor'] = labels.index(self.v['monitor'].get())
        d['window_mode'] = self.v['window_mode'].get()
        d['width'] = parse_dimension(self.v['width'].get(), 'Output width')
        d['height'] = parse_dimension(self.v['height'].get(), 'Output height')
        d['render_scale'] = int(self.v['render_scale'].get())
        d['frame_rate'] = int(self.v['frame_rate'].get())
        d['camera_aspect'] = self.v['camera_aspect'].get()
        text = self.v['hud_scale'].get().strip()
        if not text.isdigit():
            raise ValueError('HUD size must be a whole number from 50 to 100.')
        d['hud_scale'] = int(text)
        return d

    def collect_display_quiet(self):
        try:
            return self.collect_display()
        except (ValueError, AttributeError):
            return None

    def collect(self):
        c = copy.deepcopy(self.config)
        c['paths'] = {k: self.path_vars[k].get().strip() for k in PATH_NAMES}
        c['build'] = {k: self.build_vars[k].get().strip() for k in BUILD_NAMES}
        c['display'] = self.collect_display()
        for key in GRAPHICS_CHOICES:
            c['graphics'][key] = self.v[key].get()
        c['graphics']['texture_quality'] = self.v['texture_quality'].get()
        c['textures']['pack_root'] = self.v['pack_root'].get().strip()
        return validate_config(c)

    def collect_quiet(self):
        try:
            return self.collect()
        except ValueError:
            c = copy.deepcopy(self.config)
            c['paths'] = {k: self.path_vars[k].get().strip() for k in PATH_NAMES}
            return c

    def validate_settings(self):
        h, caps = self.hints, self.caps
        try:
            d = self.collect_display()
            c = copy.deepcopy(self.config)
            c['display'] = d
            for key in GRAPHICS_CHOICES:
                c['graphics'][key] = self.v[key].get()
            c['graphics']['texture_quality'] = self.v['texture_quality'].get()
            c['textures']['pack_root'] = self.v['pack_root'].get().strip()
            validate_config(c)
            player_arguments(c, caps)
            error = None
        except ValueError as e:
            d, error = self.collect_display_quiet(), str(e)
        if d:
            w, hgt, s = d['width'], d['height'], d['render_scale']
            notes = [f'{w} × {hgt} · {ratio_text(w, hgt)} · {w * hgt / 1e6:.1f} MP of {MAX_PIXELS / 1e6:.1f} MP allowed.']
            m = self.selected_monitor()
            if m and not caps.monitor_select:
                scale = m.dpi / 96
                if w * scale > m.work[2] - m.work[0] or hgt * scale > m.work[3] - m.work[1]:
                    notes.append(f'This build opens a {round(w * scale)} × {round(hgt * scale)} window at '
                                 f'{m.scale_percent}% scaling, larger than the primary display; part of it will be off-screen.')
            elif caps.monitor_select:
                notes.append('The window is fitted to the selected monitor; the output keeps this size.')
            h['output'].configure(text=' '.join(notes))
            h['render_scale'].configure(text=f'{s}× renders the game\'s 1280 × 720 targets at {1280 * s} × {720 * s}'
                                        + (' (original).' if s == 1 else '. Higher scales cost performance.')
                                        + (' Use FSR 1 at 1× to reach a large output cheaply.' if s > 1 and d['width'] > 2560 else ''))
            ratio = w / hgt
            if not caps.camera_aspects:
                cam = 'Original camera. Match output aspect needs a build with the display update.'
            elif not caps.camera_output:
                cam = 'Original camera. This build was compiled without the guarded camera bridge.'
            elif d['camera_aspect'] == 'output' and ratio <= MIN_CAMERA_RATIO + 1e-9:
                cam = 'No effect at 16:9 or narrower: the original camera is kept.'
            else:
                cam = ('Experimental: verified framing in one Faerypool scene at 3440 × 1440 only. Other cameras, areas and '
                       'cinematics are not qualified; switch back to Original if something looks wrong. Up to 32:9.')
            h['camera_aspect'].configure(text=cam)
            stored = self.config['display']
            hud_note = (f'Scales the gameplay HUD around its corners ({caps.hud_scale[0]}–{caps.hud_scale[1]}%). '
                        'Menus keep their size.' if caps.hud_scale else
                        'Needs a build with the display update.' + (f' Your saved {stored["hud_scale"]}% is kept for such a build.'
                                                                     if stored['hud_scale'] != 100 else ''))
            h['hud_scale'].configure(text=hud_note)
        dirty = self.is_dirty()
        if error:
            self.settings_msg.configure(text=error, style='Bad.TLabel')
        elif self.read_only:
            self.settings_msg.configure(text='Read only: this settings file belongs to a newer launcher.', style='Warn.TLabel')
        elif dirty or self.stored_schema not in (None, SCHEMA_VERSION):
            self.settings_msg.configure(text='Unsaved changes.' if dirty else
                                        f'Loaded from launcher schema {self.stored_schema}; Save upgrades the file.',
                                        style='Warn.TLabel')
        else:
            self.settings_msg.configure(text='Saved.' if self.stored_schema else 'Not saved yet.', style='Muted.TLabel')
        enabled = not error and not self.read_only
        for b in (self.settings_save, self.setup_save):
            b.state(['!disabled'] if enabled else ['disabled'])
        return error

    def is_dirty(self):
        try:
            return self.collect() != self.saved
        except (ValueError, AttributeError):
            return True

    def load_into_ui(self, config):
        d, g = config['display'], config['graphics']
        for k in PATH_NAMES:
            self.path_vars[k].set(config['paths'][k])
        for k in BUILD_NAMES:
            self.build_vars[k].set(config['build'][k])
        if self.monitors:
            self.v['monitor'].set(self.monitors[min(d['monitor'], len(self.monitors) - 1)].label())
        for k in ('window_mode', 'camera_aspect'):
            self.v[k].set(d[k])
        for k in ('width', 'height', 'render_scale', 'hud_scale', 'frame_rate'):
            self.v[k].set(str(d[k]))
        for k in GRAPHICS_CHOICES:
            self.v[k].set(g[k])
        self.v['texture_quality'].set(g['texture_quality'])
        self.v['pack_root'].set(config['textures']['pack_root'])
        self.custom_explicit = False
        self.apply_theme(config['appearance']['theme'], persist=False)
        self.apply_capabilities()

    def revert(self):
        self.config = copy.deepcopy(self.saved)
        self.load_into_ui(self.config)

    def save(self):
        if self.read_only:
            self.set_status('Not saved: the settings file belongs to a newer launcher.', 'warn')
            return False
        try:
            config = self.collect()
            backup = save_config(self.config_path, config)
        except (ValueError, OSError) as e:
            self.messagebox.showerror('Cannot save', str(e), parent=self.root)
            return False
        self.config, self.saved = config, copy.deepcopy(config)
        self.stored_schema = SCHEMA_VERSION
        self.set_status('Settings saved.' + (f' The previous file was kept as {backup.name}.' if backup else ''), 'ok')
        self.validate_settings()
        self.update_home()
        return True

    # ------------------------------------------------------------------ capabilities
    def refresh_capabilities(self):
        binary = self.path_vars['binary'].get().strip()
        self.caps_for = binary

        def done(ok, caps):
            if binary != self.caps_for:
                return
            limit_changed = (caps if ok else ACCEPTED).profile_path_max != self.caps.profile_path_max
            self.caps = caps if ok else ACCEPTED
            self.apply_capabilities()
            if limit_changed:
                self.check_setup()
            self.describe_build()
            self.update_home()
            if self.current_page == 'advanced':
                self.refresh_diagnostics()
        self.run_bg(discover_capabilities, done, binary)

    def describe_build(self):
        state = self.setup_state.get('binary')
        if not state or state[0] != 'ok' or self.caps_for != self.path_vars['binary'].get().strip():
            return
        caps = self.caps
        if caps.origin == 'build-record':
            extra = [n for n, on in (('HUD size', caps.hud_scale), ('camera aspect', caps.camera_output),
                                     ('monitor selection', caps.monitor_select), ('fullscreen', caps.fullscreen)) if on]
            text = 'Executable found. Capabilities recorded by its build' + (f': {", ".join(extra)}.' if extra else '.')
        else:
            text = 'Executable found. ' + caps.detail
        self.path_msgs['binary'].configure(text=text)

    # ------------------------------------------------------------------ play
    def play(self):
        if self.child is not None and self.child.poll() is None:
            self.set_status('The game started from this window is still running.', 'warn')
            return
        try:
            config = self.collect()
        except ValueError as e:
            self.set_status(f'Cannot start: {e}', 'bad')
            self.show('settings')
            return
        self.play_button.state(['disabled'])
        self.set_status('Checking your backup and starting the game…')
        caps = self.caps
        self.run_bg(launch, lambda ok, value: self._launched(ok, value, config), config, caps)

    def _launched(self, ok, value, config):
        if not ok:
            self.set_status(f'Cannot start: {value}', 'bad')
            self.update_home()
            return
        self.child = value
        self.release_decorations()
        if not self.read_only and config != self.saved:
            try:
                save_config(self.config_path, config)
                self.config, self.saved = config, copy.deepcopy(config)
                self.stored_schema = SCHEMA_VERSION
            except (ValueError, OSError) as e:
                self.set_status(f'Game started, but settings were not saved: {e}', 'warn')
                return
        self.set_status(f'Game running (PID {value.pid}). Its log is launcher-game.log in the profile folder.', 'ok')
        self.update_home()

    def release_decorations(self):
        """The game is running: stop any fade and drop decoded paintings (the shown frame stays)."""
        if self._fade_after:
            self.root.after_cancel(self._fade_after)
            self._fade_after = None
        self.art.release()
        self._bg_image = None

    def _watch_child(self):
        if self.child is None or self.child.poll() is None:
            return
        code, self.child = self.child.returncode, None
        if code in GAME_EXIT_OK:
            self.set_status('Game closed.')
        else:
            self.set_status(f'The game exited with code {code}. Advanced shows the end of its log.', 'bad')
        self.update_home()

    # ------------------------------------------------------------------ advanced
    def _build_advanced(self):
        ttk, tk = self.ttk, self.tk
        f = self.page('advanced', 'Advanced')
        f.rowconfigure(1, weight=1)
        nb = ttk.Notebook(f)
        nb.grid(row=1, column=0, sticky='nsew')
        diag = ttk.Frame(nb, padding=self.px(10))
        inputs = ttk.Frame(nb, padding=self.px(10))
        nb.add(diag, text='Diagnostics')
        nb.add(inputs, text='Build inputs')
        diag.columnconfigure(0, weight=1)
        diag.rowconfigure(0, weight=1)
        self.diag_text = tk.Text(diag, wrap='word', height=18, font=('Consolas', 9), relief='flat', borderwidth=0,
                                 background=theme.FIELD, foreground=theme.IVORY, insertbackground=theme.IVORY,
                                 selectbackground=theme.GOLD, selectforeground=theme.INK, padx=self.px(8),
                                 pady=self.px(6), highlightthickness=1, highlightbackground=theme.RULE,
                                 highlightcolor=theme.GOLD_LIGHT)
        self.diag_text.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(diag, command=self.diag_text.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.diag_text.configure(yscrollcommand=scroll.set, state='disabled')
        bar = ttk.Frame(diag)
        bar.grid(row=1, column=0, columnspan=2, sticky='w', pady=(self.px(8), 0))
        ttk.Button(bar, text='Refresh', command=self.refresh_diagnostics).pack(side='left')
        ttk.Button(bar, text='Open profile folder', command=lambda: self.open_path('profile')).pack(side='left', padx=self.px(6))
        ttk.Button(bar, text='Open game log', command=lambda: self.open_path('log')).pack(side='left')
        ttk.Button(bar, text='Copy', command=self.copy_diagnostics).pack(side='left', padx=self.px(6))
        inputs.columnconfigure(1, weight=1)
        ttk.Label(inputs, text='Developer inputs passed to scripts/build_owned_backup.py. Output must be on D:. '
                  'Source defaults to this launcher\'s checkout.', style='Muted.TLabel',
                  wraplength=self.px(760)).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, self.px(8)))
        self.build_vars = {}
        for r, key in enumerate(BUILD_NAMES, 1):
            ttk.Label(inputs, text=key.replace('_', ' ')).grid(row=r, column=0, sticky='w', padx=(0, self.px(10)))
            var = tk.StringVar()
            self.build_vars[key] = var
            ttk.Entry(inputs, textvariable=var).grid(row=r, column=1, sticky='ew', pady=self.px(2))
            var.trace_add('write', lambda *_: self.update_build_info() if hasattr(self, 'build_info') else None)
        tools = ttk.Frame(inputs)
        tools.grid(row=len(BUILD_NAMES) + 1, column=0, columnspan=2, sticky='w', pady=(self.px(8), 0))
        ttk.Button(tools, text='Import build recipe…', command=self.import_recipe).pack(side='left')
        ttk.Button(tools, text='Save', command=self.save).pack(side='left', padx=self.px(8))

    def import_recipe(self):
        from tkinter import filedialog
        chosen = filedialog.askopenfilename(parent=self.root, filetypes=[('Build recipe', '*.json')])
        if not chosen:
            return
        try:
            recipe = load_recipe(chosen)
        except (OSError, ValueError) as e:
            self.messagebox.showerror('Cannot import recipe', str(e), parent=self.root)
            return
        self.recipe = recipe
        for k in BUILD_NAMES:
            self.build_vars[k].set(recipe[k])
        if not self.path_vars['elf'].get().strip():
            self.path_vars['elf'].set(recipe['elf'])

    def open_path(self, what):
        hdd0 = self.path_vars['hdd0'].get().strip()
        if not hdd0:
            return
        target = Path(hdd0).parent / ('launcher-game.log' if what == 'log' else '')
        if target.exists() and os.name == 'nt':
            os.startfile(str(target))

    def diagnostics_text(self):
        config = self.collect_quiet()
        caps = self.caps
        lines = [f'Launcher settings: {self.config_path} (schema {SCHEMA_VERSION}{", read only" if self.read_only else ""})']
        if self.notice:
            lines.append(f'Notice: {self.notice}')
        lines += ['', f'Executable: {config["paths"]["binary"] or "(not set)"}',
                  f'SHA-256: {caps.binary_sha256 or "(not computed)"}',
                  f'Capabilities: {caps.origin}. {caps.detail}',
                  f'  HUD size: {caps.hud_scale or "not supported"}; camera: {", ".join(caps.camera_aspects) if caps.camera_aspects else "flag not supported"}; '
                  f'monitor selection: {"yes" if caps.monitor_select else "no"}; borderless fullscreen: {"yes" if caps.fullscreen else "no"}',
                  f'  Engine settings file version for this build: {caps.native_settings_version}. The launcher never writes it; '
                  'it passes explicit options instead (explicit options win over that file).']
        binary = config['paths']['binary']
        state = read_checkpoint(Path(binary).parent.parent) if binary else None
        if state:
            lines += [f'Build record: state {state.get("state")}, source {state.get("paths", {}).get("source")}, '
                      f'runtime tested: {state.get("runtime_tested")}']
        try:
            args = player_arguments(validate_config(config), caps)
            lines += ['', 'Next launch options: ' + ' '.join(args)]
            if all(config['paths'].values()):
                env = intended_environment(config, caps)
                lines += ['Environment set by the launcher: ' + ', '.join(f'{k}={v}' for k, v in env.items()),
                          'All other inherited variables are dropped except basic Windows ones (PATH, SYSTEMROOT, ...); '
                          'diagnostic switches such as AUDIO_WAV, AUDIO_DEVICE_BURST or RSX_* never reach a normal launch.']
        except ValueError as e:
            lines += ['', f'Next launch: not possible yet ({e})']
        if config['paths']['hdd0']:
            profile = Path(config['paths']['hdd0']).parent
            receipt = profile / 'launcher-last-launch.json'
            if receipt.is_file():
                lines += ['', f'Last launch receipt: {receipt}']
            log = profile / 'launcher-game.log'
            if log.is_file():
                lines += ['', f'End of {log}:', tail(log, 25)]
        if self.last_build:
            lines += ['', f'Last build: exit {self.last_build["exit_code"]}. {self.last_build["message"]}']
        lines += ['', f'Appearance: {self.art.name(self.theme)} ({self.theme}); artwork {self.art.root}; '
                  f'Pillow {"available" if theme.HAVE_PIL else "not installed: art shown at native scale"}; '
                  f'reduced motion {"on" if self.reduced_motion else "off"}; decoded paintings held: '
                  f'{", ".join(self.art.cached) or "none"}.']
        lines += [f'Artwork problem: {e}' for e in self.art.errors]
        return '\n'.join(lines)

    def refresh_diagnostics(self):
        self.diag_text.configure(state='normal')
        self.diag_text.delete('1.0', 'end')
        self.diag_text.insert('1.0', self.diagnostics_text())
        self.diag_text.configure(state='disabled')

    def copy_diagnostics(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.diagnostics_text())

    # ------------------------------------------------------------------ close
    def close(self):
        if self.build_run is not None:
            if not self.messagebox.askyesno('Stop the build?', 'A build is running. Stop it and close the launcher?',
                                            parent=self.root):
                return
            self.build_run.cancel()
        elif self.is_dirty() and not self.read_only:
            answer = self.messagebox.askyesnocancel('Unsaved changes', 'Save your changes before closing?', parent=self.root)
            if answer is None or (answer and not self.save()):
                return
        self.shutdown()

    def shutdown(self):
        for pending in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(pending)
        self.root.destroy()


def gui(config_path, recipe_path=None):
    launcher_display.enable_dpi_awareness()
    import tkinter as tk
    root = tk.Tk()
    App(root, config_path, recipe_path)
    root.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, required=True, help='Absolute path to the launcher settings JSON')
    parser.add_argument('--build-recipe', type=Path, help='Absolute path to a build recipe JSON (Build-Folklore.cmd format)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Validate and print the launch plan without starting the game')
    mode.add_argument('--launch', action='store_true', help='Start the game with the saved settings')
    args = parser.parse_args(argv)
    if not args.config.is_absolute() or (args.build_recipe and not args.build_recipe.is_absolute()):
        parser.error('--config and --build-recipe must be absolute')
    try:
        if args.check or args.launch:
            config = load_config(args.config)
            caps = discover_capabilities(config['paths']['binary'])
            if args.check:
                print(json.dumps({'config': config, 'capabilities': {'origin': caps.origin, 'detail': caps.detail},
                                  'command': build_command(config, caps),
                                  'environment': intended_environment(config, caps)}))
            else:
                print(json.dumps({'pid': launch(config, caps).pid}))
            return 0
        return gui(args.config, args.build_recipe)
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
