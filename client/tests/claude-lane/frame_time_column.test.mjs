import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Buffer } from 'node:buffer';
import { parseFrameTimeColumn } from '../../scripts/claude-lane/frame_time_column.mjs';

const MAX_BYTES = 8 * 1024 * 1024;
const MAX_SAMPLES = 1000000;
const HEADER = 'frameTimeMs';
const LF = '\n';
const CRLF = '\r\n';
const BOM = String.fromCharCode(0xfeff);
const MARKER = 'ZQXJLEAK7731';
const MESSAGE_SHAPE = /^frame-time column: [a-z ]+ \(line \d+\)$/;

function doc(rows, eol = LF, final = true) {
  return [HEADER, ...rows].join(eol) + (final ? eol : '');
}

function assertSyntax(input, line) {
  assert.throws(
    () => parseFrameTimeColumn(input),
    (err) => {
      assert.ok(err instanceof SyntaxError, `expected SyntaxError, got ${err && err.name}`);
      assert.equal(err.name, 'SyntaxError');
      assert.match(err.message, MESSAGE_SHAPE);
      if (line !== undefined) {
        assert.ok(err.message.endsWith(`(line ${line})`), `expected line ${line}: ${err.message}`);
      }
      return true;
    },
  );
}

function assertRange(input) {
  assert.throws(
    () => parseFrameTimeColumn(input),
    (err) => {
      assert.ok(err instanceof RangeError, `expected RangeError, got ${err && err.name}`);
      return true;
    },
  );
}

test('parses unsigned integers', () => {
  assert.deepEqual(parseFrameTimeColumn(doc(['1', '16', '33', '1000', '7'])), [1, 16, 33, 1000, 7]);
});

test('parses decimals', () => {
  assert.deepEqual(parseFrameTimeColumn(doc(['0.5', '16.667', '1.000', '0.001'])), [0.5, 16.667, 1, 0.001]);
});

test('parses exponents in all permitted spellings', () => {
  assert.deepEqual(
    parseFrameTimeColumn(doc(['1e-2', '1E2', '2.5e+1', '5e-1', '0.5E1', '10e0'])),
    [0.01, 100, 25, 0.5, 5, 10],
  );
});

test('preserves original order without sorting', () => {
  assert.deepEqual(parseFrameTimeColumn(doc(['33', '1', '16', '1', '8'])), [33, 1, 16, 1, 8]);
});

test('returns a fresh dense plain array on each call', () => {
  const input = doc(['4', '5', '6']);
  const a = parseFrameTimeColumn(input);
  const b = parseFrameTimeColumn(input);
  assert.notEqual(a, b);
  assert.deepEqual(a, b);
  assert.ok(Array.isArray(a));
  assert.equal(Object.getPrototypeOf(a), Array.prototype);
  assert.equal(a.length, 3);
  for (let i = 0; i < a.length; i += 1) {
    assert.ok(Object.hasOwn(a, i));
    assert.equal(typeof a[i], 'number');
  }
  a[0] = 999;
  a.push(1);
  assert.deepEqual(parseFrameTimeColumn(input), [4, 5, 6]);
  assert.deepEqual(b, [4, 5, 6]);
});

test('accepts LF with and without one final line ending', () => {
  assert.deepEqual(parseFrameTimeColumn(doc(['1', '2'], LF, true)), [1, 2]);
  assert.deepEqual(parseFrameTimeColumn(doc(['1', '2'], LF, false)), [1, 2]);
});

test('accepts CRLF with and without one final line ending', () => {
  assert.deepEqual(parseFrameTimeColumn(doc(['1', '2'], CRLF, true)), [1, 2]);
  assert.deepEqual(parseFrameTimeColumn(doc(['1', '2'], CRLF, false)), [1, 2]);
});

test('accepts a mixture of LF and CRLF', () => {
  assert.deepEqual(parseFrameTimeColumn('frameTimeMs\r\n1\n2\r\n3\n'), [1, 2, 3]);
  assert.deepEqual(parseFrameTimeColumn('frameTimeMs\n1\r\n2\n3\r\n'), [1, 2, 3]);
  assert.deepEqual(parseFrameTimeColumn('frameTimeMs\n1\r\n2'), [1, 2]);
});

test('rejects repeated trailing blank lines', () => {
  assertSyntax('frameTimeMs\n1\n\n', 3);
  assertSyntax('frameTimeMs\r\n1\r\n\r\n', 3);
  assertSyntax('frameTimeMs\n1\r\n\n', 3);
  assertSyntax('frameTimeMs\n1\n\n\n', 3);
  assertSyntax('frameTimeMs\n\n', 2);
});

test('rejects blank interior rows', () => {
  assertSyntax('frameTimeMs\n1\n\n2\n', 3);
  assertSyntax('frameTimeMs\n\n1\n', 2);
  assertSyntax('frameTimeMs\r\n1\r\n\r\n2', 3);
});

test('rejects bare carriage returns', () => {
  assertSyntax('frameTimeMs\r1\r', 1);
  assertSyntax('frameTimeMs\n1\r', 2);
  assertSyntax('frameTimeMs\n1\r2\n', 2);
  assertSyntax('frameTimeMs\n1\n2\r\r\n', 3);
  assertSyntax('\r', 1);
});

test('rejects a byte order mark', () => {
  assertSyntax(BOM + doc(['1']), 1);
  assertSyntax(BOM + HEADER, 1);
});

test('rejects any header other than the exact frameTimeMs line', () => {
  for (const header of [
    '',
    'frametimems',
    'FrameTimeMs',
    'frameTime',
    'frameTimeMsx',
    'frameTimeMs,extra',
    'frameTimeMs,',
    ' frameTimeMs',
    'frameTimeMs ',
    'frameTimeMs\t',
    '"frameTimeMs"',
    'frameTimeS',
    '1',
  ]) {
    assertSyntax(`${header}\n1\n`, 1);
  }
  assertSyntax('', 1);
  assertSyntax('1\n2', 1);
});

test('rejects spaces and tabs in sample rows', () => {
  for (const token of [' 1', '1 ', ' 1 ', '\t1', '1\t', '1 2', ' ']) {
    assertSyntax(doc([token]), 2);
    assertSyntax(doc(['1', token]), 3);
  }
});

test('rejects quoted values', () => {
  for (const token of ['"1"', "'1'", '"1', '1"']) {
    assertSyntax(doc([token]), 2);
  }
});

test('rejects commas and extra columns', () => {
  for (const token of ['1,2', '1,', ',1', ',', '1.5,16']) {
    assertSyntax(doc([token]), 2);
  }
});

test('rejects spellings outside the JSON unsigned number grammar', () => {
  for (const token of [
    '.5', '1.', '01', '00', '007', '+1', '1e', '1e+', '1e-', '1.e1', '1..2',
    '1e1.5', '1ee1', 'e5', 'abc', '1ms', '16.6ms', '1_000', '1n', '1/2',
  ]) {
    assertSyntax(doc(['1', token, '2']), 3);
  }
});

test('rejects hexadecimal, binary and octal prefixes', () => {
  for (const token of ['0x10', '0X1A', '0b1', '0o7']) {
    assertSyntax(doc([token]), 2);
  }
});

test('rejects NaN and Infinity spellings', () => {
  for (const token of ['NaN', 'nan', 'Infinity', '+Infinity', '-Infinity', 'inf']) {
    assertSyntax(doc([token]), 2);
  }
});

test('rejects negative values including negative zero', () => {
  for (const token of ['-1', '-0', '-0.0', '-1e2', '-0.5']) {
    assertSyntax(doc([token]), 2);
  }
});

test('rejects zero values', () => {
  for (const token of ['0', '0.0', '0.000', '0e5', '0E-3', '0.0e+1']) {
    assertSyntax(doc([token]), 2);
  }
});

test('rejects exponent overflow but keeps the largest finite value', () => {
  for (const token of ['1e309', '2e308', '1e999999', '1.8e308']) {
    assertSyntax(doc([token]), 2);
  }
  assert.deepEqual(parseFrameTimeColumn(doc(['1.7976931348623157e308'])), [Number.MAX_VALUE]);
});

test('rejects numeric underflow to zero but keeps the smallest subnormal', () => {
  for (const token of ['1e-400', '1e-325', '2e-324', '0.1e-324']) {
    assertSyntax(doc([token]), 2);
  }
  assert.deepEqual(parseFrameTimeColumn(doc(['5e-324'])), [Number.MIN_VALUE]);
});

test('throws TypeError for non-string inputs', () => {
  for (const value of [
    undefined, null, 1, 0, true, Symbol('x'), 10n, {}, [], ['frameTimeMs', '1'],
    new String('frameTimeMs\n1'), Buffer.from('frameTimeMs\n1'), () => 'frameTimeMs\n1',
  ]) {
    assert.throws(() => parseFrameTimeColumn(value), TypeError);
  }
  assert.throws(() => parseFrameTimeColumn(), TypeError);
});

test('accepts input of exactly the byte limit', () => {
  const prefix = `${HEADER}\n1.`;
  const input = prefix + '0'.repeat(MAX_BYTES - prefix.length);
  assert.equal(Buffer.byteLength(input, 'utf8'), MAX_BYTES);
  assert.deepEqual(parseFrameTimeColumn(input), [1]);
});

test('rejects input one byte over the limit with RangeError', () => {
  const prefix = `${HEADER}\n1.`;
  const input = prefix + '0'.repeat(MAX_BYTES - prefix.length + 1);
  assert.equal(Buffer.byteLength(input, 'utf8'), MAX_BYTES + 1);
  assertRange(input);
});

test('byte limit is checked before any format validation', () => {
  assertRange('x'.repeat(MAX_BYTES + 1));
  assertRange(BOM + 'y'.repeat(MAX_BYTES));
  assertRange('\r'.repeat(MAX_BYTES + 1));
});

test('byte limit counts UTF-8 bytes rather than string length', () => {
  const twoByte = String.fromCharCode(0xe9);
  const exact = twoByte.repeat(MAX_BYTES / 2);
  assert.equal(Buffer.byteLength(exact, 'utf8'), MAX_BYTES);
  assertSyntax(exact, 1);
  const over = twoByte.repeat(MAX_BYTES / 2 + 1);
  assert.ok(over.length < MAX_BYTES);
  assertRange(over);
});

test('rejects a header with no samples using RangeError', () => {
  assertRange(HEADER);
  assertRange(`${HEADER}\n`);
  assertRange(`${HEADER}\r\n`);
});

test('accepts exactly the maximum sample count', () => {
  const input = `${HEADER}\n` + '1\n'.repeat(MAX_SAMPLES - 1) + '2\n';
  const result = parseFrameTimeColumn(input);
  assert.equal(result.length, MAX_SAMPLES);
  assert.equal(result[0], 1);
  assert.equal(result[MAX_SAMPLES - 2], 1);
  assert.equal(result[MAX_SAMPLES - 1], 2);
});

test('rejects one sample over the maximum with RangeError', () => {
  assertRange(`${HEADER}\n` + '1\n'.repeat(MAX_SAMPLES + 1));
  assertRange(`${HEADER}\n` + '1\n'.repeat(MAX_SAMPLES) + '1');
  assertRange(`${HEADER}\r\n` + '1\r\n'.repeat(MAX_SAMPLES + 1));
});

test('diagnostics use fixed categories and never reflect input', () => {
  const cases = [
    doc([MARKER]),
    doc([`1${MARKER}`]),
    doc([`"${MARKER}"`]),
    doc([`${MARKER},1`]),
    doc([`1,${MARKER}`]),
    doc([` ${MARKER}`]),
    doc([`-${MARKER}`]),
    `${MARKER}\n1\n`,
    `${HEADER},${MARKER}\n1\n`,
    `${HEADER}\n1\r${MARKER}\n`,
    BOM + `${HEADER}\n${MARKER}\n`,
    doc(['1', `0x${MARKER}`]),
  ];
  for (const input of cases) {
    assert.throws(
      () => parseFrameTimeColumn(input),
      (err) => {
        assert.ok(err instanceof SyntaxError);
        assert.match(err.message, MESSAGE_SHAPE);
        assert.ok(!err.message.includes(MARKER));
        assert.ok(!err.message.toLowerCase().includes(MARKER.toLowerCase()));
        return true;
      },
    );
  }
  for (const thunk of [
    () => parseFrameTimeColumn(MARKER.repeat(Math.ceil((MAX_BYTES + 1) / MARKER.length))),
    () => parseFrameTimeColumn(HEADER),
    () => parseFrameTimeColumn(new String(MARKER)),
  ]) {
    assert.throws(thunk, (err) => {
      assert.ok(!String(err.message).includes(MARKER));
      return true;
    });
  }
});

test('reports the 1-based line number of the offending row', () => {
  assertSyntax(doc(['1', '2', '3', 'bad']), 5);
  assertSyntax(doc(['1', '2', '3', 'bad'], CRLF), 5);
  assertSyntax(doc(['01']), 2);
});

test('parsing and re-importing leave prototypes and globals unchanged', async () => {
  const targets = [
    Object.prototype, Array.prototype, String.prototype, Number.prototype,
    RegExp.prototype, Function.prototype, SyntaxError.prototype, RangeError.prototype,
    TypeError.prototype, Error.prototype,
  ];
  const snapshot = () => ({
    protos: targets.map((p) => Reflect.ownKeys(p).map(String).sort()),
    globals: Reflect.ownKeys(globalThis).map(String).sort(),
  });
  const before = snapshot();
  const mod = await import('../../scripts/claude-lane/frame_time_column.mjs?inert-check');
  assert.equal(typeof mod.parseFrameTimeColumn, 'function');
  assert.deepEqual(mod.parseFrameTimeColumn(doc(['1', '2'])), [1, 2]);
  parseFrameTimeColumn(doc(['3', '0.5', '1e-2'], CRLF));
  for (const bad of [doc(['-0']), doc(['1e400']), `${HEADER}\r1`, HEADER, 42]) {
    assert.throws(() => parseFrameTimeColumn(bad));
  }
  assert.deepEqual(snapshot(), before);
});
