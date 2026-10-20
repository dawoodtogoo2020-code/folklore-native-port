/**
 * Command-line entry point for createBenchmarkReport.
 *
 *   node scripts/claude-lane/benchmark_report_cli.mjs --baseline ABSOLUTE_FILE
 *     --candidate ABSOLUTE_FILE [--format json|markdown]
 *
 * Reads exactly the two named regular files (read-only, bounded, fatal UTF-8)
 * and prints the complete report to stdout. Never writes files, reads stdin,
 * uses the network, runs commands or takes paths from the environment.
 * Importing this module has no side effects; the CLI runs only when this file
 * is the entry script.
 */

import { Buffer } from 'node:buffer';
import { constants as fsConstants } from 'node:fs';
import { open } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

import { parseBenchmarkRun } from './benchmark_run.mjs';
import { createBenchmarkReport } from './benchmark_report.mjs';

const MAX_INPUT_BYTES = 8 * 1024 * 1024;
const MESSAGE_PREFIX = 'benchmark-report';

const EXIT_OK = 0;
const EXIT_FAILURE = 1;
const EXIT_USAGE = 2;

const VALUE_OPTIONS = new Set(['--baseline', '--candidate', '--format']);
const FORMATS = new Set(['json', 'markdown']);
const CONDITION_FIELDS = ['platform', 'hardware', 'scene', 'settingsKey'];
const PRINTABLE_ARGUMENT = /^[\x21-\x7e]{1,40}$/;

// O_NONBLOCK stops an open of a POSIX FIFO from waiting for a writer. It has
// no effect on regular files and is not defined on Windows.
const OPEN_FLAGS =
  fsConstants.O_RDONLY |
  (typeof fsConstants.O_NONBLOCK === 'number' ? fsConstants.O_NONBLOCK : 0);

export const USAGE = [
  'Usage: node scripts/claude-lane/benchmark_report_cli.mjs --baseline ABSOLUTE_FILE --candidate ABSOLUTE_FILE [--format json|markdown]',
  '',
  'Compares two benchmark-run JSON files recorded under identical conditions and prints a report.',
  '  --baseline FILE   absolute path to the baseline run JSON (required)',
  '  --candidate FILE  absolute path to the candidate run JSON (required)',
  '  --format FORMAT   json (default, indented two spaces) or markdown',
  '  --help            print this help; must be the only argument',
  '',
  'The report covers supplied frame-time samples only. It does not prove hardware identity,',
  'actual execution or an optimized build, and never accepts gameplay or features.',
  '',
].join('\n');

class UsageError extends Error {}
class InputError extends Error {}

function parseArguments(argv) {
  if (!Array.isArray(argv) || !argv.every((arg) => typeof arg === 'string')) {
    throw new UsageError('arguments must be strings');
  }
  if (argv.includes('--help')) {
    if (argv.length !== 1) {
      throw new UsageError('--help cannot be combined with other arguments');
    }
    return { help: true };
  }

  const values = new Map();
  for (let i = 0; i < argv.length; i += 2) {
    const flag = argv[i];
    if (!VALUE_OPTIONS.has(flag)) {
      throw new UsageError(
        PRINTABLE_ARGUMENT.test(flag) ? `unknown argument ${flag}` : 'unknown argument',
      );
    }
    if (values.has(flag)) {
      throw new UsageError(`duplicate ${flag}`);
    }
    if (i + 1 >= argv.length || VALUE_OPTIONS.has(argv[i + 1])) {
      throw new UsageError(`missing value for ${flag}`);
    }
    const value = argv[i + 1];
    if (value === '') {
      throw new UsageError(`empty value for ${flag}`);
    }
    values.set(flag, value);
  }

  for (const flag of ['--baseline', '--candidate']) {
    if (!values.has(flag)) {
      throw new UsageError(`missing required ${flag}`);
    }
    const path = values.get(flag);
    if (path.includes('\0')) {
      throw new UsageError(`${flag} path contains a NUL character`);
    }
    if (!isAbsolute(path)) {
      throw new UsageError(`${flag} must be an absolute path`);
    }
  }

  const format = values.has('--format') ? values.get('--format') : 'json';
  if (!FORMATS.has(format)) {
    throw new UsageError('--format must be json or markdown');
  }

  return {
    help: false,
    baseline: values.get('--baseline'),
    candidate: values.get('--candidate'),
    format,
  };
}

function describeOpenFailure(role, error) {
  switch (error && error.code) {
    case 'ENOENT':
    case 'ENOTDIR':
      return `${role} input does not exist`;
    case 'EISDIR':
      return `${role} input is not a regular file`;
    case 'EACCES':
    case 'EPERM':
      return `${role} input is not readable`;
    default:
      return `${role} input could not be opened`;
  }
}

async function readInput(role, path) {
  let handle;
  try {
    try {
      handle = await open(path, OPEN_FLAGS);
    } catch (error) {
      throw new InputError(describeOpenFailure(role, error));
    }

    let stats;
    try {
      stats = await handle.stat();
    } catch {
      throw new InputError(`${role} input could not be inspected`);
    }
    // Checked before any content is read.
    if (!stats.isFile()) {
      throw new InputError(`${role} input is not a regular file`);
    }
    if (stats.size > MAX_INPUT_BYTES) {
      throw new InputError(`${role} input exceeds the ${MAX_INPUT_BYTES}-byte size limit`);
    }

    // Read at most MAX_INPUT_BYTES + 1 bytes, so growth after fstat is caught.
    const buffer = Buffer.alloc(MAX_INPUT_BYTES + 1);
    let total = 0;
    try {
      while (total < buffer.length) {
        const { bytesRead } = await handle.read(buffer, total, buffer.length - total, null);
        if (bytesRead === 0) {
          break;
        }
        total += bytesRead;
      }
    } catch {
      throw new InputError(`${role} input could not be read`);
    }
    if (total > MAX_INPUT_BYTES) {
      throw new InputError(`${role} input exceeds the ${MAX_INPUT_BYTES}-byte size limit`);
    }

    try {
      return new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, total));
    } catch {
      throw new InputError(`${role} input is not valid UTF-8`);
    }
  } finally {
    if (handle !== undefined) {
      try {
        await handle.close();
      } catch {
        // Nothing useful to report; the read result is already decided.
      }
    }
  }
}

// Validates one role on its own so failures can name the role. Messages are
// fixed text and never echo input content.
function validateRun(role, text) {
  try {
    parseBenchmarkRun(text);
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new InputError(`${role} input is not valid JSON`);
    }
    if (error instanceof RangeError) {
      throw new InputError(`${role} input violates a size or sample-count limit`);
    }
    if (error instanceof TypeError) {
      throw new InputError(`${role} input does not match the benchmark run schema`);
    }
    throw error;
  }
}

function buildReport(baselineText, candidateText) {
  try {
    return createBenchmarkReport(baselineText, candidateText);
  } catch (error) {
    if (error instanceof RangeError) {
      const mismatch = /^condition mismatch: (\w+) /.exec(error.message);
      if (mismatch && CONDITION_FIELDS.includes(mismatch[1])) {
        throw new InputError(`condition mismatch: ${mismatch[1]} differs between baseline and candidate`);
      }
      throw new InputError('comparison failed: a derived metric is not finite');
    }
    throw error;
  }
}

function render(report, format) {
  if (format === 'markdown') {
    return report.markdown.endsWith('\n') ? report.markdown : `${report.markdown}\n`;
  }
  return `${JSON.stringify(report.json, null, 2)}\n`;
}

/**
 * Run the CLI with explicit arguments and output streams.
 *
 * @param {string[]} argv Arguments after the script path.
 * @param {{stdout: {write(text: string): unknown}, stderr: {write(text: string): unknown}}} [io]
 * @returns {Promise<number>} 0 on success, 1 on input/report failure, 2 on usage error.
 */
export async function runBenchmarkReportCli(
  argv,
  io = { stdout: process.stdout, stderr: process.stderr },
) {
  const writeError = (message) => {
    io.stderr.write(`${MESSAGE_PREFIX}: ${message}\n`);
  };

  let options;
  try {
    options = parseArguments(argv);
  } catch (error) {
    if (error instanceof UsageError) {
      writeError(`usage error: ${error.message} (see --help)`);
      return EXIT_USAGE;
    }
    writeError('internal error while reading arguments');
    return EXIT_FAILURE;
  }

  if (options.help) {
    io.stdout.write(USAGE);
    return EXIT_OK;
  }

  let output;
  try {
    const baselineText = await readInput('baseline', options.baseline);
    const candidateText = await readInput('candidate', options.candidate);
    validateRun('baseline', baselineText);
    validateRun('candidate', candidateText);
    // The complete report is built before anything is written to stdout.
    output = render(buildReport(baselineText, candidateText), options.format);
  } catch (error) {
    writeError(error instanceof InputError ? error.message : 'internal error while building the report');
    return EXIT_FAILURE;
  }

  io.stdout.write(output);
  return EXIT_OK;
}

function isDirectEntry() {
  if (typeof import.meta.main === 'boolean') {
    return import.meta.main;
  }
  const entry = process.argv[1];
  return typeof entry === 'string' && entry !== '' && pathToFileURL(entry).href === import.meta.url;
}

if (isDirectEntry()) {
  // exitCode (not process.exit) lets buffered stdout finish flushing.
  process.exitCode = await runBenchmarkReportCli(process.argv.slice(2));
}
