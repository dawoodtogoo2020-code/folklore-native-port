import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

import { compareBenchmarkRuns } from '../../scripts/claude-lane/benchmark_compare.mjs';
import { parseBenchmarkRun } from '../../scripts/claude-lane/benchmark_run.mjs';

const COMMIT_A = '0123456789abcdef0123456789abcdef01234567';
const COMMIT_B = 'fedcba9876543210fedcba9876543210fedcba98';
const sha256 = (text) => createHash('sha256').update(text).digest('hex');

function runJson(overrides = {}) {
  return JSON.stringify({
    schemaVersion: 1,
    runId: 'baseline1',
    commit: COMMIT_A,
    platform: 'linux-x64',
    hardware: 'Synthetic Rig',
    scene: 'Synthetic Corridor',
    settings: { preset: 'synthetic', scale: 1 },
    frameTimesMs: [40, 10, 30, 20],
    ...overrides,
  });
}

const BASELINE = runJson();
const CANDIDATE = runJson({
  runId: 'candidate1',
  commit: COMMIT_B.toUpperCase(),
  frameTimesMs: [10, 10, 10, 10],
});

const rangeErrorNaming = (field) => (error) =>
  error instanceof RangeError && error.message.includes(field);

test('compares [40,10,30,20] against [10,10,10,10]', () => {
  assert.deepEqual(compareBenchmarkRuns(BASELINE, CANDIDATE), {
    baseline: {
      runId: 'baseline1',
      commit: COMMIT_A,
      sampleCount: 4,
      sampleSha256: sha256('[40,10,30,20]'),
      summary: {
        count: 4,
        totalMs: 100,
        meanMs: 25,
        medianMs: 25,
        p95Ms: 40,
        minMs: 10,
        maxMs: 40,
        averageFps: 40,
      },
    },
    candidate: {
      runId: 'candidate1',
      commit: COMMIT_B,
      sampleCount: 4,
      sampleSha256: sha256('[10,10,10,10]'),
      summary: {
        count: 4,
        totalMs: 40,
        meanMs: 10,
        medianMs: 10,
        p95Ms: 10,
        minMs: 10,
        maxMs: 10,
        averageFps: 100,
      },
    },
    conditions: {
      platform: 'linux-x64',
      hardware: 'Synthetic Rig',
      scene: 'Synthetic Corridor',
      settingsKey: '{"preset":"synthetic","scale":1}',
    },
    deltas: {
      meanMs: { absolute: -15, percent: -60 },
      medianMs: { absolute: -15, percent: -60 },
      p95Ms: { absolute: -30, percent: -75 },
      averageFps: { absolute: 60, percent: 150 },
    },
  });
});

test('sample hashes match parseBenchmarkRun', () => {
  const result = compareBenchmarkRuns(BASELINE, CANDIDATE);
  assert.equal(result.baseline.sampleSha256, parseBenchmarkRun(BASELINE).sampleSha256);
  assert.equal(result.candidate.sampleSha256, parseBenchmarkRun(CANDIDATE).sampleSha256);
});

test('identical runs produce zero deltas', () => {
  const result = compareBenchmarkRuns(BASELINE, BASELINE);
  assert.deepEqual(result.baseline, result.candidate);
  for (const metric of ['meanMs', 'medianMs', 'p95Ms', 'averageFps']) {
    assert.deepEqual(result.deltas[metric], { absolute: 0, percent: 0 });
  }
});

test('reports a regression as plain numerical deltas', () => {
  const result = compareBenchmarkRuns(
    runJson({ frameTimesMs: [10, 10, 10, 10] }),
    runJson({ runId: 'candidate1', frameTimesMs: [40, 10, 30, 20] }),
  );
  assert.deepEqual(result.deltas, {
    meanMs: { absolute: 15, percent: 150 },
    medianMs: { absolute: 15, percent: 150 },
    p95Ms: { absolute: 30, percent: 300 },
    averageFps: { absolute: -60, percent: -60 },
  });
});

test('allows and retains different sample counts', () => {
  const result = compareBenchmarkRuns(
    runJson({ frameTimesMs: [20, 20] }),
    runJson({ runId: 'candidate1', frameTimesMs: [10, 10, 10] }),
  );
  assert.equal(result.baseline.sampleCount, 2);
  assert.equal(result.candidate.sampleCount, 3);
  assert.equal(result.baseline.summary.count, 2);
  assert.equal(result.candidate.summary.count, 3);
  assert.equal(result.baseline.summary.p95Ms, 20);
  assert.equal(result.candidate.summary.p95Ms, 10);
  assert.deepEqual(result.deltas.meanMs, { absolute: -10, percent: -50 });
  assert.deepEqual(result.deltas.averageFps, { absolute: 50, percent: 100 });
});

test('treats equivalent settings order and label whitespace as equal', () => {
  const result = compareBenchmarkRuns(
    runJson({ settings: { a: 1, b: { x: 1, y: 2 } } }),
    runJson({
      runId: 'candidate1',
      hardware: '  Synthetic Rig  ',
      settings: { b: { y: 2, x: 1 }, a: 1 },
    }),
  );
  assert.equal(result.conditions.settingsKey, '{"a":1,"b":{"x":1,"y":2}}');
  assert.equal(result.conditions.hardware, 'Synthetic Rig');
});

test('allows different commits and run IDs', () => {
  const result = compareBenchmarkRuns(BASELINE, CANDIDATE);
  assert.equal(result.baseline.commit, COMMIT_A);
  assert.equal(result.candidate.commit, COMMIT_B);
  assert.equal(result.baseline.runId, 'baseline1');
  assert.equal(result.candidate.runId, 'candidate1');
});

test('rejects each mismatched condition with a RangeError naming the field', () => {
  const cases = [
    ['platform', { platform: 'windows-x64' }],
    ['hardware', { hardware: 'Synthetic Rig B' }],
    ['scene', { scene: 'Synthetic Atrium' }],
    ['settingsKey', { settings: { preset: 'synthetic', scale: 2 } }],
  ];
  for (const [field, overrides] of cases) {
    assert.throws(
      () => compareBenchmarkRuns(BASELINE, runJson({ runId: 'candidate1', ...overrides })),
      rangeErrorNaming(field),
    );
  }
});

test('checks conditions before deriving summaries', () => {
  assert.throws(
    () => compareBenchmarkRuns(BASELINE, runJson({ scene: 'Other', frameTimesMs: [5e-324] })),
    rangeErrorNaming('scene'),
  );
});

test('propagates invalid input errors', () => {
  assert.throws(() => compareBenchmarkRuns(42, CANDIDATE), TypeError);
  assert.throws(() => compareBenchmarkRuns(BASELINE, undefined), TypeError);
  assert.throws(() => compareBenchmarkRuns('{', CANDIDATE), SyntaxError);
  assert.throws(() => compareBenchmarkRuns(BASELINE, '{'), SyntaxError);
  assert.throws(() => compareBenchmarkRuns(BASELINE, runJson({ frameTimesMs: [] })), RangeError);
  assert.throws(() => compareBenchmarkRuns(BASELINE, runJson({ platform: 'macos-arm64' })), TypeError);
  assert.throws(() => compareBenchmarkRuns(BASELINE, runJson({ frameTimesMs: [0] })), TypeError);
});

test('rejects nonfinite derived summaries', () => {
  // A valid but tiny sample makes averageFps = 1000 / 5e-324 = Infinity.
  assert.throws(
    () => compareBenchmarkRuns(runJson({ frameTimesMs: [5e-324] }), CANDIDATE),
    rangeErrorNaming('averageFps'),
  );
  assert.throws(
    () => compareBenchmarkRuns(BASELINE, runJson({ runId: 'candidate1', frameTimesMs: [5e-324] })),
    rangeErrorNaming('averageFps'),
  );
  // Sum overflow is rejected by the frame-time summary.
  assert.throws(
    () => compareBenchmarkRuns(BASELINE, runJson({ frameTimesMs: [1.7e308, 1.7e308] })),
    RangeError,
  );
});

test('rejects nonfinite derived percentages', () => {
  // Summaries are finite, but 100 * (1e308 - 1e-300) overflows.
  assert.throws(
    () =>
      compareBenchmarkRuns(
        runJson({ frameTimesMs: [1e-300] }),
        runJson({ runId: 'candidate1', frameTimesMs: [1e308] }),
      ),
    rangeErrorNaming('meanMs percent delta'),
  );
});

test('returns JSON-safe numbers only', () => {
  const result = compareBenchmarkRuns(BASELINE, CANDIDATE);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), result);
});
