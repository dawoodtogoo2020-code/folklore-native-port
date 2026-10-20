import { parseFrameTimeColumn } from './frame_time_column.mjs';
import { parseBenchmarkRun } from './benchmark_run.mjs';

const MAX_RAW_BYTES = 8 * 1024 * 1024;

const METADATA_FIELDS = Object.freeze([
  'schemaVersion',
  'runId',
  'commit',
  'platform',
  'hardware',
  'scene',
  'settings',
]);

const RUN_FIELDS = Object.freeze([...METADATA_FIELDS, 'frameTimesMs']);

// Counts UTF-8 bytes without allocating an encoded copy; stops once over limit.
function exceedsUtf8Bytes(text, limit) {
  if (text.length > limit) {
    return true;
  }
  let bytes = 0;
  for (let index = 0; index < text.length; index += 1) {
    const unit = text.charCodeAt(index);
    if (unit < 0x80) {
      bytes += 1;
    } else if (unit < 0x800) {
      bytes += 2;
    } else if (unit >= 0xd800 && unit <= 0xdbff && index + 1 < text.length) {
      const next = text.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        bytes += 4;
        index += 1;
      } else {
        bytes += 3;
      }
    } else {
      bytes += 3;
    }
    if (bytes > limit) {
      return true;
    }
  }
  return false;
}

// Preserves the error class while replacing every detail with fixed text.
function safeError(error, message) {
  if (error instanceof RangeError) {
    return new RangeError(message);
  }
  if (error instanceof SyntaxError) {
    return new SyntaxError(message);
  }
  return new TypeError(message);
}

function parseMetadata(metadataJson) {
  if (exceedsUtf8Bytes(metadataJson, MAX_RAW_BYTES)) {
    throw new RangeError('benchmark run builder: metadata exceeds byte limit');
  }
  let metadata;
  try {
    metadata = JSON.parse(metadataJson);
  } catch (error) {
    throw safeError(
      error instanceof RangeError ? error : new SyntaxError(),
      'benchmark run builder: metadata is not valid JSON',
    );
  }
  if (
    metadata === null ||
    typeof metadata !== 'object' ||
    Array.isArray(metadata) ||
    Object.getPrototypeOf(metadata) !== Object.prototype
  ) {
    throw new TypeError('benchmark run builder: metadata must be a JSON object');
  }
  const keys = Object.keys(metadata);
  if (
    keys.length !== METADATA_FIELDS.length ||
    !METADATA_FIELDS.every((field) => Object.prototype.hasOwnProperty.call(metadata, field))
  ) {
    throw new TypeError('benchmark run builder: metadata must contain exactly the seven metadata fields');
  }
  if (metadata.schemaVersion !== 1) {
    throw new TypeError('benchmark run builder: metadata schemaVersion must be 1');
  }
  return metadata;
}

export function createBenchmarkRunFromColumn(metadataJson, columnText) {
  if (typeof metadataJson !== 'string') {
    throw new TypeError('benchmark run builder: metadata must be a string');
  }
  if (typeof columnText !== 'string') {
    throw new TypeError('benchmark run builder: frame time column must be a string');
  }

  const metadata = parseMetadata(metadataJson);

  let frameTimesMs;
  try {
    frameTimesMs = parseFrameTimeColumn(columnText);
  } catch (error) {
    throw safeError(error, 'benchmark run builder: frame time column is invalid');
  }

  let rawRun;
  try {
    rawRun = JSON.stringify({
      schemaVersion: metadata.schemaVersion,
      runId: metadata.runId,
      commit: metadata.commit,
      platform: metadata.platform,
      hardware: metadata.hardware,
      scene: metadata.scene,
      settings: metadata.settings,
      frameTimesMs,
    });
  } catch (error) {
    throw safeError(
      error instanceof RangeError ? error : new TypeError(),
      'benchmark run builder: combined run could not be serialized',
    );
  }
  if (exceedsUtf8Bytes(rawRun, MAX_RAW_BYTES)) {
    throw new RangeError('benchmark run builder: combined run exceeds byte limit');
  }

  let run;
  try {
    run = parseBenchmarkRun(rawRun);
  } catch (error) {
    throw safeError(error, 'benchmark run builder: benchmark run validation failed');
  }

  const normalized = {};
  for (const field of RUN_FIELDS) {
    normalized[field] = run[field];
  }
  const output = JSON.stringify(normalized);
  // The required final LF is part of the document passed to parseBenchmarkRun.
  if (exceedsUtf8Bytes(output, MAX_RAW_BYTES - 1)) {
    throw new RangeError('benchmark run builder: normalized run exceeds byte limit');
  }
  return `${output}\n`;
}
