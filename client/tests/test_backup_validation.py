import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('backup_validation',
    Path(__file__).resolve().parents[1] / 'launcher/backup_validation.py')
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def synthetic_sfo(fields=None):
    fields = fields or {'TITLE_ID': 'BCUS98147', 'VERSION': '01.00', 'CATEGORY': 'DG'}
    keys, values, records = bytearray(), bytearray(), []
    for key, value in fields.items():
        key_offset = len(keys)
        keys += key.encode('ascii') + b'\0'
        raw = value.encode('utf-8') + b'\0' if isinstance(value, str) else struct.pack('<I', value)
        capacity = (len(raw) + 3) & ~3
        records.append(struct.pack('<HHIII', key_offset, 0x204 if isinstance(value, str) else 0x404,
                                   len(raw), capacity, len(values)))
        values += raw + b'\0' * (capacity - len(raw))
    key_start = 20 + 16 * len(records)
    value_start = (key_start + len(keys) + 3) & ~3
    return (struct.pack('<4sIIII', b'\0PSF', 0x101, key_start, value_start, len(records)) +
            b''.join(records) + keys + b'\0' * (value_start - key_start - len(keys)) + values)


class BoundedSfo(unittest.TestCase):
    def test_valid_text_and_integer(self):
        fields = {'TITLE_ID': 'BCUS98147', 'VERSION': '01.00', 'CATEGORY': 'DG', 'BOOTABLE': 1}
        self.assertEqual(validation.parse_sfo(synthetic_sfo(fields)), fields)

    def test_short_header(self):
        for size in range(20):
            with self.subTest(size=size), self.assertRaises(ValueError):
                validation.parse_sfo(bytes(size))

    def test_bad_header_magic_version_count_and_tables(self):
        for offset, fmt, value in [(0, '<I', 1), (4, '<I', 0x102), (16, '<I', 0xffffffff),
                                   (16, '<I', 0), (8, '<I', 1), (12, '<I', 0xffffffff)]:
            data = bytearray(synthetic_sfo())
            struct.pack_into(fmt, data, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                validation.parse_sfo(bytes(data))

    def test_bad_entry_offsets_lengths_capacities_and_format(self):
        for offset, fmt, value in [(20, '<H', 65535), (22, '<H', 0x999), (24, '<I', 100),
                                   (28, '<I', 0), (28, '<I', 0xffffffff), (32, '<I', 0xfffffff0),
                                   (32, '<I', 1)]:
            data = bytearray(synthetic_sfo())
            struct.pack_into(fmt, data, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                validation.parse_sfo(bytes(data))

    def test_overlapping_values(self):
        data = bytearray(synthetic_sfo())
        struct.pack_into('<I', data, 48, 0)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            validation.parse_sfo(bytes(data))

    def test_duplicate_key(self):
        data = bytearray(synthetic_sfo())
        struct.pack_into('<H', data, 36, 0)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            validation.parse_sfo(bytes(data))

    def test_missing_text_terminator_and_bad_utf8(self):
        for replacement in (b'X', b'\xff'):
            data = bytearray(synthetic_sfo())
            value_start = struct.unpack_from('<I', data, 12)[0]
            data[value_start + (9 if replacement == b'X' else 0)] = replacement[0]
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                validation.parse_sfo(bytes(data))

    def test_truncated_payload_and_oversized_file(self):
        with self.assertRaises(ValueError):
            validation.parse_sfo(synthetic_sfo()[:-1])
        with self.assertRaises(ValueError):
            validation.parse_sfo(b'\0' * (validation.MAX_SFO_BYTES + 1))


class BackupIdentity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'PS3_GAME').mkdir()
        self.marker = self.root / 'PS3_DISC.SFB'
        self.marker.write_bytes(b'.SFB\0\x01\0\0' + bytes(24))
        self.sfo = self.root / 'PS3_GAME/PARAM.SFO'
        self.sfo.write_bytes(synthetic_sfo())
        self.elf = self.root / 'EBOOT.elf'
        self.elf.write_bytes(b'synthetic ELF fixture')
        self.expected_elf = hashlib.sha256(self.elf.read_bytes()).hexdigest()

    def test_valid_backup_without_app_version_and_no_writes(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with mock.patch.object(validation, 'ELF_SHA256', self.expected_elf):
            identity = validation.validate_backup(self.root, self.elf)
        self.assertEqual(identity['title_id'], 'BCUS98147')
        self.assertIsNone(identity['app_version'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_optional_matching_app_version(self):
        self.sfo.write_bytes(synthetic_sfo({'TITLE_ID': 'BCUS98147', 'VERSION': '01.00',
                                           'APP_VER': '01.00', 'CATEGORY': 'DG'}))
        with mock.patch.object(validation, 'ELF_SHA256', self.expected_elf):
            self.assertEqual(validation.validate_backup(self.root, self.elf)['app_version'], '01.00')

    def test_wrong_region_version_update_and_category(self):
        for key, value in [('TITLE_ID', 'BCES00050'), ('VERSION', '01.01'),
                           ('APP_VER', '01.01'), ('CATEGORY', 'GD')]:
            fields = {'TITLE_ID': 'BCUS98147', 'VERSION': '01.00', 'CATEGORY': 'DG'}
            fields[key] = value
            self.sfo.write_bytes(synthetic_sfo(fields))
            with self.subTest(key=key), self.assertRaises(ValueError):
                validation.validate_backup(self.root, self.elf)

    def test_wrong_elf(self):
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            validation.validate_backup(self.root, self.elf)

    def test_bad_short_and_missing_marker(self):
        for marker in (b'.SFB', b'wrong marker header' + bytes(32)):
            self.marker.write_bytes(marker)
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, 'marker'):
                validation.validate_backup(self.root, self.elf)
        self.marker.unlink()
        with self.assertRaisesRegex(ValueError, 'PS3_DISC.SFB'):
            validation.validate_backup(self.root, self.elf)

    def test_split_checks_keep_the_same_rules(self):
        # Setup reports the disc and the ELF separately; neither half may be looser.
        self.assertEqual(validation.validate_disc(self.root)['title_id'], 'BCUS98147')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            validation.validate_elf(self.elf)
        with mock.patch.object(validation, 'ELF_SHA256', self.expected_elf):
            self.assertEqual(validation.validate_elf(self.elf), self.expected_elf)
            self.assertEqual(validation.validate_backup(self.root, self.elf)['elf_sha256'], self.expected_elf)
        self.sfo.write_bytes(synthetic_sfo({'TITLE_ID': 'BCUS98147', 'VERSION': '01.01', 'CATEGORY': 'DG'}))
        with self.assertRaisesRegex(ValueError, 'version'):
            validation.validate_disc(self.root)
        with self.assertRaisesRegex(ValueError, 'Cannot read'):
            validation.validate_elf(self.root / 'missing.elf')


if __name__ == '__main__':
    unittest.main()
