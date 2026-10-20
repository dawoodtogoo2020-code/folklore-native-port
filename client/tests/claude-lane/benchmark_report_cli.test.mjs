import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { Buffer } from 'node:buffer';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { dirname, isAbsolute, join, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { createBenchmarkReport } from '../../scripts/claude-lane/benchmark_report.mjs';
import { USAGE, runBenchmarkReportCli } from '../../scripts/claude-lane/benchmark_report_cli.mjs';

const CLI_PATH = fileURLToPath(new URL('../../scripts/claude-lane/benchmark_report_cli.mjs', import.meta.url));
const CHILD_TIMEOUT_MS = 30_000;
const MAX_INPUT_BYTES = 8 * 1024 * 1024;
const FIXTURE_PREFIX = 'benchmark-report-cli-';

const COMMIT_A = '0123456789abcdef0123456789abcdef01234567';
const COMMIT_B = 'fedcba9876543210fedcba9876543210fedcba98';
const E_ACUTE = String.fromCodePoint(0xe9);
const LIMITATION =
  'Caller-supplied metadata and frame-time samples do not prove hardware identity, actual execution, feature wiring, stability or an optimized build.';

const sha256 = (data) => createHash('sha256').update(data).digest('hex');

let rootReal;
let workDir;
let fixtureCounter = 0;
let BASELINE_PATH;
let CANDIDATE_PATH;
let MISSING_PATH;

function rootPrefix() {
  return rootReal.endsWith(sep) ? rootReal : rootReal + sep;
}

function assertContained(dir) {
  const real = realpathSync(dir);
  assert.equal(dirname(real), rootReal, 'fixture directory must be a direct child of FOLKLORE_TEST_OUTPUT_ROOT');
  assert.ok(real.startsWith(rootPrefix()), 'fixture directory escaped FOLKLORE_TEST_OUTPUT_ROOT');
  assert.ok(real.slice(rootPrefix().length).startsWith(FIXTURE_PREFIX), 'unexpected fixture directory name');
}

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

const CANDIDATE_OVERRIDES = { runId: 'candidate1', commit: COMMIT_B, frameTimesMs: [10, 10, 10, 10] };

function fixture(contents, name = 'run') {
  fixtureCounter += 1;
  const path = join(workDir, `${String(fixtureCounter).padStart(3, '0')}-${name}.json`);
  writeFileSync(path, contents, { flag: 'wx' });
  return path;
}

before(() => {
  const root = process.env.FOLKLORE_TEST_OUTPUT_ROOT;
  if (typeof root !== 'string' || root === '' || !isAbsolute(root)) {
    throw new Error(
      'FOLKLORE_TEST_OUTPUT_ROOT must be set to an existing absolute directory; refusing to create fixtures anywhere else',
    );
  }
  let stats;
  try {
    stats = statSync(root);
  } catch {
    throw new Error('FOLKLORE_TEST_OUTPUT_ROOT does not exist');
  }
  if (!stats.isDirectory()) {
    throw new Error('FOLKLORE_TEST_OUTPUT_ROOT is not a directory');
  }
  rootReal = realpathSync(root);
  workDir = mkdtempSync(join(rootReal, FIXTURE_PREFIX));
  assertContained(workDir);

  BASELINE_PATH = fixture(runJson(), 'baseline');
  CANDIDATE_PATH = fixture(runJson(CANDIDATE_OVERRIDES), 'candidate');
  MISSING_PATH = join(workDir, 'does-not-exist.json');
});

after(() => {
  if (workDir === undefined) {
    return;
  }
  assertContained(workDir);
  rmSync(workDir, { recursive: true });
});

function runCli(args) {
  const result = spawnSync(process.execPath, [CLI_PATH, ...args], {
    encoding: 'utf8',
    timeout: CHILD_TIMEOUT_MS,
    windowsHide: true,
    maxBuffer: 64 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  assert.equal(result.error, undefined, `child process failed to run or timed out: ${result.error && result.error.code}`);
  assert.equal(result.signal, null, 'child process was killed by a signal');
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

async function runInProcess(args) {
  const out = [];
  const err = [];
  const io = {
    stdout: { write: (text) => { out.push(String(text)); return true; } },
    stderr: { write: (text) => { err.push(String(text)); return true; } },
  };
  const status = await runBenchmarkReportCli(args, io);
  return { status, stdout: out.join(''), stderr: err.join('') };
}

function assertFailure(result, expectedStatus, pattern) {
  assert.equal(result.status, expectedStatus, `unexpected status; stderr: ${result.stderr}`);
  assert.equal(result.stdout, '');
  assert.match(result.stderr, /^benchmark-report: [^\n]+\n$/, 'stderr must be exactly one line');
  assert.match(result.stderr, pattern);
  assert.doesNotMatch(result.stderr, /Error:/);
  assert.doesNotMatch(result.stderr, /\bat \S+:\d+/);
}

function assertUsageError(result, pattern) {
  assertFailure(result, 2, pattern);
  assert.match(result.stderr, /^benchmark-report: usage error: /);
}

function expectedJson(baselineText, candidateText) {
  return `${JSON.stringify(createBenchmarkReport(baselineText, candidateText).json, null, 2)}\n`;
}

function snapshotWorkDir() {
  return readdirSync(workDir)
    .sort()
    .map((name) => {
      const path = join(workDir, name);
      const stats = statSync(path);
      return {
        name,
        isFile: stats.isFile(),
        size: stats.size,
        mtimeMs: stats.mtimeMs,
        digest: stats.isFile() ? sha256(readFileSync(path)) : null,
      };
    });
}

function collectNumbers(value, found = []) {
  if (typeof value === 'number') {
    found.push(value);
  } else if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) {
      collectNumbers(child, found);
    }
  }
  return found;
}

// --- import, help ---

test('importing the CLI module is inert', () => {
  const code = `const m = await import(${JSON.stringify(pathToFileURL(CLI_PATH).href)}); process.stdout.write(typeof m.runBenchmarkReportCli);`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', code, '--', '--help'], {
    encoding: 'utf8',
    timeout: CHILD_TIMEOUT_MS,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0);
  assert.equal(result.stdout, 'function');
  assert.equal(result.stderr, '');
  assert.equal(typeof runBenchmarkReportCli, 'function');
  assert.equal(typeof USAGE, 'string');
});

test('--help alone prints usage and exits 0', async () => {
  const child = runCli(['--help']);
  assert.equal(child.status, 0);
  assert.equal(child.stdout, USAGE);
  assert.equal(child.stderr, '');
  assert.ok(USAGE.startsWith('Usage: node scripts/claude-lane/benchmark_report_cli.mjs --baseline ABSOLUTE_FILE'));

  const inProcess = await runInProcess(['--help']);
  assert.deepEqual(inProcess, { status: 0, stdout: USAGE, stderr: '' });
});

test('--help combined with other arguments is a usage error', async () => {
  for (const args of [
    ['--help', '--help'],
    ['--help', '--format', 'json'],
    ['--format', 'json', '--help'],
    ['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH, '--help'],
    ['--baseline', '--help'],
  ]) {
    assertUsageError(await runInProcess(args), /--help cannot be combined/);
  }
  assertUsageError(runCli(['--help', '--format', 'json']), /--help cannot be combined/);
});

// --- argument errors ---

test('rejects missing required arguments', async () => {
  assertUsageError(await runInProcess([]), /missing required --baseline/);
  assertUsageError(await runInProcess(['--candidate', CANDIDATE_PATH]), /missing required --baseline/);
  assertUsageError(await runInProcess(['--baseline', BASELINE_PATH]), /missing required --candidate/);
  assertUsageError(await runInProcess(['--format', 'json']), /missing required --baseline/);
});

test('rejects unknown and positional arguments', async () => {
  const valid = ['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH];
  for (const args of [
    ['--bogus', 'x', ...valid],
    [...valid, 'extra'],
    ['extra', ...valid],
    ['-b', BASELINE_PATH, '--candidate', CANDIDATE_PATH],
    ['--BASELINE', BASELINE_PATH, '--candidate', CANDIDATE_PATH],
    [`--baseline=${BASELINE_PATH}`, '--candidate', CANDIDATE_PATH],
    [...valid, '--stdin', 'yes'],
  ]) {
    assertUsageError(await runInProcess(args), /unknown argument/);
  }
});

test('rejects duplicate arguments before reading any file', async () => {
  // MISSING_PATH would give exit 1 if it were read; exit 2 proves parsing comes first.
  assertUsageError(
    await runInProcess(['--baseline', MISSING_PATH, '--baseline', MISSING_PATH, '--candidate', MISSING_PATH]),
    /duplicate --baseline/,
  );
  assertUsageError(
    await runInProcess(['--baseline', MISSING_PATH, '--candidate', MISSING_PATH, '--candidate', MISSING_PATH]),
    /duplicate --candidate/,
  );
  assertUsageError(
    await runInProcess(['--format', 'json', '--baseline', MISSING_PATH, '--candidate', MISSING_PATH, '--format', 'markdown']),
    /duplicate --format/,
  );
});

test('rejects missing and empty values', async () => {
  assertUsageError(await runInProcess(['--baseline', BASELINE_PATH, '--candidate']), /missing value for --candidate/);
  assertUsageError(await runInProcess(['--baseline', '--candidate', CANDIDATE_PATH]), /missing value for --baseline/);
  assertUsageError(
    await runInProcess(['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH, '--format']),
    /missing value for --format/,
  );
  assertUsageError(await runInProcess(['--baseline', '', '--candidate', CANDIDATE_PATH]), /empty value for --baseline/);
  assertUsageError(await runInProcess(['--baseline', BASELINE_PATH, '--candidate', '']), /empty value for --candidate/);
  assertUsageError(
    await runInProcess(['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH, '--format', '']),
    /empty value for --format/,
  );
});

test('rejects relative and NUL-containing paths', async () => {
  for (const relative of ['run.json', './run.json', '../run.json', join('sub', 'run.json')]) {
    assertUsageError(
      await runInProcess(['--baseline', relative, '--candidate', CANDIDATE_PATH]),
      /--baseline must be an absolute path/,
    );
    assertUsageError(
      await runInProcess(['--baseline', BASELINE_PATH, '--candidate', relative]),
      /--candidate must be an absolute path/,
    );
  }
  assertUsageError(
    await runInProcess(['--baseline', `${BASELINE_PATH}\0`, '--candidate', CANDIDATE_PATH]),
    /NUL character/,
  );
});

test('rejects invalid formats', async () => {
  for (const format of ['html', 'JSON', 'md', ' json', 'json ']) {
    assertUsageError(
      await runInProcess(['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH, '--format', format]),
      /--format must be json or markdown/,
    );
  }
});

test('rejects non-string arguments in-process', async () => {
  for (const args of [[1], ['--baseline', null, '--candidate', CANDIDATE_PATH], 'not-an-array', undefined]) {
    assertUsageError(await runInProcess(args), /arguments must be strings/);
  }
});

test('the real CLI process exits 2 on usage errors', () => {
  assertUsageError(runCli([]), /missing required --baseline/);
  assertUsageError(runCli(['--bogus']), /unknown argument --bogus/);
  assertUsageError(runCli(['--baseline', 'run.json', '--candidate', CANDIDATE_PATH]), /absolute path/);
  assertUsageError(
    runCli(['--baseline', MISSING_PATH, '--baseline', MISSING_PATH, '--candidate', MISSING_PATH]),
    /duplicate --baseline/,
  );
});

// --- success ---

test('JSON output matches the library exactly', async () => {
  const expected = expectedJson(runJson(), runJson(CANDIDATE_OVERRIDES));
  for (const args of [
    ['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH],
    ['--format', 'json', '--candidate', CANDIDATE_PATH, '--baseline', BASELINE_PATH],
  ]) {
    const child = runCli(args);
    assert.equal(child.status, 0, child.stderr);
    assert.equal(child.stderr, '');
    assert.equal(child.stdout, expected);
    assert.ok(child.stdout.endsWith('}\n'));
    assert.ok(!child.stdout.endsWith('\n\n'));
    assert.deepEqual(await runInProcess(args), { status: 0, stdout: expected, stderr: '' });
  }
});

test('Markdown output matches the library exactly', async () => {
  const expected = createBenchmarkReport(runJson(), runJson(CANDIDATE_OVERRIDES)).markdown;
  const args = ['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH, '--format', 'markdown'];
  const child = runCli(args);
  assert.equal(child.status, 0, child.stderr);
  assert.equal(child.stderr, '');
  assert.equal(child.stdout, expected);
  assert.ok(child.stdout.endsWith('\n'));
  assert.ok(!child.stdout.endsWith('\n\n'));
  assert.deepEqual(await runInProcess(args), { status: 0, stdout: expected, stderr: '' });
});

test('reports honest scope flags, counts, hashes and finite metrics', () => {
  const child = runCli(['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH]);
  assert.equal(child.status, 0, child.stderr);
  const report = JSON.parse(child.stdout);
  assert.equal(report.schemaVersion, 1);
  assert.deepEqual(report.scope, {
    measurement: 'supplied frame-time samples only',
    gameplayAccepted: false,
    featureAccepted: false,
    optimizedBuildClaimed: false,
  });
  assert.ok(report.limitations.includes(LIMITATION));
  assert.equal(report.comparison.baseline.sampleCount, 4);
  assert.equal(report.comparison.candidate.sampleCount, 4);
  assert.equal(report.comparison.baseline.sampleSha256, sha256('[40,10,30,20]'));
  assert.equal(report.comparison.candidate.sampleSha256, sha256('[10,10,10,10]'));
  assert.deepEqual(report.comparison.deltas.meanMs, { absolute: -15, percent: -60 });
  const numbers = collectNumbers(report);
  assert.ok(numbers.length > 0);
  assert.ok(numbers.every(Number.isFinite));
  assert.ok(!child.stdout.includes('null'));

  const markdown = runCli(['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH, '--format', 'markdown']);
  const lines = markdown.stdout.split('\n');
  for (const label of ['- Gameplay accepted: no', '- Feature accepted: no', '- Optimized build claimed: no', `- ${LIMITATION}`]) {
    assert.ok(lines.includes(label), label);
  }
});

test('allows the same file for both roles', () => {
  const child = runCli(['--baseline', BASELINE_PATH, '--candidate', BASELINE_PATH]);
  assert.equal(child.status, 0, child.stderr);
  assert.equal(child.stderr, '');
  assert.equal(child.stdout, expectedJson(runJson(), runJson()));
  const report = JSON.parse(child.stdout);
  assert.deepEqual(report.comparison.baseline, report.comparison.candidate);
  for (const metric of ['meanMs', 'medianMs', 'p95Ms', 'averageFps']) {
    assert.deepEqual(report.comparison.deltas[metric], { absolute: 0, percent: 0 });
  }
});

test('accepts valid multi-byte UTF-8 labels', () => {
  const hardware = `Synthetic GPU ${E_ACUTE}`;
  const baseline = runJson({ hardware });
  const candidate = runJson({ ...CANDIDATE_OVERRIDES, hardware });
  const child = runCli(['--baseline', fixture(baseline, 'utf8-base'), '--candidate', fixture(candidate, 'utf8-cand')]);
  assert.equal(child.status, 0, child.stderr);
  assert.equal(child.stdout, expectedJson(baseline, candidate));
  assert.equal(JSON.parse(child.stdout).comparison.conditions.hardware, hardware);
});

// --- input failures ---

test('fails for missing input files', () => {
  assertFailure(runCli(['--baseline', MISSING_PATH, '--candidate', CANDIDATE_PATH]), 1, /baseline input does not exist/);
  assertFailure(runCli(['--baseline', BASELINE_PATH, '--candidate', MISSING_PATH]), 1, /candidate input does not exist/);
});

test('rejects directories before reading them', () => {
  const directory = join(workDir, 'a-directory');
  mkdirSync(directory);
  assertFailure(runCli(['--baseline', directory, '--candidate', CANDIDATE_PATH]), 1, /baseline input is not a regular file/);
  assertFailure(runCli(['--baseline', BASELINE_PATH, '--candidate', directory]), 1, /candidate input is not a regular file/);
});

test('enforces the 8 MiB input limit', () => {
  const base = runJson();
  const atLimit = fixture(base + ' '.repeat(MAX_INPUT_BYTES - Buffer.byteLength(base)), 'at-limit');
  assert.equal(statSync(atLimit).size, MAX_INPUT_BYTES);
  const ok = runCli(['--baseline', atLimit, '--candidate', CANDIDATE_PATH]);
  assert.equal(ok.status, 0, ok.stderr);
  assert.equal(ok.stderr, '');

  const overLimit = fixture(base + ' '.repeat(MAX_INPUT_BYTES + 1 - Buffer.byteLength(base)), 'over-limit');
  assert.equal(statSync(overLimit).size, MAX_INPUT_BYTES + 1);
  assertFailure(runCli(['--baseline', overLimit, '--candidate', CANDIDATE_PATH]), 1, /baseline input exceeds the 8388608-byte size limit/);
  assertFailure(runCli(['--baseline', BASELINE_PATH, '--candidate', overLimit]), 1, /candidate input exceeds the 8388608-byte size limit/);
});

test('rejects invalid UTF-8', () => {
  const loneByte = fixture(Buffer.from([0x7b, 0xff, 0x7d]), 'lone-byte');
  assertFailure(runCli(['--baseline', loneByte, '--candidate', CANDIDATE_PATH]), 1, /baseline input is not valid UTF-8/);

  const text = runJson(CANDIDATE_OVERRIDES);
  const marker = text.indexOf('Synthetic Rig');
  const corrupted = Buffer.concat([
    Buffer.from(text.slice(0, marker)),
    Buffer.from([0xc3, 0x28]),
    Buffer.from(text.slice(marker)),
  ]);
  const badCandidate = fixture(corrupted, 'bad-utf8');
  assertFailure(runCli(['--baseline', BASELINE_PATH, '--candidate', badCandidate]), 1, /candidate input is not valid UTF-8/);
});

test('rejects malformed JSON without echoing input', () => {
  const malformed = fixture('{"hardware":"RAW-MARKER-0001"', 'malformed');
  const result = runCli(['--baseline', malformed, '--candidate', CANDIDATE_PATH]);
  assertFailure(result, 1, /baseline input is not valid JSON/);
  assert.ok(!result.stderr.includes('RAW-MARKER'));

  const empty = fixture('', 'empty');
  assertFailure(runCli(['--baseline', BASELINE_PATH, '--candidate', empty]), 1, /candidate input is not valid JSON/);
});

test('rejects schema violations without echoing input', () => {
  const badVersion = fixture(runJson({ ...CANDIDATE_OVERRIDES, schemaVersion: 2 }), 'bad-version');
  assertFailure(
    runCli(['--baseline', BASELINE_PATH, '--candidate', badVersion]),
    1,
    /candidate input does not match the benchmark run schema/,
  );

  const badRunId = fixture(runJson({ runId: 'RAW-MARKER-0002 has spaces' }), 'bad-run-id');
  const result = runCli(['--baseline', badRunId, '--candidate', CANDIDATE_PATH]);
  assertFailure(result, 1, /baseline input does not match the benchmark run schema/);
  assert.ok(!result.stderr.includes('RAW-MARKER'));

  const extraField = fixture(JSON.stringify({ ...JSON.parse(runJson()), extra: 1 }), 'extra-field');
  assertFailure(runCli(['--baseline', extraField, '--candidate', CANDIDATE_PATH]), 1, /baseline input does not match/);
});

test('rejects invalid sample counts', () => {
  const emptyFrames = fixture(runJson({ frameTimesMs: [] }), 'empty-frames');
  assertFailure(
    runCli(['--baseline', emptyFrames, '--candidate', CANDIDATE_PATH]),
    1,
    /baseline input violates a size or sample-count limit/,
  );
});

test('rejects each condition mismatch', () => {
  for (const [field, overrides] of [
    ['platform', { platform: 'windows-x64' }],
    ['hardware', { hardware: 'Synthetic Rig B' }],
    ['scene', { scene: 'Synthetic Atrium' }],
    ['settingsKey', { settings: { preset: 'synthetic', scale: 2 } }],
  ]) {
    const candidate = fixture(runJson({ ...CANDIDATE_OVERRIDES, ...overrides }), `mismatch-${field}`);
    assertFailure(
      runCli(['--baseline', BASELINE_PATH, '--candidate', candidate]),
      1,
      new RegExp(`condition mismatch: ${field} differs between baseline and candidate`),
    );
  }
});

test('rejects nonfinite derived metrics', () => {
  const tiny = fixture(runJson({ frameTimesMs: [5e-324] }), 'tiny');
  assertFailure(runCli(['--baseline', tiny, '--candidate', CANDIDATE_PATH]), 1, /comparison failed: a derived metric is not finite/);

  const small = fixture(runJson({ frameTimesMs: [1e-300] }), 'small');
  const huge = fixture(runJson({ ...CANDIDATE_OVERRIDES, frameTimesMs: [1e308] }), 'huge');
  assertFailure(runCli(['--baseline', small, '--candidate', huge]), 1, /comparison failed: a derived metric is not finite/);

  const overflow = fixture(runJson({ frameTimesMs: [1.7e308, 1.7e308] }), 'overflow');
  assertFailure(runCli(['--baseline', overflow, '--candidate', CANDIDATE_PATH]), 1, /not finite/);
});

test('never writes, creates or changes files', async () => {
  const before = snapshotWorkDir();
  runCli(['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH]);
  runCli(['--baseline', BASELINE_PATH, '--candidate', CANDIDATE_PATH, '--format', 'markdown']);
  runCli(['--baseline', BASELINE_PATH, '--candidate', MISSING_PATH]);
  runCli(['--baseline', BASELINE_PATH, '--candidate', join(workDir, 'a-directory')]);
  await runInProcess(['--baseline', BASELINE_PATH, '--candidate', BASELINE_PATH]);
  assert.deepEqual(snapshotWorkDir(), before);
});

test('refuses a POSIX FIFO without blocking', { skip: process.platform === 'win32' ? 'POSIX FIFOs are not available on Windows' : false }, (t) => {
  const fifo = join(workDir, 'input.fifo');
  const made = spawnSync('mkfifo', [fifo], { timeout: CHILD_TIMEOUT_MS, stdio: 'ignore' });
  if (made.error || made.status !== 0) {
    t.skip('mkfifo is not available');
    return;
  }
  // runCli asserts the child neither timed out nor was killed.
  assertFailure(runCli(['--baseline', fifo, '--candidate', CANDIDATE_PATH]), 1, /baseline input is not a regular file/);
  assertFailure(runCli(['--baseline', BASELINE_PATH, '--candidate', fifo]), 1, /candidate input is not a regular file/);
});
