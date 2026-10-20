import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Buffer } from 'node:buffer';
import { createHash } from 'node:crypto';

import {
  canonicalSettings,
  parseBenchmarkRun,
} from '../../scripts/claude-lane/benchmark_run.mjs';

const MAX_JSON_BYTES = 8 * 1024 * 1024;
const POISON_KEYS = ['__proto__', 'constructor', 'prototype'];
const sha256 = (text) => createHash('sha256').update(text).digest('hex');

// Built from code points so the source holds no escaped or literal
// non-ASCII characters.
const char = (code) => String.fromCodePoint(code);
const E_ACUTE = char(0xe9);
const GRINNING = char(0x1f600);

// Nested positions used for poisoned / symbol-keyed array checks.
const NESTED_POSITIONS = [
  (inner) => ({ a: inner }),
  (inner) => ({ a: [inner] }),
  (inner) => ({ a: { b: [0, inner] } }),
  (inner) => ({ a: [[{ c: inner }]] }),
];

function runObject(overrides = {}) {
  return {
    schemaVersion: 1,
    runId: 'synthetic-run-001',
    commit: '0123456789abcdef0123456789abcdef01234567',
    platform: 'windows-x64',
    hardware: 'Synthetic GPU A',
    scene: 'Synthetic Scene 1',
    settings: { preset: 'synthetic', resolution: { width: 1280, height: 720 } },
    frameTimesMs: [40, 10, 30, 20],
    ...overrides,
  };
}

const runJson = (overrides) => JSON.stringify(runObject(overrides));

function runJsonWithout(field) {
  const run = runObject();
  delete run[field];
  return JSON.stringify(run);
}

function nestedObjects(levels) {
  let value = 1;
  for (let i = 0; i < levels; i += 1) {
    value = { k: value };
  }
  return value;
}

function nestedArrays(levels) {
  let value = 1;
  for (let i = 0; i < levels; i += 1) {
    value = [value];
  }
  return value;
}

// --- canonicalSettings ---

test('canonicalSettings sorts keys recursively and preserves array order', () => {
  const expected = '{"a":{"c":"x","d":[3,1,2]},"b":1}';
  assert.equal(canonicalSettings({ b: 1, a: { d: [3, 1, 2], c: 'x' } }), expected);
  assert.equal(canonicalSettings({ a: { c: 'x', d: [3, 1, 2] }, b: 1 }), expected);
  assert.equal(
    canonicalSettings({ list: [{ y: 1, x: 2 }, { b: null, a: true }] }),
    '{"list":[{"x":2,"y":1},{"a":true,"b":null}]}',
  );
});

test('canonicalSettings normalizes negative zero as JSON does', () => {
  assert.equal(canonicalSettings({ z: -0, a: [-0, 0.5, -1.25] }), '{"a":[0,0.5,-1.25],"z":0}');
});

test('canonicalSettings escapes strings and keys like JSON', () => {
  assert.equal(
    canonicalSettings({ [E_ACUTE]: E_ACUTE, 'k"ey': 'line\nbreak' }),
    `{"k\\"ey":"line\\nbreak","${E_ACUTE}":"${E_ACUTE}"}`,
  );
});

test('canonicalSettings handles empty containers', () => {
  assert.equal(canonicalSettings({}), '{}');
  assert.equal(canonicalSettings({ b: [], a: {} }), '{"a":{},"b":[]}');
});

test('canonicalSettings allows shared acyclic references', () => {
  const shared = { x: 1 };
  assert.equal(
    canonicalSettings({ a: shared, b: [shared, shared] }),
    '{"a":{"x":1},"b":[{"x":1},{"x":1}]}',
  );
});

test('canonicalSettings accepts null-prototype objects', () => {
  const value = Object.create(null);
  value.b = 2;
  value.a = 1;
  assert.equal(canonicalSettings(value), '{"a":1,"b":2}');
});

test('canonicalSettings accepts frozen input and does not mutate it', () => {
  const input = Object.freeze({
    b: Object.freeze([2, 1]),
    a: Object.freeze({ d: 1, c: 2 }),
  });
  const before = JSON.stringify(input);
  assert.equal(canonicalSettings(input), '{"a":{"c":2,"d":1},"b":[2,1]}');
  assert.equal(JSON.stringify(input), before);
  assert.deepEqual(Object.keys(input), ['b', 'a']);
  assert.deepEqual(input.b, [2, 1]);
});

test('canonicalSettings still accepts ordinary, frozen and shared arrays', () => {
  // Array.prototype has its own constructor; only own array keys are poison.
  assert.ok(Object.hasOwn(Array.prototype, 'constructor'));
  const shared = Object.freeze([3, 1, 2]);
  assert.equal(
    canonicalSettings({ b: shared, a: [shared, [], [[1]]] }),
    '{"a":[[3,1,2],[],[[1]]],"b":[3,1,2]}',
  );
  assert.ok(Object.isFrozen(shared));
  assert.deepEqual(shared, [3, 1, 2]);
});

test('canonicalSettings rejects non-object roots', () => {
  for (const root of [[], [1], null, undefined, 'x', 1, true, new Date(0), new Map()]) {
    assert.throws(() => canonicalSettings(root), TypeError);
  }
});

test('canonicalSettings rejects unsupported values', () => {
  for (const bad of [undefined, () => 1, Symbol('s'), 1n, NaN, Infinity, -Infinity]) {
    assert.throws(() => canonicalSettings({ a: bad }), TypeError);
    assert.throws(() => canonicalSettings({ a: [bad] }), TypeError);
    assert.throws(() => canonicalSettings({ a: { b: bad } }), TypeError);
  }
});

test('canonicalSettings rejects nonplain objects and arrays', () => {
  class Custom {}
  class SubArray extends Array {}
  for (const bad of [
    new Date(0),
    new Map(),
    new Set(),
    /x/,
    new String('x'),
    new Number(1),
    Object.create({}),
    new Custom(),
    SubArray.from([1]),
    new Uint8Array(1),
  ]) {
    assert.throws(() => canonicalSettings({ a: bad }), TypeError);
  }
});

test('canonicalSettings rejects poison keys', () => {
  assert.throws(() => canonicalSettings(JSON.parse('{"__proto__":{"x":1}}')), TypeError);
  assert.throws(() => canonicalSettings({ constructor: 1 }), TypeError);
  assert.throws(() => canonicalSettings({ prototype: 1 }), TypeError);
  assert.throws(() => canonicalSettings({ a: { constructor: {} } }), TypeError);
  assert.throws(() => canonicalSettings({ a: [JSON.parse('{"__proto__":1}')] }), TypeError);
});

test('canonicalSettings rejects nonenumerable own poison and symbol keys on objects', () => {
  for (const key of POISON_KEYS) {
    const object = { a: 1 };
    Object.defineProperty(object, key, { value: 1, enumerable: false });
    assert.throws(() => canonicalSettings(object), TypeError, key);
    assert.throws(() => canonicalSettings({ nested: object }), TypeError, key);
  }
  const symbolic = { a: 1 };
  Object.defineProperty(symbolic, Symbol('s'), { value: 1, enumerable: false });
  assert.throws(() => canonicalSettings({ nested: symbolic }), TypeError);
});

test('canonicalSettings rejects own poison keys on arrays at nested positions', () => {
  for (const key of POISON_KEYS) {
    for (const enumerable of [true, false]) {
      for (const place of NESTED_POSITIONS) {
        const array = [1, 2];
        Object.defineProperty(array, key, { value: 1, enumerable, configurable: true, writable: true });
        assert.ok(Object.hasOwn(array, key));
        assert.throws(
          () => canonicalSettings(place(array)),
          TypeError,
          `${key} enumerable=${enumerable}`,
        );
      }
    }
  }
});

test('canonicalSettings rejects symbol-keyed arrays at nested positions', () => {
  for (const enumerable of [true, false]) {
    for (const place of NESTED_POSITIONS) {
      const array = [1, 2];
      Object.defineProperty(array, Symbol('s'), { value: 1, enumerable });
      assert.throws(() => canonicalSettings(place(array)), TypeError, `enumerable=${enumerable}`);
    }
  }
  const frozen = [1];
  frozen[Symbol('s')] = 2;
  Object.freeze(frozen);
  assert.throws(() => canonicalSettings({ a: frozen }), TypeError);
});

test('canonicalSettings rejects cycles', () => {
  const self = {};
  self.self = self;
  assert.throws(() => canonicalSettings(self), TypeError);

  const arr = [];
  arr.push(arr);
  assert.throws(() => canonicalSettings({ arr }), TypeError);

  const a = {};
  const b = { a };
  a.b = b;
  assert.throws(() => canonicalSettings({ a }), TypeError);
});

test('canonicalSettings rejects sparse arrays', () => {
  // eslint-disable-next-line no-sparse-arrays
  assert.throws(() => canonicalSettings({ a: [1, , 2] }), TypeError);
  assert.throws(() => canonicalSettings({ a: new Array(3) }), TypeError);
  const trailing = [1];
  trailing.length = 2;
  assert.throws(() => canonicalSettings({ a: trailing }), TypeError);
});

test('canonicalSettings rejects symbol-keyed properties', () => {
  assert.throws(() => canonicalSettings({ a: 1, [Symbol('s')]: 2 }), TypeError);
});

test('canonicalSettings enforces maximum nesting depth 16', () => {
  assert.equal(canonicalSettings(nestedObjects(16)), `${'{"k":'.repeat(15)}{"k":1${'}'.repeat(16)}`);
  assert.throws(() => canonicalSettings(nestedObjects(17)), TypeError);
  assert.equal(canonicalSettings({ a: nestedArrays(15) }), `{"a":${'['.repeat(15)}1${']'.repeat(15)}}`);
  assert.throws(() => canonicalSettings({ a: nestedArrays(16) }), TypeError);
});

// --- parseBenchmarkRun ---

test('parseBenchmarkRun returns normalized copied output', () => {
  const text = JSON.stringify({
    frameTimesMs: [16.5, 17, 16],
    settings: { z: { b: 2, a: 1 }, a: [3, 1, 2] },
    scene: ' Synthetic Scene ',
    hardware: '  Synthetic Handheld  ',
    platform: 'steamdeck-x64',
    commit: 'ABCDEF0123456789ABCDEF0123456789ABCDEF01',
    runId: 'run.001_a-b',
    schemaVersion: 1,
  });
  const result = parseBenchmarkRun(text);
  assert.deepEqual(result, {
    schemaVersion: 1,
    runId: 'run.001_a-b',
    commit: 'abcdef0123456789abcdef0123456789abcdef01',
    platform: 'steamdeck-x64',
    hardware: 'Synthetic Handheld',
    scene: 'Synthetic Scene',
    settings: { a: [3, 1, 2], z: { a: 1, b: 2 } },
    frameTimesMs: [16.5, 17, 16],
    settingsKey: '{"a":[3,1,2],"z":{"a":1,"b":2}}',
    sampleSha256: sha256('[16.5,17,16]'),
  });
  assert.deepEqual(Object.keys(result), [
    'schemaVersion',
    'runId',
    'commit',
    'platform',
    'hardware',
    'scene',
    'settings',
    'frameTimesMs',
    'settingsKey',
    'sampleSha256',
  ]);
  assert.deepEqual(Object.keys(result.settings), ['a', 'z']);
  assert.deepEqual(Object.keys(result.settings.z), ['a', 'b']);
});

test('parseBenchmarkRun returns independent objects per call', () => {
  const text = runJson();
  const first = parseBenchmarkRun(text);
  const second = parseBenchmarkRun(text);
  assert.notEqual(first.settings, second.settings);
  assert.notEqual(first.frameTimesMs, second.frameTimesMs);
  first.frameTimesMs.push(1);
  first.settings.preset = 'changed';
  assert.deepEqual(parseBenchmarkRun(text), second);
});

test('parseBenchmarkRun accepts every supported platform', () => {
  for (const platform of ['windows-x64', 'linux-x64', 'steamdeck-x64', 'android-arm64']) {
    assert.equal(parseBenchmarkRun(runJson({ platform })).platform, platform);
  }
});

test('parseBenchmarkRun rejects unsupported platforms', () => {
  for (const platform of ['Windows-x64', 'macos-arm64', 'linux-arm64', 'windows-x64 ', '', 1, null]) {
    assert.throws(() => parseBenchmarkRun(runJson({ platform })), TypeError);
  }
});

test('parseBenchmarkRun requires schemaVersion 1', () => {
  for (const schemaVersion of [2, 0, '1', null, true, 1.5]) {
    assert.throws(() => parseBenchmarkRun(runJson({ schemaVersion })), TypeError);
  }
  const text = runJson().replace('"schemaVersion":1', '"schemaVersion":1.0');
  assert.equal(parseBenchmarkRun(text).schemaVersion, 1);
});

test('parseBenchmarkRun requires exactly the eight fields', () => {
  for (const field of [
    'schemaVersion',
    'runId',
    'commit',
    'platform',
    'hardware',
    'scene',
    'settings',
    'frameTimesMs',
  ]) {
    assert.throws(() => parseBenchmarkRun(runJsonWithout(field)), TypeError);
  }
  assert.throws(() => parseBenchmarkRun(runJson({ extra: 1 })), TypeError);
  assert.throws(() => parseBenchmarkRun(`{"__proto__":1,${runJson().slice(1)}`), TypeError);
});

test('parseBenchmarkRun rejects non-object JSON roots', () => {
  for (const text of ['[]', 'null', '"x"', '1', 'true']) {
    assert.throws(() => parseBenchmarkRun(text), TypeError);
  }
});

test('parseBenchmarkRun rejects non-string input', () => {
  for (const value of [undefined, null, 1, {}, runObject(), Buffer.from(runJson())]) {
    assert.throws(() => parseBenchmarkRun(value), TypeError);
  }
});

test('parseBenchmarkRun rejects malformed JSON with SyntaxError', () => {
  for (const text of ['', '{', '{"a":}', "{'a':1}", runJson().slice(0, -1)]) {
    assert.throws(() => parseBenchmarkRun(text), SyntaxError);
  }
});

test('parseBenchmarkRun enforces the 8 MiB UTF-8 limit', () => {
  const base = runJson();
  const atLimit = base + ' '.repeat(MAX_JSON_BYTES - Buffer.byteLength(base, 'utf8'));
  assert.equal(Buffer.byteLength(atLimit, 'utf8'), MAX_JSON_BYTES);
  assert.equal(parseBenchmarkRun(atLimit).runId, 'synthetic-run-001');

  assert.throws(() => parseBenchmarkRun(`${atLimit} `), RangeError);

  // Fewer UTF-16 units than the limit, but more UTF-8 bytes.
  const multibyte = `"${E_ACUTE.repeat(MAX_JSON_BYTES / 2)}"`;
  assert.ok(multibyte.length < MAX_JSON_BYTES);
  assert.throws(() => parseBenchmarkRun(multibyte), RangeError);

  // Size is checked before parsing.
  assert.throws(() => parseBenchmarkRun('x'.repeat(MAX_JSON_BYTES + 1)), RangeError);
});

test('parseBenchmarkRun validates runId', () => {
  for (const runId of ['a', 'A.b_c-1', '0', 'a'.repeat(64)]) {
    assert.equal(parseBenchmarkRun(runJson({ runId })).runId, runId);
  }
  for (const runId of [
    '',
    '-start',
    '.start',
    '_start',
    'has space',
    'slash/id',
    `${E_ACUTE}moji`,
    'a'.repeat(65),
    'line\nbreak',
    'trailing\n',
    7,
    null,
  ]) {
    assert.throws(() => parseBenchmarkRun(runJson({ runId })), TypeError);
  }
});

test('parseBenchmarkRun validates and lowercases commit', () => {
  const upper = 'ABCDEF0123456789ABCDEF0123456789ABCDEF01';
  assert.equal(parseBenchmarkRun(runJson({ commit: upper })).commit, upper.toLowerCase());
  for (const commit of [
    'a'.repeat(39),
    'a'.repeat(41),
    'g'.repeat(40),
    ` ${'a'.repeat(40)}`,
    `${'a'.repeat(40)}\n`,
    123,
    null,
  ]) {
    assert.throws(() => parseBenchmarkRun(runJson({ commit })), TypeError);
  }
});

test('parseBenchmarkRun validates hardware and scene labels', () => {
  for (const field of ['hardware', 'scene']) {
    const parse = (value) => parseBenchmarkRun(runJson({ [field]: value }))[field];

    assert.equal(parse('  Synthetic Label  '), 'Synthetic Label');
    assert.equal(parse('x'.repeat(160)), 'x'.repeat(160));
    assert.equal(parse(`  ${'x'.repeat(160)}  `), 'x'.repeat(160));
    assert.equal(parse(GRINNING.repeat(160)), GRINNING.repeat(160));

    for (const bad of [
      '',
      '   ',
      'x'.repeat(161),
      GRINNING.repeat(161),
      `a${char(0x00)}b`,
      'a\tb',
      'a\nb',
      'a\rb',
      `a${char(0x1f)}b`,
      `a${char(0x7f)}b`,
      `a${char(0x85)}b`,
      `a${char(0x2028)}b`,
      `a${char(0x2029)}b`,
      ' a\n',
      1,
      null,
      [],
    ]) {
      assert.throws(() => parse(bad), TypeError);
    }
  }
});

test('parseBenchmarkRun canonicalizes settings', () => {
  const first = parseBenchmarkRun(runJson({ settings: { b: { d: 1, c: 2 }, a: [2, 1] } }));
  const second = parseBenchmarkRun(runJson({ settings: { a: [2, 1], b: { c: 2, d: 1 } } }));
  assert.equal(first.settingsKey, '{"a":[2,1],"b":{"c":2,"d":1}}');
  assert.equal(second.settingsKey, first.settingsKey);
  assert.deepEqual(first.settings, { a: [2, 1], b: { c: 2, d: 1 } });

  assert.equal(
    parseBenchmarkRun(runJson({ settings: nestedObjects(16) })).settingsKey,
    canonicalSettings(nestedObjects(16)),
  );
});

test('parseBenchmarkRun rejects invalid settings', () => {
  for (const settings of [[], [1], null, 'x', 1, true, nestedObjects(17)]) {
    assert.throws(() => parseBenchmarkRun(runJson({ settings })), TypeError);
  }
  const base = runJson();
  for (const injected of ['"__proto__":1,', '"constructor":1,', '"prototype":1,', '"x":1e400,']) {
    const text = base.replace('"settings":{', `"settings":{${injected}`);
    assert.throws(() => parseBenchmarkRun(text), TypeError);
  }
});

test('parseBenchmarkRun rejects invalid frame values', () => {
  for (const frameTimesMs of ['16', 16, null, {}, true]) {
    assert.throws(() => parseBenchmarkRun(runJson({ frameTimesMs })), TypeError);
  }
  for (const bad of [0, -1, '16', null, true, [16], {}]) {
    assert.throws(() => parseBenchmarkRun(runJson({ frameTimesMs: [16, bad] })), TypeError);
  }
  const infinite = runJson().replace('"frameTimesMs":[40', '"frameTimesMs":[1e400');
  assert.throws(() => parseBenchmarkRun(infinite), TypeError);
});

test('parseBenchmarkRun enforces frame count bounds', () => {
  assert.throws(() => parseBenchmarkRun(runJson({ frameTimesMs: [] })), RangeError);

  const maxFrames = new Array(1_000_000).fill(1);
  const accepted = parseBenchmarkRun(runJson({ frameTimesMs: maxFrames }));
  assert.equal(accepted.frameTimesMs.length, 1_000_000);
  assert.equal(accepted.sampleSha256, sha256(JSON.stringify(maxFrames)));

  const overFrames = new Array(1_000_001).fill(1);
  assert.throws(() => parseBenchmarkRun(runJson({ frameTimesMs: overFrames })), RangeError);
});

test('parseBenchmarkRun digests compact JSON of frameTimesMs', () => {
  const result = parseBenchmarkRun(runJson());
  assert.match(result.sampleSha256, /^[0-9a-f]{64}$/);
  assert.equal(result.sampleSha256, sha256('[40,10,30,20]'));

  const reordered = parseBenchmarkRun(runJson({ frameTimesMs: [10, 20, 30, 40] }));
  assert.notEqual(reordered.sampleSha256, result.sampleSha256);

  const respelled = runJson().replace('"frameTimesMs":[40,10,30,20]', '"frameTimesMs":[16.50, 1e1]');
  assert.equal(parseBenchmarkRun(respelled).sampleSha256, sha256('[16.5,10]'));
});
