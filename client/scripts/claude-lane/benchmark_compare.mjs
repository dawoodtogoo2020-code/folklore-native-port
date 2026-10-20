/**
 * Compare two synthetic benchmark runs captured under identical conditions.
 *
 * Reports numerical deltas only; it does not judge whether a build is
 * optimized or whether timing is correct. No I/O, clocks or environment
 * access.
 */

import { parseBenchmarkRun } from './benchmark_run.mjs';
import { summarizeFrameTimes } from './frame_time_summary.mjs';

const CONDITION_FIELDS = ['platform', 'hardware', 'scene', 'settingsKey'];
const DELTA_METRICS = ['meanMs', 'medianMs', 'p95Ms', 'averageFps'];

function summarizeRun(run, role) {
  const summary = summarizeFrameTimes(run.frameTimesMs);
  for (const [key, value] of Object.entries(summary)) {
    if (!Number.isFinite(value)) {
      throw new RangeError(`${role} summary ${key} is not finite`);
    }
  }
  return {
    runId: run.runId,
    commit: run.commit,
    sampleCount: run.frameTimesMs.length,
    sampleSha256: run.sampleSha256,
    summary,
  };
}

function finite(value, label) {
  if (!Number.isFinite(value)) {
    throw new RangeError(`${label} is not finite`);
  }
  return value;
}

/**
 * Compare a baseline and a candidate benchmark-run JSON document.
 *
 * @param {string} baselineJson
 * @param {string} candidateJson
 * @returns {{baseline: object, candidate: object, conditions: object, deltas: object}}
 * @throws {TypeError|SyntaxError|RangeError} From parsing either run.
 * @throws {RangeError} If a condition differs (the message names the field)
 *   or any derived summary, delta or percentage is not finite.
 */
export function compareBenchmarkRuns(baselineJson, candidateJson) {
  const baselineRun = parseBenchmarkRun(baselineJson);
  const candidateRun = parseBenchmarkRun(candidateJson);

  for (const field of CONDITION_FIELDS) {
    if (baselineRun[field] !== candidateRun[field]) {
      throw new RangeError(`condition mismatch: ${field} differs between baseline and candidate`);
    }
  }

  const baseline = summarizeRun(baselineRun, 'baseline');
  const candidate = summarizeRun(candidateRun, 'candidate');

  const deltas = {};
  for (const metric of DELTA_METRICS) {
    const before = baseline.summary[metric];
    const after = candidate.summary[metric];
    deltas[metric] = {
      absolute: finite(after - before, `${metric} absolute delta`),
      percent: finite((100 * (after - before)) / before, `${metric} percent delta`),
    };
  }

  return {
    baseline,
    candidate,
    conditions: {
      platform: baselineRun.platform,
      hardware: baselineRun.hardware,
      scene: baselineRun.scene,
      settingsKey: baselineRun.settingsKey,
    },
    deltas,
  };
}
