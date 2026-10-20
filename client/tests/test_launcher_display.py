from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import launcher_display as ld  # noqa: E402


class WindowFit(unittest.TestCase):
    def check_inside(self, work, geometry):
        x, y, w, h, mw, mh = geometry
        self.assertGreaterEqual(x, work[0])
        self.assertGreaterEqual(y, work[1])
        self.assertLessEqual(x + w, work[2])
        self.assertLessEqual(y + h, work[3])
        self.assertLessEqual(mw, w)
        self.assertLessEqual(mh, h)

    def test_scales_with_dpi(self):
        for dpi, expected in ((96, (900, 660)), (120, (1125, 825)), (144, (1350, 990)), (192, (1800, 1320))):
            g = ld.fit_geometry((0, 0, 3840, 2160), dpi, (900, 660))
            self.assertEqual(g[2:4], expected)
            self.check_inside((0, 0, 3840, 2160), g)

    def test_small_or_scaled_screens_never_overflow(self):
        for work, dpi in (((0, 0, 1366, 728), 96), ((0, 0, 1280, 760), 144), ((0, 0, 800, 600), 192),
                          ((0, 0, 1920, 1040), 192)):
            with self.subTest(work=work, dpi=dpi):
                self.check_inside(work, ld.fit_geometry(work, dpi, (900, 660)))

    def test_stays_on_a_secondary_or_left_monitor(self):
        for work in ((3440, 0, 6000, 1400), (-2560, 200, 0, 1600)):
            with self.subTest(work=work):
                self.check_inside(work, ld.fit_geometry(work, 120, (900, 660)))

    def test_sdl_display_order(self):
        adapters = [('\\\\.\\DISPLAY1', True, False), ('\\\\.\\DISPLAY2', True, True),
                    ('\\\\.\\DISPLAY3', False, False), ('\\\\.\\DISPLAY4', True, False)]
        self.assertEqual(ld.order_like_sdl(adapters), ['\\\\.\\DISPLAY2', '\\\\.\\DISPLAY1', '\\\\.\\DISPLAY4'])

    def test_monitor_label(self):
        m = ld.Monitor(1, 'D2', False, (3440, 0, 6000, 1440), (3440, 0, 6000, 1400), 120)
        self.assertEqual(m.label(), 'Display 2 · 2560 × 1440 · 125% scaling')

    def test_live_monitor_list_is_consistent(self):
        monitors = ld.list_monitors()
        if not monitors:
            self.skipTest('no Windows monitor information')
        self.assertEqual([m.index for m in monitors], list(range(len(monitors))))
        self.assertTrue(monitors[0].primary)
        for m in monitors:
            self.assertGreater(m.size[0], 0)
            self.assertGreaterEqual(m.dpi, 96)


if __name__ == '__main__':
    unittest.main()
