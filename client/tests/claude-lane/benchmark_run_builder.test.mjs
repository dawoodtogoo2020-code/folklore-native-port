import test from 'node:test';
import assert from 'node:assert/strict';

import * as builderModule from '../../scripts/claude-lane/benchmark_run_builder.mjs';
import { createBenchmarkRunFromColumn } from '../../scripts/claude-lane/benchmark_run_builder.mjs';
import { parseBenchmarkRun } from '../../scripts/claude-lane/benchmark_run.mjs';
import { parseFrameTimeColumn } from '../../scripts/claude-lane/frame_time_column.mjs';

const LIMIT = 8 * 1024 * 1024;
const COMMIT = '0123456789abcdef0123456789abcdef01234567';
const RUN_FIELDS = [
  'schemaVersion',
  'runId',
  'commit',
  'platform',
  'hardware',
  'scene',
  'settings',
  'frameTimesMs',
];
const PLATFORMS = ['windows-x64', 'linux-x64', 'steamdeck-x64', 'android-arm64'];

function baseMetadata(overrides = {}) {
  return {
    schemaVersion: 1,
    runId: 'run-1.a_b',
    commit: COMMIT,
    platform: 'linux-x64',
    hardware: 'Test GPU',
    scene: 'Forest',
    settings: { quality: 'high', vsync: false },
    ...overrides,
  };
}

function metaText(overrides) {
  return JSON.stringify(baseMetadata(overrides));
}

function metaWithRawSettings(rawSettings) {
  const text = metaText({ settings: '__RAW_SETTINGS__' });
  return text.replace('"__RAW_SETTINGS__"', rawSettings);
}

function column(values) {
  return `frameTimeMs\n${values.join('\n')}\n`;
}

const COLUMN = column(['16.6', '17.25', '15']);

function withFrames(metadataText, framesText = '[1]') {
  assert.ok(metadataText.endsWith('}'));
  return `${metadataText.slice(0, -1)},"frameTimesMs":${framesText}}`;
}

function pickRun(run) {
  const picked = {};
  for (const field of RUN_FIELDS) {
    picked[field] = run[field];
  }
  return picked;
}

function expectedClass(error) {
  if (error instanceof RangeError) {
    return RangeError;
  }
  if (error instanceof SyntaxError) {
    return SyntaxError;
  }
  return TypeError;
}

function captureError(fn) {
  try {
    fn();
  } catch (error) {
    return error;
  }
  assert.fail('expected function to throw');
}

function assertSafe(error, markers = []) {
  assert.ok(error instanceof Error);
  assert.equal(typeof error.message, 'string');
  assert.match(error.message, /^[ -~]{1,200}$/);
  assert.equal(Object.prototype.hasOwnProperty.call(error, 'cause'), false);
  for (const marker of markers) {
    assert.equal(error.message.includes(marker), false);
  }
}

test('module exports only the builder function', () => {
  assert.deepEqual(Object.keys(builderModule), ['createBenchmarkRunFromColumn']);
  assert.equal(typeof createBenchmarkRunFromColumn, 'function');
});

test('returns compact JSON with one trailing LF and fixed field order', () => {
  const output = createBenchmarkRunFromColumn(metaText(), COLUMN);
  assert.equal(typeof output, 'string');
  assert.ok(output.endsWith('}\n'));
  assert.equal(output.indexOf('\n'), output.length - 1);
  const parsed = JSON.parse(output);
  assert.deepEqual(Object.keys(parsed), RUN_FIELDS);
  assert.equal(output, `${JSON.stringify(parsed)}\n`);
  assert.equal(output.includes('settingsKey'), false);
  assert.equal(output.includes('sampleSha256'), false);
});

test('output corresponds to the actual parser on the combined raw run', () => {
  const metadata = metaText();
  const output = createBenchmarkRunFromColumn(metadata, COLUMN);
  const reparsed = parseBenchmarkRun(output);
  const direct = parseBenchmarkRun(withFrames(metadata, '[16.6,17.25,15]'));
  assert.deepEqual(pickRun(reparsed), pickRun(direct));
  assert.equal(reparsed.settingsKey, direct.settingsKey);
  assert.equal(reparsed.sampleSha256, direct.sampleSha256);
  assert.equal(output, `${JSON.stringify(pickRun(direct))}\n`);
  assert.deepEqual(reparsed.frameTimesMs, parseFrameTimeColumn(COLUMN));
});

test('accepts all four platform labels', () => {
  for (const platform of PLATFORMS) {
    const output = createBenchmarkRunFromColumn(metaText({ platform }), COLUMN);
    const run = parseBenchmarkRun(output);
    assert.equal(run.platform, platform);
    assert.equal(JSON.parse(output).platform, platform);
  }
});

test('normalizes commit, labels, settings and preserves sample order', () => {
  const metadata = metaWithRawSettings('{"z":{"b":[3,1,2],"a":-0},"a":1}')
    .replace(COMMIT, COMMIT.toUpperCase())
    .replace('"Test GPU"', '"  Test GPU  "')
    .replace('"Forest"', '" Forest "');
  const output = createBenchmarkRunFromColumn(metadata, column(['3', '1', '2.5']));
  const parsed = JSON.parse(output);
  assert.equal(parsed.commit, COMMIT);
  assert.equal(parsed.hardware, 'Test GPU');
  assert.equal(parsed.scene, 'Forest');
  assert.deepEqual(Object.keys(parsed.settings), ['a', 'z']);
  assert.deepEqual(Object.keys(parsed.settings.z), ['a', 'b']);
  assert.ok(Object.is(parsed.settings.z.a, 0));
  assert.deepEqual(parsed.settings.z.b, [3, 1, 2]);
  assert.deepEqual(parsed.frameTimesMs, [3, 1, 2.5]);
  assert.ok(output.includes('"settings":{"a":1,"z":{"a":0,"b":[3,1,2]}}'));
  assert.ok(output.includes('"frameTimesMs":[3,1,2.5]'));
});

test('canonically equivalent settings produce identical output', () => {
  const first = createBenchmarkRunFromColumn(
    metaWithRawSettings('{"b":{"y":2,"x":[1,{"q":1,"p":2}]},"a":true}'),
    COLUMN,
  );
  const second = createBenchmarkRunFromColumn(
    metaWithRawSettings('{"a":true,"b":{"x":[1,{"p":2,"q":1}],"y":2}}'),
    COLUMN,
  );
  assert.equal(first, second);
  const reordered = createBenchmarkRunFromColumn(
    metaWithRawSettings('{"a":true,"b":{"x":[{"p":2,"q":1},1],"y":2}}'),
    COLUMN,
  );
  assert.notEqual(first, reordered);
});

test('same input produces deterministic output', () => {
  const metadata = metaText();
  const first = createBenchmarkRunFromColumn(metadata, COLUMN);
  const second = createBenchmarkRunFromColumn(metadata, COLUMN);
  assert.equal(first, second);
});

test('CRLF column produces the same run as LF column', () => {
  const lf = createBenchmarkRunFromColumn(metaText(), 'frameTimeMs\n4\n5\n');
  const crlf = createBenchmarkRunFromColumn(metaText(), 'frameTimeMs\r\n4\r\n5\r\n');
  assert.equal(lf, crlf);
});

test('rejects metadata containing frameTimesMs or acceptance/derived overrides', () => {
  const overrides = [
    'frameTimesMs',
    'settingsKey',
    'sampleSha256',
    'accepted',
    'acceptance',
    'derived',
    'frameCount',
    'meanMs',
    'p99Ms',
  ];
  for (const key of overrides) {
    const metadata = { ...baseMetadata(), [key]: key === 'frameTimesMs' ? [1, 2] : 'x' };
    const error = captureError(() => createBenchmarkRunFromColumn(JSON.stringify(metadata), COLUMN));
    assert.ok(error instanceof TypeError, key);
    assertSafe(error, [key]);
  }
  const replaced = { ...baseMetadata(), frameTimesMs: [1] };
  delete replaced.settings;
  assert.throws(() => createBenchmarkRunFromColumn(JSON.stringify(replaced), COLUMN), TypeError);
  assert.throws(
    () => createBenchmarkRunFromColumn(withFrames(metaText()).replace('{', '{"__proto__":{},'), COLUMN),
    TypeError,
  );
  assert.throws(
    () => createBenchmarkRunFromColumn(metaText().replace('{', '{"__proto__":{"x":1},'), COLUMN),
    TypeError,
  );
});

test('rejects metadata missing any required field', () => {
  for (const field of RUN_FIELDS.slice(0, 7)) {
    const metadata = baseMetadata();
    delete metadata[field];
    assert.throws(() => createBenchmarkRunFromColumn(JSON.stringify(metadata), COLUMN), TypeError, field);
  }
  assert.throws(() => createBenchmarkRunFromColumn('{}', COLUMN), TypeError);
});

test('rejects null, array and scalar metadata', () => {
  for (const text of ['null', '[]', '[1,2]', '1', '"x"', 'true', 'false', '0']) {
    assert.throws(() => createBenchmarkRunFromColumn(text, COLUMN), TypeError, text);
  }
});

test('rejects non-string arguments without coercion', () => {
  let called = false;
  const trap = {
    toString() {
      called = true;
      return metaText();
    },
    valueOf() {
      called = true;
      return metaText();
    },
  };
  for (const value of [trap, null, undefined, 1, true, [], baseMetadata(), new String(metaText())]) {
    assert.throws(() => createBenchmarkRunFromColumn(value, COLUMN), TypeError);
    assert.throws(() => createBenchmarkRunFromColumn(metaText(), value), TypeError);
  }
  assert.throws(() => createBenchmarkRunFromColumn(metaText(), new String(COLUMN)), TypeError);
  assert.throws(() => createBenchmarkRunFromColumn(metaText(), ['frameTimeMs', '1']), TypeError);
  assert.equal(called, false);
  assert.throws(() => createBenchmarkRunFromColumn(1, 2), TypeError);
});

test('malformed metadata JSON throws SyntaxError', () => {
  for (const text of ['', '{', '{"runId":}', "{'a':1}", '{"a":1,}', 'undefined', 'NaN']) {
    const error = captureError(() => createBenchmarkRunFromColumn(text, COLUMN));
    assert.ok(error instanceof SyntaxError, text);
    assertSafe(error);
  }
});

test('oversized metadata throws RangeError before JSON parsing', () => {
  const asciiOversize = ' '.repeat(LIMIT + 1);
  assert.throws(() => createBenchmarkRunFromColumn(asciiOversize, COLUMN), RangeError);
  const invalidButLarge = '{'.repeat(LIMIT + 1);
  assert.throws(() => createBenchmarkRunFromColumn(invalidButLarge, COLUMN), RangeError);
  const twoByte = String.fromCodePoint(0xe9).repeat(LIMIT / 2 + 1);
  assert.ok(twoByte.length < LIMIT);
  assert.throws(() => createBenchmarkRunFromColumn(twoByte, COLUMN), RangeError);
  const atLimitInvalid = ' '.repeat(LIMIT);
  assert.throws(() => createBenchmarkRunFromColumn(atLimitInvalid, COLUMN), SyntaxError);
});

test('metadata schema failures match the actual parser', () => {
  const nested = (depth) => {
    let text = '1';
    for (let level = 0; level < depth; level += 1) {
      text = level % 2 === 0 ? `[${text}]` : `{"k":${text}}`;
    }
    return text;
  };
  const cases = [
    ['schemaVersion string', metaText({ schemaVersion: '1' })],
    ['runId leading underscore', metaText({ runId: '_bad' })],
    ['runId empty', metaText({ runId: '' })],
    ['runId too long', metaText({ runId: 'a'.repeat(65) })],
    ['runId bad character', metaText({ runId: 'run id' })],
    ['runId number', metaText({ runId: 7 })],
    ['commit short', metaText({ commit: COMMIT.slice(1) })],
    ['commit non-hex', metaText({ commit: 'g'.repeat(40) })],
    ['platform unknown', metaText({ platform: 'macos-x64' })],
    ['hardware empty', metaText({ hardware: '' })],
    ['hardware blank', metaText({ hardware: '   ' })],
    ['hardware multiline', metaText({ hardware: 'a\nb' })],
    ['hardware too long', metaText({ hardware: 'a'.repeat(161) })],
    ['scene number', metaText({ scene: 5 })],
    ['scene null', metaText({ scene: null })],
    ['settings array', metaText({ settings: [1] })],
    ['settings null', metaText({ settings: null })],
    ['settings string', metaText({ settings: 'x' })],
    ['settings proto key', metaWithRawSettings('{"__proto__":{"x":1}}')],
    ['settings nested constructor', metaWithRawSettings('{"a":{"constructor":1}}')],
    ['settings nested prototype', metaWithRawSettings('{"a":[{"prototype":1}]}')],
    ['settings too deep', metaWithRawSettings(`{"d":${nested(40)}}`)],
  ];
  for (const [name, metadata] of cases) {
    const parserError = captureError(() => parseBenchmarkRun(withFrames(metadata)));
    const error = captureError(() => createBenchmarkRunFromColumn(metadata, COLUMN));
    assert.equal(error.constructor, expectedClass(parserError), name);
    assertSafe(error);
  }
  for (const version of [0, 2, '1', null]) {
    assert.throws(() => createBenchmarkRunFromColumn(metaText({ schemaVersion: version }), COLUMN), TypeError);
  }
});

test('accepts boundary-valid metadata labels and nested settings', () => {
  const runId = `A${'b'.repeat(63)}`;
  const hardware = String.fromCodePoint(0x1f600).repeat(160);
  const metadata = metaWithRawSettings('{"a":{"b":[{"c":1}]}}')
    .replace('"run-1.a_b"', JSON.stringify(runId))
    .replace('"Test GPU"', JSON.stringify(hardware));
  const output = createBenchmarkRunFromColumn(metadata, COLUMN);
  const run = parseBenchmarkRun(output);
  assert.equal(run.runId, runId);
  assert.equal(run.hardware, hardware);
  assert.deepEqual(run.settings, { a: { b: [{ c: 1 }] } });
});

test('invalid column input preserves the actual parser error class', () => {
  const bom = String.fromCharCode(0xfeff);
  const cases = [
    ['zero', column(['0'])],
    ['negative', column(['-1'])],
    ['overflow', column(['1e400'])],
    ['underflow', column(['1e-400'])],
    ['bom', `${bom}${column(['1'])}`],
    ['bare cr', 'frameTimeMs\n1\r2\n'],
    ['whitespace', column([' 1'])],
    ['quoted', column(['"1"'])],
    ['extra column', column(['1,2'])],
    ['blank row', 'frameTimeMs\n1\n\n2\n'],
    ['double terminator', 'frameTimeMs\n1\n\n'],
    ['wrong header', 'frameTimesMs\n1\n'],
    ['header only', 'frameTimeMs\n'],
    ['header without terminator', 'frameTimeMs'],
    ['empty', ''],
    ['non numeric', column(['abc'])],
    ['too many samples', `frameTimeMs\n${'1\n'.repeat(1000001)}`],
    ['oversize', `frameTimeMs\n${'1'.repeat(LIMIT)}\n`],
  ];
  for (const [name, text] of cases) {
    const parserError = captureError(() => parseFrameTimeColumn(text));
    const error = captureError(() => createBenchmarkRunFromColumn(metaText(), text));
    assert.equal(error.constructor, expectedClass(parserError), name);
    assertSafe(error);
    assert.notEqual(error.message, parserError.message, name);
  }
  assert.throws(() => createBenchmarkRunFromColumn(metaText(), column(['0'])), SyntaxError);
  assert.throws(() => createBenchmarkRunFromColumn(metaText(), `frameTimeMs\n${'1\n'.repeat(1000001)}`), RangeError);
  assert.throws(() => createBenchmarkRunFromColumn(metaText(), `frameTimeMs\n${'1'.repeat(LIMIT)}\n`), RangeError);
});

test('combined run over the byte limit throws RangeError', () => {
  const metadata = metaText({ settings: { pad: 'a'.repeat(LIMIT - 4096) } });
  assert.ok(metadata.length < LIMIT);
  const samples = column(new Array(4096).fill('1'));
  assert.ok(samples.length < LIMIT);
  const error = captureError(() => createBenchmarkRunFromColumn(metadata, samples));
  assert.ok(error instanceof RangeError);
  assertSafe(error);
  const combined = withFrames(metadata, `[${new Array(4096).fill('1').join(',')}]`);
  assert.ok(combined.length > LIMIT);
  assert.throws(() => parseBenchmarkRun(combined));
});

test('combined run just under the byte limit is accepted', () => {
  const metadata = metaText({ settings: { pad: 'a'.repeat(LIMIT - 4096) } });
  const output = createBenchmarkRunFromColumn(metadata, column(['2']));
  assert.ok(output.length - 1 <= LIMIT);
  const run = parseBenchmarkRun(output);
  assert.deepEqual(run.frameTimesMs, [2]);
  assert.equal(run.settings.pad.length, LIMIT - 4096);
});

test('tiny positive frame time Number.MIN_VALUE is a valid run input', () => {
  const output = createBenchmarkRunFromColumn(metaText(), column(['5e-324']));
  const run = parseBenchmarkRun(output);
  assert.deepEqual(run.frameTimesMs, [Number.MIN_VALUE]);
  assert.deepEqual(JSON.parse(output).frameTimesMs, [Number.MIN_VALUE]);
  const mixed = createBenchmarkRunFromColumn(metaText(), column(['5e-324', '1.7976931348623157e308']));
  assert.deepEqual(JSON.parse(mixed).frameTimesMs, [Number.MIN_VALUE, Number.MAX_VALUE]);
});

test('the final LF counts toward the reparsable output byte limit', () => {
  const small = createBenchmarkRunFromColumn(metaText({ settings: { pad: '' } }), column(['1']));
  const exactPad = 'a'.repeat(LIMIT - small.length);
  const exact = createBenchmarkRunFromColumn(metaText({ settings: { pad: exactPad } }), column(['1']));
  assert.equal(exact.length, LIMIT);
  assert.deepEqual(parseBenchmarkRun(exact).frameTimesMs, [1]);
  assert.throws(
    () => createBenchmarkRunFromColumn(metaText({ settings: { pad: exactPad + 'a' } }), column(['1'])),
    RangeError,
  );
});

test('diagnostics never leak secret markers or library messages', () => {
  const marker = 'SECRET-MARKER-7f3a';
  const cases = [
    [`{"runId":"${marker}"`, COLUMN],
    [metaText({ runId: `${marker}!` }), COLUMN],
    [metaText({ hardware: `${marker}\nline` }), COLUMN],
    [metaText({ platform: marker }), COLUMN],
    [metaText({ commit: marker }), COLUMN],
    [metaWithRawSettings(`{"${marker}":{"__proto__":1}}`), COLUMN],
    [JSON.stringify({ ...baseMetadata(), [marker]: marker }), COLUMN],
    [JSON.stringify([marker]), COLUMN],
    [metaText(), column([marker])],
    [metaText(), `${marker}\n1\n`],
    [metaText(), column([`1${marker}`])],
  ];
  for (const [metadata, text] of cases) {
    const error = captureError(() => createBenchmarkRunFromColumn(metadata, text));
    assertSafe(error, [marker, 'SECRET', 'D:', '/scripts/', '\\scripts\\']);
    assert.ok(
      error instanceof TypeError || error instanceof SyntaxError || error instanceof RangeError,
    );
  }
  const first = captureError(() => createBenchmarkRunFromColumn(metaText(), column([`${marker}a`])));
  const second = captureError(() => createBenchmarkRunFromColumn(metaText(), column(['other'])));
  assert.equal(first.message, second.message);
  const runA = captureError(() => createBenchmarkRunFromColumn(metaText({ runId: `_${marker}` }), COLUMN));
  const runB = captureError(() => createBenchmarkRunFromColumn(metaText({ runId: '_x' }), COLUMN));
  assert.equal(runA.message, runB.message);
  const parserError = captureError(() => parseBenchmarkRun(withFrames(metaText({ runId: '_x' }))));
  assert.notEqual(runB.message, parserError.message);
});
