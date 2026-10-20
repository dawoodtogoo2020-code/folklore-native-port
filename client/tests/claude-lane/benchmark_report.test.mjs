import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

import { createBenchmarkReport } from '../../scripts/claude-lane/benchmark_report.mjs';
import { compareBenchmarkRuns } from '../../scripts/claude-lane/benchmark_compare.mjs';

const COMMIT_A = '0123456789abcdef0123456789abcdef01234567';
const COMMIT_B = 'fedcba9876543210fedcba9876543210fedcba98';
const sha256 = (text) => createHash('sha256').update(text).digest('hex');
const LINE_SEPARATOR = String.fromCodePoint(0x2028);

const LIMITATION =
  'Caller-supplied metadata and frame-time samples do not prove hardware identity, actual execution, feature wiring, stability or an optimized build.';
const NO_ACCEPTANCE =
  'This report does not accept gameplay, does not accept any feature and does not claim an optimized build.';
const UNITS_NOTE =
  'Units: frame times are in milliseconds (ms); FPS is frames per second, derived as 1000 / mean frame time. ' +
  'Absolute delta = candidate - baseline. Percent delta = 100 * (candidate - baseline) / baseline. ' +
  'Markdown values are rounded to 12 significant digits; the JSON keeps full precision. ' +
  'Lower milliseconds or higher FPS are numerical observations only, not acceptance of any build.';

// Detects affirmative acceptance claims while ignoring explicit negative
// scope such as "Gameplay accepted: no" or "does not accept gameplay".
const AFFIRMATIVE_CLAIM_PATTERNS = [
  // "Gameplay accepted: yes", "Optimized build claimed: true"
  /\b(gameplay|features?|optimi[sz]ed build)\s+(accepted|claimed|confirmed|verified|proven)\s*:\s*(yes|true)\b/i,
  // "Gameplay is accepted", "Features are verified"
  /\b(gameplay|features?|optimi[sz]ed build)\s+(is|are|was|were|has been|have been)\s+(accepted|claimed|confirmed|verified|proven)\b/i,
  // "accepts the gameplay", "confirmed an optimized build"
  /\b(accepts|accepted|confirms|confirmed|verifies|verified|proves|proved|claims|claimed)\s+(an?\s+|the\s+)?(gameplay|features?|optimi[sz]ed build)\b/i,
];

const claimsAcceptance = (text) => AFFIRMATIVE_CLAIM_PATTERNS.some((pattern) => pattern.test(text));

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
const CANDIDATE = runJson({ runId: 'candidate1', commit: COMMIT_B, frameTimesMs: [10, 10, 10, 10] });

function hasUnescapedAsciiPunctuation(text) {
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '\\') {
      i += 1;
      continue;
    }
    if (/[!-\/:-@\[-`{-~]/.test(ch)) {
      return true;
    }
  }
  return false;
}

test('json contains schema, full comparison, scope and limitations', () => {
  const { json } = createBenchmarkReport(BASELINE, CANDIDATE);
  assert.deepEqual(json, {
    schemaVersion: 1,
    comparison: compareBenchmarkRuns(BASELINE, CANDIDATE),
    scope: {
      measurement: 'supplied frame-time samples only',
      gameplayAccepted: false,
      featureAccepted: false,
      optimizedBuildClaimed: false,
    },
    limitations: [LIMITATION, NO_ACCEPTANCE],
  });
  assert.equal(json.comparison.baseline.summary.meanMs, 25);
  assert.equal(json.comparison.candidate.summary.averageFps, 100);
  assert.deepEqual(json.comparison.deltas.meanMs, { absolute: -15, percent: -60 });
  assert.deepEqual(json.comparison.deltas.averageFps, { absolute: 60, percent: 150 });
  assert.deepEqual(JSON.parse(JSON.stringify(json)), json);
});

test('markdown matches the exact deterministic layout', () => {
  const { markdown } = createBenchmarkReport(BASELINE, CANDIDATE);
  const expected = [
    '# Frame-time benchmark comparison',
    '',
    'Measurement scope: supplied frame-time samples only.',
    '',
    '- Gameplay accepted: no',
    '- Feature accepted: no',
    '- Optimized build claimed: no',
    '',
    '## Runs',
    '',
    '| Role | Run ID | Commit | Sample count | Sample SHA-256 |',
    '| --- | --- | --- | ---: | --- |',
    `| Baseline | baseline1 | ${COMMIT_A} | 4 | ${sha256('[40,10,30,20]')} |`,
    `| Candidate | candidate1 | ${COMMIT_B} | 4 | ${sha256('[10,10,10,10]')} |`,
    '',
    '## Matching conditions',
    '',
    '- Platform: linux\\-x64',
    '- Hardware: Synthetic Rig',
    '- Scene: Synthetic Corridor',
    '- Settings (canonical JSON): \\{\\"preset\\"\\:\\"synthetic\\"\\,\\"scale\\"\\:1\\}',
    '',
    '## Metrics',
    '',
    '| Metric | Unit | Baseline | Candidate | Absolute delta | Percent delta |',
    '| --- | --- | ---: | ---: | ---: | ---: |',
    '| Mean frame time | ms | 25 | 10 | -15 | -60% |',
    '| Median frame time | ms | 25 | 10 | -15 | -60% |',
    '| P95 frame time (nearest rank) | ms | 40 | 10 | -30 | -75% |',
    '| Average FPS (1000 / mean) | FPS | 40 | 100 | 60 | 150% |',
    '',
    UNITS_NOTE,
    '',
    '## Limitations',
    '',
    `- ${LIMITATION}`,
    `- ${NO_ACCEPTANCE}`,
    '',
  ].join('\n');
  assert.equal(markdown, expected);
});

test('markdown shows counts and hashes for different sample counts', () => {
  const { markdown } = createBenchmarkReport(
    runJson({ frameTimesMs: [20, 20] }),
    runJson({ runId: 'candidate1', frameTimesMs: [10, 10, 10] }),
  );
  assert.ok(markdown.includes(`| Baseline | baseline1 | ${COMMIT_A} | 2 | ${sha256('[20,20]')} |`));
  assert.ok(markdown.includes(`| Candidate | candidate1 | ${COMMIT_A} | 3 | ${sha256('[10,10,10]')} |`));
  assert.ok(markdown.includes('| Mean frame time | ms | 20 | 10 | -10 | -50% |'));
  assert.ok(markdown.includes('| Average FPS (1000 / mean) | FPS | 50 | 100 | 50 | 100% |'));
});

test('json keeps full precision while markdown rounds for display', () => {
  const { json, markdown } = createBenchmarkReport(
    runJson({ frameTimesMs: [3, 3, 4] }),
    runJson({ runId: 'candidate1', frameTimesMs: [1] }),
  );
  assert.equal(json.comparison.baseline.summary.meanMs, 10 / 3);
  assert.equal(json.comparison.deltas.meanMs.absolute, 1 - 10 / 3);
  assert.equal(json.comparison.deltas.meanMs.percent, (100 * (1 - 10 / 3)) / (10 / 3));
  assert.ok(markdown.includes('| Mean frame time | ms | 3.33333333333 | 1 | -2.33333333333 |'));
});

test('repeated calls are deterministic and independent', () => {
  const first = createBenchmarkReport(BASELINE, CANDIDATE);
  const second = createBenchmarkReport(BASELINE, CANDIDATE);
  assert.equal(first.markdown, second.markdown);
  assert.deepEqual(first.json, second.json);
  assert.equal(JSON.stringify(first.json), JSON.stringify(second.json));
  assert.notEqual(first.json, second.json);

  first.json.comparison.deltas.meanMs.absolute = 0;
  first.json.limitations.push('tampered');
  const third = createBenchmarkReport(BASELINE, CANDIDATE);
  assert.equal(third.json.comparison.deltas.meanMs.absolute, -15);
  assert.equal(third.json.limitations.length, 2);
  assert.equal(third.markdown, second.markdown);
});

test('does not mutate inputs', () => {
  const baseline = `${BASELINE}`;
  const candidate = `${CANDIDATE}`;
  createBenchmarkReport(baseline, candidate);
  assert.equal(baseline, BASELINE);
  assert.equal(candidate, CANDIDATE);
});

test('escapes hostile labels and settings', () => {
  const hardware = '# H [x](http://e) ```c``` a|b <i>';
  const scene = '> quote * star _u_ ~~s~~ ![img](x) &amp; www.example.com';
  const settings = {
    '# k': '[l](javascript:alert(1))',
    fence: '```',
    pipe: 'a|b',
    nl: 'x\ny',
    ls: `a${LINE_SEPARATOR}b`,
  };
  const { json, markdown } = createBenchmarkReport(
    runJson({ hardware, scene, settings }),
    runJson({ runId: 'candidate1', hardware, scene, settings }),
  );

  assert.equal(json.comparison.conditions.hardware, hardware);
  assert.equal(json.comparison.conditions.scene, scene);

  const lines = markdown.split('\n');
  assert.deepEqual(
    lines.filter((line) => line.startsWith('#')),
    [
      '# Frame-time benchmark comparison',
      '## Runs',
      '## Matching conditions',
      '## Metrics',
      '## Limitations',
    ],
  );
  assert.equal(lines.filter((line) => line.startsWith('|')).length, 10);
  assert.ok(!markdown.includes('```'));
  assert.ok(!markdown.includes(LINE_SEPARATOR));
  // The separator is rendered as visible text: escaped backslash, "u2028".
  assert.ok(markdown.includes('a' + '\\\\' + 'u2028b'));
  assert.ok(lines.includes('- Hardware: \\# H \\[x\\]\\(http\\:\\/\\/e\\) \\`\\`\\`c\\`\\`\\` a\\|b \\<i\\>'));

  for (const prefix of ['- Platform: ', '- Hardware: ', '- Scene: ', '- Settings (canonical JSON): ']) {
    const line = lines.find((candidate) => candidate.startsWith(prefix));
    assert.ok(line, `missing line ${prefix}`);
    assert.ok(!hasUnescapedAsciiPunctuation(line.slice(prefix.length)), `unescaped text in ${prefix}`);
  }
});

test('propagates mismatched and invalid input errors', () => {
  assert.throws(
    () => createBenchmarkReport(BASELINE, runJson({ scene: 'Synthetic Atrium' })),
    (error) => error instanceof RangeError && error.message.includes('scene'),
  );
  assert.throws(
    () => createBenchmarkReport(BASELINE, runJson({ settings: { preset: 'other' } })),
    (error) => error instanceof RangeError && error.message.includes('settingsKey'),
  );
  assert.throws(() => createBenchmarkReport('{', CANDIDATE), SyntaxError);
  assert.throws(() => createBenchmarkReport(BASELINE, null), TypeError);
  assert.throws(() => createBenchmarkReport(BASELINE, runJson({ frameTimesMs: [] })), RangeError);
  assert.throws(() => createBenchmarkReport(runJson({ frameTimesMs: [5e-324] }), CANDIDATE), RangeError);
});

test('acceptance detector separates negative scope from affirmative claims', () => {
  for (const negative of [
    '- Gameplay accepted: no',
    '- Feature accepted: no',
    '- Optimized build claimed: no',
    'Gameplay is not accepted.',
    LIMITATION,
    NO_ACCEPTANCE,
    UNITS_NOTE,
  ]) {
    assert.equal(claimsAcceptance(negative), false, negative);
  }
  for (const affirmative of [
    '- Gameplay accepted: yes',
    '- Feature accepted: TRUE',
    '- Optimized build claimed: yes',
    'Gameplay is accepted.',
    'Features are verified.',
    'The optimized build is confirmed.',
    'This report accepts the gameplay.',
    'Codex confirmed an optimized build.',
  ]) {
    assert.equal(claimsAcceptance(affirmative), true, affirmative);
  }

  // Flipping any real negative label to an affirmative one is detected.
  const { markdown } = createBenchmarkReport(BASELINE, CANDIDATE);
  for (const [negative, affirmative] of [
    ['Gameplay accepted: no', 'Gameplay accepted: yes'],
    ['Feature accepted: no', 'Feature accepted: yes'],
    ['Optimized build claimed: no', 'Optimized build claimed: yes'],
  ]) {
    const tampered = markdown.replace(negative, affirmative);
    assert.notEqual(tampered, markdown);
    assert.equal(claimsAcceptance(tampered), true, affirmative);
  }
});

test('never claims gameplay, feature or optimization acceptance', () => {
  for (const [baseline, candidate] of [
    [BASELINE, CANDIDATE],
    [CANDIDATE, BASELINE],
    [BASELINE, BASELINE],
  ]) {
    const { json, markdown } = createBenchmarkReport(baseline, candidate);
    assert.deepEqual(json.scope, {
      measurement: 'supplied frame-time samples only',
      gameplayAccepted: false,
      featureAccepted: false,
      optimizedBuildClaimed: false,
    });
    assert.ok(json.limitations.includes(LIMITATION));
    assert.ok(json.limitations.includes(NO_ACCEPTANCE));

    const lines = markdown.split('\n');
    for (const label of ['- Gameplay accepted: no', '- Feature accepted: no', '- Optimized build claimed: no']) {
      assert.ok(lines.includes(label), label);
    }
    assert.ok(lines.includes(`- ${LIMITATION}`));
    assert.ok(lines.includes(`- ${NO_ACCEPTANCE}`));

    assert.equal(claimsAcceptance(markdown), false);
    assert.equal(claimsAcceptance(JSON.stringify(json)), false);
  }
});
