import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('limiter', Path(__file__).parents[1] / 'scripts/prepare_frame_limiter.py')
limiter = importlib.util.module_from_spec(spec); spec.loader.exec_module(limiter)


def scaffold(eol=b'\n', site=None):
    # Synthetic scaffold around the guarded site only.
    site = limiter.SITE if site is None else site
    body = (b'// preserved legacy byte \x97\n#include "ppu_recomp.h"\n' + limiter.FUNCTION + b'\n' + site +
            b'}\nvoid func_OTHER(ppu_context* ctx) {\n}\n')
    return body.replace(b'\n', eol)


class FrameLimiterBridgeTests(unittest.TestCase):
    def test_replaces_only_the_two_constants(self):
        for eol in (b'\n', b'\r\n'):
            data = scaffold(eol)
            with mock.patch.object(limiter, 'SOURCE_SHA256', limiter.digest(data)):
                changed = limiter.transform(data)
            self.assertIn(b'#include "frame_limiter.h"', changed)
            self.assertIn(b'(int64_t)folklore_frame_floor_units()', changed)
            self.assertIn(b'(int32_t)(folklore_frame_sleep_us())', changed)
            self.assertNotIn(b'0xBB8', changed)
            self.assertNotIn(b'0x2710', changed)
            self.assertTrue(changed.startswith(b'// preserved legacy byte \x97'))
            # Nothing else moves: undo the two substitutions and the include.
            undone = (changed.replace(b'#include "ppu_recomp.h"' + eol + b'#include "frame_limiter.h"', b'#include "ppu_recomp.h"')
                      .replace(b'(int64_t)folklore_frame_floor_units()', b'(int64_t)0xBB8')
                      .replace(b'(int32_t)(folklore_frame_sleep_us())', b'(int32_t)(0x2710)'))
            self.assertEqual(undone, data)

    def test_changed_site_is_refused(self):
        data = scaffold(site=limiter.SITE.replace(b'0xBB8', b'0xBB9'))
        with mock.patch.object(limiter, 'SOURCE_SHA256', limiter.digest(data)):
            with self.assertRaisesRegex(ValueError, 'site differs'):
                limiter.transform(data)
        twice = scaffold() + limiter.SITE
        with mock.patch.object(limiter, 'SOURCE_SHA256', limiter.digest(twice)):
            with self.assertRaisesRegex(ValueError, 'site differs'):
                limiter.transform(twice)

    def test_wrong_source_fails_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'ppu_recomp_007.cpp'; source.write_text('unreviewed')
            output = root / 'out/ppu_recomp_007.cpp'
            with self.assertRaisesRegex(ValueError, 'digest'):
                limiter.prepare(source, output)
            self.assertFalse(output.exists())

    def test_preserves_source_and_refuses_changed_replacement(self):
        data = scaffold()
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(limiter, 'SOURCE_SHA256', limiter.digest(data)):
            root = Path(directory); source = root / 'src/ppu_recomp_007.cpp'
            source.parent.mkdir(); source.write_bytes(data)
            output = root / 'out/ppu_recomp_007.cpp'
            receipt = limiter.prepare(source, output)
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual(receipt['original_constants'], {'floor_units_10us': 3000, 'sleep_us': 10000})
            self.assertEqual(limiter.prepare(source, output), receipt)
            output.write_text('manual modification')
            with self.assertRaisesRegex(ValueError, 'Existing'):
                limiter.prepare(source, output)
            with self.assertRaisesRegex(ValueError, 'separate'):
                limiter.prepare(source, source)


if __name__ == '__main__':
    unittest.main()
