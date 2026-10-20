import { test } from 'node:test';
import assert from 'node:assert/strict';

import { summarizeFrameTimes } from '../../scripts/claude-lane/frame_time_summary.mjs';

test('summarizes unsorted even-count input [40, 10, 30, 20]', () => {
  assert.deepEqual(summarizeFrameTimes([40, 10, 30, 20]), {
    count: 4,
    totalMs: 100,
    meanMs: 25,
    medianMs: 25,
    p95Ms: 40,
    minMs: 10,
    maxMs: 40,
    averageFps: 40,
  });
});

test('returns exactly the documented keys', () => {
  assert.deepEqual(Object.keys(summarizeFrameTimes([16])).sort(), [
    'averageFps',
    'count',
    'maxMs',
    'meanMs',
    'medianMs',
    'minMs',
    'p95Ms',
    'totalMs',
  ]);
});

test('summarizes a single sample [16]', () => {
  assert.deepEqual(summarizeFrameTimes([16]), {
    count: 1,
    totalMs: 16,
    meanMs: 16,
    medianMs: 16,
    p95Ms: 16,
    minMs: 16,
    maxMs: 16,
    averageFps: 62.5,
  });
});

test('uses the center value as median for odd counts', () => {
  assert.deepEqual(summarizeFrameTimes([5, 1, 3]), {
    count: 3,
    totalMs: 9,
    meanMs: 3,
    medianMs: 3,
    p95Ms: 5,
    minMs: 1,
    maxMs: 5,
    averageFps: 1000 / 3,
  });
  assert.equal(summarizeFrameTimes([9, 2, 7, 4, 100]).medianMs, 7);
});

test('averages the two center values for even-count median', () => {
  assert.equal(summarizeFrameTimes([1, 2, 3, 10]).medianMs, 2.5);
  assert.equal(summarizeFrameTimes([8, 17]).medianMs, 12.5);
});

test('accepts frozen input and does not mutate it', () => {
  const frozen = Object.freeze([30, 10, 20]);
  const result = summarizeFrameTimes(frozen);
  assert.equal(result.medianMs, 20);
  assert.equal(result.minMs, 10);
  assert.equal(result.maxMs, 30);
  assert.ok(Object.isFrozen(frozen));
  assert.deepEqual(frozen, [30, 10, 20]);
});

test('does not reorder a mutable input array', () => {
  const input = [40, 10, 30, 20];
  summarizeFrameTimes(input);
  assert.deepEqual(input, [40, 10, 30, 20]);
});

test('uses nearest-rank p95 at larger boundaries', () => {
  // Deterministic shuffle of 1..n so sorting is actually exercised.
  const shuffled = (n) => {
    const values = Array.from({ length: n }, (_, i) => i + 1);
    for (let i = n - 1; i > 0; i -= 1) {
      const j = (i * 7 + 3) % (i + 1);
      [values[i], values[j]] = [values[j], values[i]];
    }
    return values;
  };

  // count 20: ceil(19) - 1 = 18 -> 19th smallest.
  assert.equal(summarizeFrameTimes(shuffled(20)).p95Ms, 19);
  // count 21: ceil(19.95) - 1 = 19 -> 20th smallest.
  assert.equal(summarizeFrameTimes(shuffled(21)).p95Ms, 20);
  // count 100: ceil(95) - 1 = 94 -> 95th smallest.
  assert.equal(summarizeFrameTimes(shuffled(100)).p95Ms, 95);
  // count 101: ceil(95.95) - 1 = 95 -> 96th smallest.
  assert.equal(summarizeFrameTimes(shuffled(101)).p95Ms, 96);
});

test('rejects non-array input with TypeError', () => {
  for (const value of [
    undefined,
    null,
    16,
    '16',
    { length: 1, 0: 16 },
    new Float64Array([16]),
    new Set([16]),
  ]) {
    assert.throws(() => summarizeFrameTimes(value), TypeError);
  }
  assert.throws(() => summarizeFrameTimes(), TypeError);
});

test('rejects holes with TypeError', () => {
  // eslint-disable-next-line no-sparse-arrays
  assert.throws(() => summarizeFrameTimes([10, , 20]), TypeError);
  // eslint-disable-next-line no-sparse-arrays
  assert.throws(() => summarizeFrameTimes([, 16]), TypeError);
  assert.throws(() => summarizeFrameTimes(new Array(2)), TypeError);
  const trailing = [16];
  trailing.length = 2;
  assert.throws(() => summarizeFrameTimes(trailing), TypeError);
});

test('rejects invalid member types with TypeError', () => {
  for (const member of [
    undefined,
    null,
    '16',
    true,
    16n,
    {},
    [16],
    new Number(16),
  ]) {
    assert.throws(() => summarizeFrameTimes([16, member]), TypeError);
  }
});

test('rejects nonfinite members with TypeError', () => {
  for (const member of [NaN, Infinity, -Infinity]) {
    assert.throws(() => summarizeFrameTimes([16, member]), TypeError);
  }
});

test('rejects zero and negative members with TypeError', () => {
  for (const member of [0, -0, -1, -Number.MIN_VALUE]) {
    assert.throws(() => summarizeFrameTimes([16, member]), TypeError);
  }
});

test('rejects empty input with RangeError', () => {
  assert.throws(() => summarizeFrameTimes([]), RangeError);
  assert.throws(() => summarizeFrameTimes(Object.freeze([])), RangeError);
});

test('rejects an overflowing sum with RangeError', () => {
  assert.throws(
    () => summarizeFrameTimes([Number.MAX_VALUE, Number.MAX_VALUE]),
    RangeError,
  );
  assert.throws(
    () => summarizeFrameTimes([Number.MAX_VALUE / 2, Number.MAX_VALUE, 1]),
    RangeError,
  );
});

test('accepts a single maximal sample whose sum is still finite', () => {
  const result = summarizeFrameTimes([Number.MAX_VALUE]);
  assert.equal(result.totalMs, Number.MAX_VALUE);
  assert.equal(result.averageFps, 1000 / Number.MAX_VALUE);
});
