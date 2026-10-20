// Restricted single-column frame-time parser.
// Format: exact first line `frameTimeMs`, then one unsigned JSON-number token
// per line (milliseconds). LF or CRLF line endings, at most one final ending.
// This is deliberately not a CSV parser.

import { Buffer } from 'node:buffer';

const MAX_BYTES = 8 * 1024 * 1024;
const MAX_SAMPLES = 1000000;
const HEADER = 'frameTimeMs';
const BOM_CODE = 0xfeff;

const BARE_CR = /\r(?!\n)/;
const LINE_BREAK = /\r?\n/;
const NUMBER_PATTERN = /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/;
const ZERO_MANTISSA = /^0(?:\.0+)?(?:[eE]|$)/;
const WHITESPACE = /[ \t]/;
const QUOTE = /["']/;

function formatError(category, line) {
  return new SyntaxError(`frame-time column: ${category} (line ${line})`);
}

function lineAt(text, index) {
  let line = 1;
  let pos = text.indexOf('\n');
  while (pos !== -1 && pos < index) {
    line += 1;
    pos = text.indexOf('\n', pos + 1);
  }
  return line;
}

function classifyToken(token) {
  if (token === '') return 'blank row';
  if (WHITESPACE.test(token)) return 'whitespace';
  if (QUOTE.test(token)) return 'quoted value';
  if (token.includes(',')) return 'extra column';
  if (token.startsWith('-')) return 'negative value';
  return 'malformed number';
}

export function parseFrameTimeColumn(text) {
  if (typeof text !== 'string') {
    throw new TypeError('frame-time column: input must be a string');
  }
  if (Buffer.byteLength(text, 'utf8') > MAX_BYTES) {
    throw new RangeError('frame-time column: input exceeds byte limit');
  }
  if (text.length > 0 && text.charCodeAt(0) === BOM_CODE) {
    throw formatError('byte order mark', 1);
  }
  const bareCr = text.search(BARE_CR);
  if (bareCr !== -1) {
    throw formatError('bare carriage return', lineAt(text, bareCr));
  }

  const lines = text.split(LINE_BREAK);
  if (lines.length > 1 && lines[lines.length - 1] === '') {
    lines.pop();
  }
  if (lines[0] !== HEADER) {
    throw formatError('invalid header', 1);
  }

  const count = lines.length - 1;
  if (count === 0) {
    throw new RangeError('frame-time column: no samples');
  }
  if (count > MAX_SAMPLES) {
    throw new RangeError('frame-time column: too many samples');
  }

  const samples = [];
  for (let i = 1; i < lines.length; i += 1) {
    const token = lines[i];
    const line = i + 1;
    if (!NUMBER_PATTERN.test(token)) {
      throw formatError(classifyToken(token), line);
    }
    const value = Number(token);
    if (!Number.isFinite(value)) {
      throw formatError('overflow', line);
    }
    if (value === 0) {
      throw formatError(ZERO_MANTISSA.test(token) ? 'zero value' : 'underflow', line);
    }
    samples.push(value);
  }
  return samples;
}
