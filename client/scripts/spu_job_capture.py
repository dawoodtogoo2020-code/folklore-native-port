"""Verify bounded private SPURS captures after the diagnostic process exits."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from collections.abc import Sequence

SEED = 1469598103934665603
MAX_RECORDS = 16
MAX_IMAGE = 256 * 1024
MAX_DESCRIPTOR = 1024
MAX_SPAN_BYTES = 128 * 1024
SPAN_CONSISTENCY = 'separate checked copy; guest not quiesced'
PREFIX = re.compile(r"spurs-job-[0-9A-F]{8}-[0-9A-F]{16}-[0-9]{2}")


def _span_pairs(spans):
    if (not isinstance(spans, Sequence) or isinstance(spans, (str, bytes, bytearray)) or
            not 1 <= len(spans) <= 2):
        raise ValueError('capture spans must contain one or two pairs')
    pairs = []
    for pair in spans:
        if (not isinstance(pair, Sequence) or isinstance(pair, (str, bytes, bytearray)) or
                len(pair) != 2):
            raise ValueError('capture span must be an EA/size pair')
        ea, size = pair
        if (type(ea) is not int or type(size) is not int or ea < 0x10000 or
                not 1 <= size <= MAX_SPAN_BYTES or ea + size > 1 << 32):
            raise ValueError('invalid capture span extent')
        if any(ea < old_ea + old_size and old_ea < ea + size for old_ea, old_size in pairs):
            raise ValueError('overlapping capture spans')
        pairs.append((ea, size))
    if sum(size for _, size in pairs) > MAX_SPAN_BYTES:
        raise ValueError('capture spans exceed byte budget')
    return tuple(pairs)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate metadata key')
        result[key] = value
    return result


def runtime_fingerprint(data):
    value = SEED
    for byte in data:
        value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
    return value


def inspect_capture(directory, expected_aux_ea=None, expected_spans=None, expected_span_index=None):
    """Read only regular, unlinked capture files; never follow supplied paths."""
    result = {"status": "invalid", "records": [], "errors": [],
              "scope": "Captured bytes and metadata agree; guest memory was not quiesced."}
    try:
        if (expected_aux_ea is not None and
                (type(expected_aux_ea) is not int or not 0x10000 <= expected_aux_ea <= 0xFFFFFFFC)):
            raise ValueError("invalid requested auxiliary span")
        requested = None if expected_spans is None else _span_pairs(expected_spans)
        if expected_aux_ea is not None and requested is not None:
            raise ValueError('auxiliary and span requests are mutually exclusive')
        if expected_span_index is not None and (requested is None or
                type(expected_span_index) is not int or not 0 <= expected_span_index < MAX_RECORDS):
            raise ValueError('invalid requested capture ordinal')
        directory = Path(directory)
        if (directory.is_symlink() or not directory.is_dir() or
                getattr(directory.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError("capture directory is missing or is a link")
        files = {}
        total = 0
        for path in directory.iterdir():
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError("capture contains a link or non-file")
            if len(files) >= MAX_RECORDS * 5 or info.st_size > MAX_IMAGE:
                raise ValueError("capture exceeds file budget")
            total += info.st_size
            if total > MAX_RECORDS * (MAX_IMAGE + MAX_DESCRIPTOR + MAX_SPAN_BYTES + 8192):
                raise ValueError("capture exceeds byte budget")
            files[path.name] = path
        def read_checked(path, maximum):
            before = path.lstat()
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    getattr(before, 'st_file_attributes', 0) & 0x400):
                raise ValueError('capture file became a link or non-file')
            with path.open('rb') as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum or
                        getattr(info, 'st_file_attributes', 0) & 0x400 or
                        (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
                    raise ValueError('capture file changed or exceeds budget')
                data = source.read(maximum + 1)
                if len(data) > maximum or len(data) != info.st_size:
                    raise ValueError('capture length changed or exceeds budget')
                return data
        if not files:
            if expected_aux_ea is not None or requested is not None:
                raise ValueError("requested capture is missing")
            result["status"] = "none"
            return result
        used = set()
        ordinals = set()
        metadata = sorted(name for name in files if name.endswith('.json'))
        if not metadata or len(metadata) > MAX_RECORDS:
            raise ValueError("capture metadata is missing or exceeds budget")
        for name in metadata:
            prefix = name[:-5]
            if not PREFIX.fullmatch(prefix) or files[name].stat().st_size > 8192:
                raise ValueError("invalid metadata filename or size")
            ordinal = int(prefix[-2:])
            if ordinal >= MAX_RECORDS or ordinal in ordinals:
                raise ValueError('invalid or duplicate global capture ordinal')
            ordinals.add(ordinal)
            metadata_bytes = read_checked(files[name], 8192)
            meta = json.loads(metadata_bytes, object_pairs_hook=_unique_object)
            if not isinstance(meta, dict):
                raise ValueError('capture metadata must be an object')
            if meta.get("schema_version") not in (1, 2, 3) or type(meta.get("schema_version")) is not int:
                raise ValueError("unsupported capture schema")
            schema = meta['schema_version']
            selected = requested is not None and (expected_span_index is None or ordinal == expected_span_index)
            if selected and schema != 3:
                raise ValueError('requested spans are missing or misversioned')
            if requested is not None and not selected and schema != 1:
                raise ValueError('unselected capture must use schema 1')
            if schema != 3 and 'capture_spans' in meta:
                raise ValueError('capture spans require schema 3')
            expected = {"descriptor_file": prefix + '.desc.bin', "image_file": prefix + '.image.bin'}
            for key, leaf in expected.items():
                if meta.get(key) != leaf or leaf not in files:
                    raise ValueError("capture payload name or file is invalid")
            desc = read_checked(files[expected['descriptor_file']], MAX_DESCRIPTOR)
            image = read_checked(files[expected['image_file']], MAX_IMAGE)
            if (type(meta.get('descriptor_size')) is not int or not 48 <= len(desc) <= MAX_DESCRIPTOR or
                    len(desc) % 16 or len(desc) != meta['descriptor_size']):
                raise ValueError("descriptor size mismatch")
            if (type(meta.get('image_size')) is not int or not 0 < len(image) <= MAX_IMAGE or
                    len(image) != meta['image_size'] or len(image) != int.from_bytes(desc[8:10], 'big') * 16):
                raise ValueError("image size mismatch")
            def address(key, digits):
                value = meta.get(key)
                if not isinstance(value, str) or not re.fullmatch(r'0x[0-9A-F]{' + str(digits) + '}', value):
                    raise ValueError("invalid address or fingerprint")
                return int(value, 16)
            raw = address('raw_binary_ea', 16)
            binary = address('binary_ea', 8)
            job = address('job_ea', 8)
            fingerprint = address('runtime_fingerprint', 16)
            if (raw != int.from_bytes(desc[:8], 'big') or raw != binary or raw & 15 or job & 15 or
                    type(meta.get('binary_flags')) is not int or meta['binary_flags'] != 0 or
                    not 0x10000 <= binary < (1 << 32) or binary + len(image) > (1 << 32) or
                    not 0x10000 <= job < (1 << 32) or job + len(desc) > (1 << 32)):
                raise ValueError("descriptor address/flags mismatch")
            if (meta.get('fingerprint_seed') != str(SEED) or fingerprint != runtime_fingerprint(image) or
                    prefix[10:18] != f'{job:08X}' or prefix[19:35] != f'{fingerprint:016X}'):
                raise ValueError("runtime fingerprint mismatch")
            if meta.get('snapshot_consistency') != 'separate checked copies; guest not quiesced':
                raise ValueError("missing snapshot consistency boundary")
            auxiliary = {}
            spans_result = {}
            aux_keys = {'auxiliary_ea', 'auxiliary_size', 'auxiliary_file', 'auxiliary_consistency'}
            if meta['schema_version'] == 2:
                if not aux_keys <= meta.keys():
                    raise ValueError("incomplete auxiliary metadata")
                aux_ea = address('auxiliary_ea', 8)
                aux_leaf = prefix + '.aux.bin'
                if (type(meta['auxiliary_size']) is not int or meta['auxiliary_size'] != 4 or
                        not 0x10000 <= aux_ea <= 0xFFFFFFFC or
                        meta['auxiliary_file'] != aux_leaf or aux_leaf not in files or
                        meta['auxiliary_consistency'] != 'separate checked copy; guest not quiesced'):
                    raise ValueError("invalid auxiliary capture contract")
                aux = read_checked(files[aux_leaf], 4)
                if len(aux) != 4:
                    raise ValueError("auxiliary capture size mismatch")
                if expected_aux_ea is not None and aux_ea != expected_aux_ea:
                    raise ValueError("auxiliary capture EA differs from request")
                expected['auxiliary_file'] = aux_leaf
                auxiliary = {'auxiliary_file': aux_leaf, 'auxiliary_ea': meta['auxiliary_ea'],
                             'auxiliary_size': 4, 'auxiliary_sha256': hashlib.sha256(aux).hexdigest(),
                             'auxiliary_hex': aux.hex(), 'auxiliary_consistency': meta['auxiliary_consistency']}
            elif aux_keys & meta.keys() or expected_aux_ea is not None:
                raise ValueError("requested auxiliary capture is missing or misversioned")
            if schema == 3:
                if any(key.startswith('auxiliary_') for key in meta):
                    raise ValueError('schema 3 cannot include auxiliary metadata')
                spans = meta.get('capture_spans')
                if not isinstance(spans, list) or not 1 <= len(spans) <= 2:
                    raise ValueError('invalid capture spans metadata')
                pairs = []
                for index, span in enumerate(spans):
                    if not isinstance(span, dict) or set(span) != {'ea', 'size', 'file', 'consistency'}:
                        raise ValueError('invalid capture span fields')
                    ea = span['ea']
                    leaf = prefix + f'.span-{index:02d}.bin'
                    if (not isinstance(ea, str) or not re.fullmatch(r'0x[0-9A-F]{8}', ea) or
                            span['file'] != leaf or leaf not in files or
                            span['consistency'] != SPAN_CONSISTENCY):
                        raise ValueError('invalid capture span metadata')
                    pairs.append((int(ea, 16), span['size']))
                actual = _span_pairs(pairs)
                if selected and actual != requested:
                    raise ValueError('captured spans differ from requested ordered spans')
                verified_spans = []
                for index, span in enumerate(spans):
                    payload = read_checked(files[span['file']], span['size'])
                    if len(payload) != span['size']:
                        raise ValueError('capture span size mismatch')
                    expected[f'span_{index}'] = span['file']
                    record = {'file': span['file'], 'ea': span['ea'], 'size': len(payload),
                              'sha256': hashlib.sha256(payload).hexdigest(), 'consistency': SPAN_CONSISTENCY}
                    if len(payload) <= 64:
                        record['hex'] = payload.hex()
                    verified_spans.append(record)
                spans_result['capture_spans'] = verified_spans
            used.update((name, *expected.values()))
            result['records'].append({"metadata_file": name, "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                "descriptor_file": expected['descriptor_file'], "descriptor_size": len(desc), "descriptor_sha256": hashlib.sha256(desc).hexdigest(),
                "image_file": expected['image_file'], "image_size": len(image), "image_sha256": hashlib.sha256(image).hexdigest(),
                "job_ea": meta['job_ea'], "binary_ea": meta['binary_ea'], "raw_binary_ea": meta['raw_binary_ea'],
                "runtime_fingerprint": meta['runtime_fingerprint'], "fingerprint_seed": str(SEED), **auxiliary, **spans_result})
        if expected_span_index is not None and expected_span_index not in ordinals:
            raise ValueError('requested capture ordinal is missing')
        if used != set(files):
            raise ValueError("unreferenced or incomplete capture files")
        result['status'] = 'verified'
    except (OSError, ValueError, TypeError, KeyError) as error:
        result['records'] = []
        result['errors'].append(str(error))
    return result
