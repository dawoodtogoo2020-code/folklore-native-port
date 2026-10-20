/**
 * Frame-time summary utility for synthetic frame-time samples.
 *
 * Dependency-free ESM. No I/O, clocks or packages.
 */

/**
 * Summarize frame-time samples given in milliseconds.
 *
 * @param {number[]} samples Nonempty Array of positive finite numbers (ms).
 * @returns {{count: number, totalMs: number, meanMs: number, medianMs: number,
 *   p95Ms: number, minMs: number, maxMs: number, averageFps: number}}
 * @throws {TypeError} If samples is not an Array, or any member is a hole,
 *   not a number primitive, nonfinite, zero or negative.
 * @throws {RangeError} If samples is empty or the sum is not finite.
 */
export function summarizeFrameTimes(samples) {
  if (!Array.isArray(samples)) {
    throw new TypeError('samples must be an Array of positive finite numbers');
  }

  const count = samples.length;
  if (count === 0) {
    throw new RangeError('samples must not be empty');
  }

  // Validate and copy in one pass; the input is only read, never written,
  // so frozen arrays are supported.
  const sorted = [];
  let totalMs = 0;
  for (let i = 0; i < count; i += 1) {
    if (!Object.prototype.hasOwnProperty.call(samples, i)) {
      throw new TypeError(`samples[${i}] is a hole`);
    }
    const value = samples[i];
    if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
      throw new TypeError(`samples[${i}] must be a positive finite number`);
    }
    sorted.push(value);
    totalMs += value;
  }

  if (!Number.isFinite(totalMs)) {
    throw new RangeError('sum of samples is not finite');
  }

  sorted.sort((a, b) => a - b);

  const middle = Math.floor(count / 2);
  const medianMs =
    count % 2 === 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle];

  // Nearest-rank p95: index ceil(0.95 * count) - 1. Computed as
  // ceil(95 * count / 100) so the rank is exact integer arithmetic and
  // never hit by binary rounding of 0.95.
  const p95Index = Math.ceil((95 * count) / 100) - 1;

  const meanMs = totalMs / count;

  return {
    count,
    totalMs,
    meanMs,
    medianMs,
    p95Ms: sorted[p95Index],
    minMs: sorted[0],
    maxMs: sorted[count - 1],
    averageFps: 1000 / meanMs,
  };
}
