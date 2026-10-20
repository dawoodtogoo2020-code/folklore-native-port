/**
 * Benchmark-run parsing and settings canonicalization for synthetic
 * frame-time benchmark records.
 *
 * Dependency-free ESM. No I/O, clocks or environment access.
 */

import { Buffer } from 'node:buffer';
import { createHash } from 'node:crypto';

const MAX_JSON_BYTES = 8 * 1024 * 1024;
const MAX_FRAME_SAMPLES = 1_000_000;
const MAX_SETTINGS_DEPTH = 16;
const MAX_LABEL_CODE_POINTS = 160;

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
const PLATFORMS = new Set(['windows-x64', 'linux-x64', 'steamdeck-x64', 'android-arm64']);
const POISON_KEYS = ['__proto__', 'constructor', 'prototype'];
const RUN_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const COMMIT_PATTERN = /^[0-9A-Fa-f]{40}$/;

const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

// Rejects ASCII control characters, DEL, and the Unicode NEL, line separator
// and paragraph separator, so labels stay on a single line. Checked by code
// unit so the source holds no literal or escaped separator characters.
function hasForbiddenLabelChar(text) {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code <= 0x1f || code === 0x7f || code === 0x85 || code === 0x2028 || code === 0x2029) {
      return true;
    }
  }
  return false;
}

function isPlainObject(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return false;
  }
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

// Inspects own properties only, enumerable or not. Inherited members such as
// Array.prototype.constructor are not considered, and no getters run.
function assertNoForbiddenOwnKeys(value, path) {
  if (Object.getOwnPropertySymbols(value).length > 0) {
    throw new TypeError(`${path} must not have symbol keys`);
  }
  for (const key of POISON_KEYS) {
    if (hasOwn(value, key)) {
      throw new TypeError(`${path}[${JSON.stringify(key)}] uses a forbidden key`);
    }
  }
}

function encodeSettingsValue(value, depth, ancestors, path) {
  if (value === null) {
    return 'null';
  }
  switch (typeof value) {
    case 'string':
      return JSON.stringify(value);
    case 'boolean':
      return value ? 'true' : 'false';
    case 'number':
      if (!Number.isFinite(value)) {
        throw new TypeError(`${path} must be a finite number`);
      }
      // JSON.stringify normalizes -0 to 0.
      return JSON.stringify(value);
    case 'object':
      break;
    default:
      throw new TypeError(`${path} has unsupported type ${typeof value}`);
  }

  if (depth > MAX_SETTINGS_DEPTH) {
    throw new TypeError(`${path} exceeds maximum settings nesting depth ${MAX_SETTINGS_DEPTH}`);
  }
  if (ancestors.has(value)) {
    throw new TypeError(`${path} contains a cycle`);
  }

  ancestors.add(value);
  let encoded;
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== Array.prototype) {
      throw new TypeError(`${path} must be a plain array`);
    }
    assertNoForbiddenOwnKeys(value, path);
    const parts = [];
    for (let i = 0; i < value.length; i += 1) {
      if (!hasOwn(value, i)) {
        throw new TypeError(`${path}[${i}] is a hole`);
      }
      parts.push(encodeSettingsValue(value[i], depth + 1, ancestors, `${path}[${i}]`));
    }
    encoded = `[${parts.join(',')}]`;
  } else {
    if (!isPlainObject(value)) {
      throw new TypeError(`${path} must be a plain object`);
    }
    assertNoForbiddenOwnKeys(value, path);
    const parts = [];
    for (const key of Object.keys(value).sort()) {
      const keyPath = `${path}[${JSON.stringify(key)}]`;
      parts.push(`${JSON.stringify(key)}:${encodeSettingsValue(value[key], depth + 1, ancestors, keyPath)}`);
    }
    encoded = `{${parts.join(',')}}`;
  }
  // Only ancestors count as cycles; shared acyclic references are allowed.
  ancestors.delete(value);
  return encoded;
}

/**
 * Return compact canonical JSON for a settings object: object keys sorted
 * recursively, array order preserved, -0 written as 0.
 *
 * @param {object} value Root plain object (not an array or null).
 * @returns {string}
 * @throws {TypeError} For any unsupported, cyclic, sparse, poisoned,
 *   symbol-keyed, nonplain or too deeply nested value.
 */
export function canonicalSettings(value) {
  if (!isPlainObject(value)) {
    throw new TypeError('settings must be a plain object');
  }
  return encodeSettingsValue(value, 1, new Set(), 'settings');
}

function normalizeLabel(value, field) {
  if (typeof value !== 'string') {
    throw new TypeError(`${field} must be a string`);
  }
  if (hasForbiddenLabelChar(value)) {
    throw new TypeError(`${field} must be a single line without control characters`);
  }
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    throw new TypeError(`${field} must not be empty`);
  }
  // Each code point is at most two UTF-16 units, so very long strings can be
  // rejected without spreading them.
  if (
    trimmed.length > MAX_LABEL_CODE_POINTS * 2 ||
    [...trimmed].length > MAX_LABEL_CODE_POINTS
  ) {
    throw new TypeError(`${field} must be at most ${MAX_LABEL_CODE_POINTS} characters`);
  }
  return trimmed;
}

function normalizeFrameTimes(value) {
  if (!Array.isArray(value)) {
    throw new TypeError('frameTimesMs must be an array');
  }
  if (value.length === 0) {
    throw new RangeError('frameTimesMs must not be empty');
  }
  if (value.length > MAX_FRAME_SAMPLES) {
    throw new RangeError(`frameTimesMs must have at most ${MAX_FRAME_SAMPLES} entries`);
  }
  const frames = [];
  for (let i = 0; i < value.length; i += 1) {
    const frame = value[i];
    if (typeof frame !== 'number' || !Number.isFinite(frame) || frame <= 0) {
      throw new TypeError(`frameTimesMs[${i}] must be a positive finite number`);
    }
    frames.push(frame);
  }
  return frames;
}

/**
 * Parse and validate one benchmark-run JSON document.
 *
 * @param {string} jsonText
 * @returns {{schemaVersion: 1, runId: string, commit: string, platform: string,
 *   hardware: string, scene: string, settings: object, frameTimesMs: number[],
 *   settingsKey: string, sampleSha256: string}}
 * @throws {TypeError} For a non-string input or invalid schema, types or fields.
 * @throws {RangeError} For input over 8 MiB UTF-8 or an empty/over-count
 *   frameTimesMs.
 * @throws {SyntaxError} For malformed JSON.
 */
export function parseBenchmarkRun(jsonText) {
  if (typeof jsonText !== 'string') {
    throw new TypeError('jsonText must be a string');
  }
  if (Buffer.byteLength(jsonText, 'utf8') > MAX_JSON_BYTES) {
    throw new RangeError(`jsonText must be at most ${MAX_JSON_BYTES} UTF-8 bytes`);
  }

  const root = JSON.parse(jsonText);
  if (root === null || typeof root !== 'object' || Array.isArray(root)) {
    throw new TypeError('benchmark run must be a JSON object');
  }
  if (
    Object.keys(root).length !== RUN_FIELDS.length ||
    !RUN_FIELDS.every((field) => hasOwn(root, field))
  ) {
    throw new TypeError(`benchmark run must have exactly these fields: ${RUN_FIELDS.join(', ')}`);
  }

  if (root.schemaVersion !== 1) {
    throw new TypeError('schemaVersion must be 1');
  }
  if (typeof root.runId !== 'string' || !RUN_ID_PATTERN.test(root.runId)) {
    throw new TypeError('runId must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}');
  }
  if (typeof root.commit !== 'string' || !COMMIT_PATTERN.test(root.commit)) {
    throw new TypeError('commit must be 40 hexadecimal characters');
  }
  if (typeof root.platform !== 'string' || !PLATFORMS.has(root.platform)) {
    throw new TypeError(`platform must be one of ${[...PLATFORMS].join(', ')}`);
  }
  const hardware = normalizeLabel(root.hardware, 'hardware');
  const scene = normalizeLabel(root.scene, 'scene');
  const settingsKey = canonicalSettings(root.settings);
  const frameTimesMs = normalizeFrameTimes(root.frameTimesMs);

  return {
    schemaVersion: 1,
    runId: root.runId,
    commit: root.commit.toLowerCase(),
    platform: root.platform,
    hardware,
    scene,
    settings: JSON.parse(settingsKey),
    frameTimesMs,
    settingsKey,
    sampleSha256: createHash('sha256').update(JSON.stringify(frameTimesMs)).digest('hex'),
  };
}
