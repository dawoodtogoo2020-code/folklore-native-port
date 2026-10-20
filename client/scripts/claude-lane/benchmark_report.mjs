/**
 * Deterministic JSON + Markdown report for two synthetic benchmark runs.
 *
 * The report states numerical observations about supplied frame-time
 * samples only. It never accepts gameplay or features, and never claims an
 * optimized build. No locale, timestamps, I/O or app control.
 */

import { compareBenchmarkRuns } from './benchmark_compare.mjs';

const LIMITATIONS = [
  'Caller-supplied metadata and frame-time samples do not prove hardware identity, actual execution, feature wiring, stability or an optimized build.',
  'This report does not accept gameplay, does not accept any feature and does not claim an optimized build.',
];

const METRIC_ROWS = [
  ['meanMs', 'Mean frame time', 'ms'],
  ['medianMs', 'Median frame time', 'ms'],
  ['p95Ms', 'P95 frame time (nearest rank)', 'ms'],
  ['averageFps', 'Average FPS (1000 / mean)', 'FPS'],
];

const UNITS_NOTE =
  'Units: frame times are in milliseconds (ms); FPS is frames per second, derived as 1000 / mean frame time. ' +
  'Absolute delta = candidate - baseline. Percent delta = 100 * (candidate - baseline) / baseline. ' +
  'Markdown values are rounded to 12 significant digits; the JSON keeps full precision. ' +
  'Lower milliseconds or higher FPS are numerical observations only, not acceptance of any build.';

const BACKSLASH = '\\';

// Every ASCII punctuation character may be backslash-escaped in CommonMark
// and GFM, which neutralizes headings, links, emphasis, code fences, HTML,
// autolinks and table pipes in caller-controlled text.
function isAsciiPunctuation(code) {
  return (
    (code >= 0x21 && code <= 0x2f) ||
    (code >= 0x3a && code <= 0x40) ||
    (code >= 0x5b && code <= 0x60) ||
    (code >= 0x7b && code <= 0x7e)
  );
}

// ASCII controls, DEL, NEL, line separator and paragraph separator. Checked
// by code point so the source holds no literal or escaped separators.
function isLineBreaking(code) {
  return code <= 0x1f || code === 0x7f || code === 0x85 || code === 0x2028 || code === 0x2029;
}

function escapeMarkdownText(value) {
  let escaped = '';
  for (const ch of String(value)) {
    const code = ch.codePointAt(0);
    if (isAsciiPunctuation(code)) {
      escaped += BACKSLASH + ch;
    } else if (isLineBreaking(code)) {
      // Rendered as visible text: an escaped backslash, then "u" and 4 hex digits.
      escaped += `${BACKSLASH}${BACKSLASH}u${code.toString(16).padStart(4, '0')}`;
    } else {
      escaped += ch;
    }
  }
  return escaped;
}

function formatNumber(value) {
  // Display rounding only; toPrecision is locale-independent.
  return String(Number(value.toPrecision(12)));
}

function runRow(role, run) {
  return `| ${role} | ${escapeMarkdownText(run.runId)} | ${escapeMarkdownText(run.commit)} | ${run.sampleCount} | ${escapeMarkdownText(run.sampleSha256)} |`;
}

function metricRow(comparison, [metric, label, unit]) {
  const baseline = comparison.baseline.summary[metric];
  const candidate = comparison.candidate.summary[metric];
  const { absolute, percent } = comparison.deltas[metric];
  return `| ${label} | ${unit} | ${formatNumber(baseline)} | ${formatNumber(candidate)} | ${formatNumber(absolute)} | ${formatNumber(percent)}% |`;
}

function renderMarkdown(report) {
  const { comparison, scope, limitations } = report;
  const { conditions } = comparison;
  const lines = [
    '# Frame-time benchmark comparison',
    '',
    `Measurement scope: ${scope.measurement}.`,
    '',
    '- Gameplay accepted: no',
    '- Feature accepted: no',
    '- Optimized build claimed: no',
    '',
    '## Runs',
    '',
    '| Role | Run ID | Commit | Sample count | Sample SHA-256 |',
    '| --- | --- | --- | ---: | --- |',
    runRow('Baseline', comparison.baseline),
    runRow('Candidate', comparison.candidate),
    '',
    '## Matching conditions',
    '',
    `- Platform: ${escapeMarkdownText(conditions.platform)}`,
    `- Hardware: ${escapeMarkdownText(conditions.hardware)}`,
    `- Scene: ${escapeMarkdownText(conditions.scene)}`,
    `- Settings (canonical JSON): ${escapeMarkdownText(conditions.settingsKey)}`,
    '',
    '## Metrics',
    '',
    '| Metric | Unit | Baseline | Candidate | Absolute delta | Percent delta |',
    '| --- | --- | ---: | ---: | ---: | ---: |',
    ...METRIC_ROWS.map((row) => metricRow(comparison, row)),
    '',
    UNITS_NOTE,
    '',
    '## Limitations',
    '',
    ...limitations.map((limitation) => `- ${limitation}`),
    '',
  ];
  return lines.join('\n');
}

/**
 * Build a JSON object and Markdown text describing a baseline/candidate
 * frame-time comparison.
 *
 * @param {string} baselineJson
 * @param {string} candidateJson
 * @returns {{json: object, markdown: string}}
 * @throws Whatever compareBenchmarkRuns throws for invalid or mismatched input.
 */
export function createBenchmarkReport(baselineJson, candidateJson) {
  const comparison = compareBenchmarkRuns(baselineJson, candidateJson);
  const json = {
    schemaVersion: 1,
    comparison,
    scope: {
      measurement: 'supplied frame-time samples only',
      gameplayAccepted: false,
      featureAccepted: false,
      optimizedBuildClaimed: false,
    },
    limitations: [...LIMITATIONS],
  };
  return { json, markdown: renderMarkdown(json) };
}
