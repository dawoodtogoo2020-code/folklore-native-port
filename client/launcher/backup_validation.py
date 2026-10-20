"""Read-only identity checks for the supported Folklore USA 1.00 disc backup."""
import hashlib
from pathlib import Path
import struct

ELF_SHA256 = '51a9eb16ceca3edfb7454b37a9e895f5a4e35bd515a40ae5b918f97c3a1b6c0e'
MAX_SFO_BYTES = 1024 * 1024
MAX_SFO_ENTRIES = 256


def parse_sfo(data):
    """Parse bounded PSF 1.1 metadata, rejecting ambiguous/overlapping fields."""
    if not isinstance(data, bytes) or not 20 <= len(data) <= MAX_SFO_BYTES:
        raise ValueError('PARAM.SFO is truncated or exceeds the 1 MiB limit.')
    magic, version, keys, values, count = struct.unpack_from('<4sIIII', data)
    table_end = 20 + count * 16
    if magic != b'\0PSF' or version != 0x101:
        raise ValueError('PARAM.SFO has an unsupported PSF header.')
    if not 1 <= count <= MAX_SFO_ENTRIES or not table_end <= keys < values <= len(data) or values % 4:
        raise ValueError('PARAM.SFO has invalid table offsets or entry count.')
    result, spans = {}, []
    for index in range(count):
        key_offset, fmt, length, capacity, offset = struct.unpack_from('<HHIII', data, 20 + index * 16)
        key_start = keys + key_offset
        if not keys <= key_start < values:
            raise ValueError('PARAM.SFO key offset is outside its key table.')
        key_end = data.find(b'\0', key_start, min(values, key_start + 129))
        if key_end < 0 or key_end == key_start:
            raise ValueError('PARAM.SFO has an empty, unterminated or oversized key.')
        try:
            key = data[key_start:key_end].decode('ascii')
        except UnicodeError as error:
            raise ValueError('PARAM.SFO has a non-ASCII key.') from error
        if key in result:
            raise ValueError('PARAM.SFO has duplicate keys.')
        start, end = values + offset, values + offset + capacity
        if capacity == 0 or length > capacity or offset % 4 or start < values or end > len(data):
            raise ValueError('PARAM.SFO value offset, length or capacity is invalid.')
        if any(start < previous_end and previous_start < end for previous_start, previous_end in spans):
            raise ValueError('PARAM.SFO value allocations overlap.')
        spans.append((start, end))
        raw = data[start:start + length]
        if fmt == 0x0204:
            if not raw.endswith(b'\0') or b'\0' in raw[:-1]:
                raise ValueError('PARAM.SFO text is not a single terminated string.')
            try:
                value = raw[:-1].decode('utf-8')
            except UnicodeError as error:
                raise ValueError('PARAM.SFO text contains invalid UTF-8.') from error
        elif fmt == 0x0404:
            if length != 4 or capacity != 4:
                raise ValueError('PARAM.SFO integer has an invalid length.')
            value = int.from_bytes(raw, 'little')
        elif fmt == 0x0004:
            value = raw
        else:
            raise ValueError('PARAM.SFO contains an unsupported value format.')
        result[key] = value
    return result


def _read_bounded(path, maximum, label):
    try:
        with path.open('rb') as stream:
            data = stream.read(maximum + 1)
    except OSError as error:
        raise ValueError(f'Cannot read {label}: {path}') from error
    if len(data) > maximum:
        raise ValueError(f'{label} exceeds its size limit.')
    return data


def validate_backup(disc, elf):
    """Validate disc metadata and exact decrypted ELF identity without writes.

    The tested original PARAM.SFO has VERSION=01.00 and no APP_VER. An optional
    APP_VER must also be 01.00 so an update/mixed-version folder fails closed.
    This checks identity, not every asset's integrity or gameplay compatibility.
    """
    result = validate_disc(disc)
    result['elf_sha256'] = validate_elf(elf)
    return result


def validate_disc(disc):
    """Check the disc marker and PARAM.SFO identity (the disc half of validate_backup)."""
    disc = Path(disc)
    marker = _read_bounded(disc / 'PS3_DISC.SFB', 65536, 'PS3_DISC.SFB')
    if len(marker) < 32 or marker[:8] != b'.SFB\0\x01\0\0':
        raise ValueError('PS3_DISC.SFB is missing a valid disc marker header.')
    sfo_bytes = _read_bounded(disc / 'PS3_GAME/PARAM.SFO', MAX_SFO_BYTES, 'PARAM.SFO')
    fields = parse_sfo(sfo_bytes)
    if fields.get('TITLE_ID') != 'BCUS98147':
        raise ValueError('Unsupported disc region/title: select Folklore USA BCUS98147.')
    if fields.get('CATEGORY') != 'DG':
        raise ValueError('Select the original disc backup, not an installed game or update folder.')
    if fields.get('VERSION') != '01.00' or ('APP_VER' in fields and fields['APP_VER'] != '01.00'):
        raise ValueError('Unsupported disc version: only USA disc VERSION 01.00 is supported.')
    return {'title_id': 'BCUS98147', 'version': '01.00', 'app_version': fields.get('APP_VER'),
            'param_sfo_sha256': hashlib.sha256(sfo_bytes).hexdigest(),
            'disc_marker_sha256': hashlib.sha256(marker).hexdigest()}


def validate_elf(elf):
    """Require the exact decrypted USA 1.00 EBOOT.elf; returns its SHA-256."""
    elf = Path(elf)
    h = hashlib.sha256()
    try:
        with elf.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
    except OSError as error:
        raise ValueError(f'Cannot read the decrypted EBOOT.elf: {elf}') from error
    if h.hexdigest() != ELF_SHA256:
        raise ValueError('Unsupported EBOOT.elf SHA256: use the decrypted ELF from the supported USA 1.00 disc.')
    return ELF_SHA256
