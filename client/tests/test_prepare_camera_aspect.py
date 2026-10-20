import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec=importlib.util.spec_from_file_location('bridge',Path(__file__).parents[1]/'scripts/prepare_camera_aspect.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

class CameraBridgeTests(unittest.TestCase):
    def test_both_graphics_camera_sites_are_required(self):
        data = b'// preserved legacy byte \x97\n#include "ppu_recomp.h"\n'
        for function in ('00145AD4', '00147D10'):
            data += (f'void func_{function}(ppu_context* ctx) {{\n' +
                     bridge.ANCHOR + '\n}\n').encode('ascii')
        data += b'void func_OTHER() {}\n'
        with mock.patch.object(bridge, 'SOURCE005_SHA256', bridge.digest(data)):
            changed = bridge.transform(data, 'ppu_recomp_005.cpp')
            self.assertEqual(changed.count(b'folklore_camera_aspect_override'), 2)
            self.assertIn(b'0x00145AD4u', changed)
            self.assertIn(b'0x00147D10u', changed)
            self.assertTrue(changed.startswith(b'// preserved legacy byte \x97\n'))
        missing = data.replace(bridge.ANCHOR.encode('ascii'), b'// absent', 1)
        with mock.patch.object(bridge, 'SOURCE005_SHA256', bridge.digest(missing)):
            with self.assertRaisesRegex(ValueError, 'load anchor'):
                bridge.transform(missing, 'ppu_recomp_005.cpp')

    def test_wrong_source_fails_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.cpp';source.write_text('unreviewed')
            output=root/'out/ppu_recomp_006.cpp'
            with self.assertRaisesRegex(ValueError,'digest'):bridge.prepare(source,output)
            self.assertFalse(output.exists())

    def test_preserves_source_and_refuses_changed_replacement(self):
        # Synthetic scaffold only; no translated game instructions in fixtures.
        data=b'// preserved legacy byte \x97\n'+('#include "ppu_recomp.h"\n'+bridge.FUNCTION+'\n'+bridge.ANCHOR+'\n}\nvoid func_OTHER() {}\n').encode()
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(bridge,'SOURCE_SHA256',bridge.digest(data)):
            root=Path(directory);source=root/'source.cpp';source.write_bytes(data)
            output=root/'out/ppu_recomp_006.cpp'
            receipt=bridge.prepare(source,output)
            self.assertEqual(source.read_bytes(),data)
            self.assertEqual(output.read_bytes().count(b'folklore_camera_aspect_override'),1)
            self.assertTrue(output.read_bytes().startswith(b'// preserved legacy byte \x97\n'))
            self.assertEqual(bridge.prepare(source,output),receipt)
            output.write_text('manual modification')
            with self.assertRaisesRegex(ValueError,'Existing'):bridge.prepare(source,output)
            self.assertEqual(output.read_text(),'manual modification')
            with self.assertRaisesRegex(ValueError,'separate'):bridge.prepare(source,source)

if __name__=='__main__':unittest.main()
