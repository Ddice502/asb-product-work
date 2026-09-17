#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const RECOVERY_VERSION = '1.0.1';

function nowIso() {
  return new Date().toISOString();
}

function fail(message) {
  throw new Error(message);
}

function ensureDir(directory) {
  fs.mkdirSync(directory, { recursive: true });
}

function readJson(target, label = target) {
  try {
    return JSON.parse(fs.readFileSync(target, 'utf8'));
  } catch (error) {
    throw new Error(`Could not read ${label}: ${error.message}`);
  }
}

function atomicWriteJson(target, value) {
  ensureDir(path.dirname(target));
  const temporary = path.join(
    path.dirname(target),
    `.${path.basename(target)}.${process.pid}.${Date.now()}.tmp`
  );
  const descriptor = fs.openSync(temporary, 'wx', 0o664);
  try {
    fs.writeFileSync(descriptor, JSON.stringify(value, null, 2) + '\n', 'utf8');
    fs.fsyncSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
  fs.renameSync(temporary, target);
}

function sha256File(target) {
  const hash = crypto.createHash('sha256');
  const descriptor = fs.openSync(target, 'r');
  const buffer = Buffer.alloc(1024 * 1024);
  try {
    while (true) {
      const count = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (!count) break;
      hash.update(buffer.subarray(0, count));
    }
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest('hex');
}

function safeName(value, maximum = 180) {
  return String(value || 'camera-photo')
    .replace(/[^\w.\- ()[\]]+/g, '_')
    .slice(0, maximum);
}

function uniquePath(directory, filename, suffix) {
  ensureDir(directory);
  const first = path.join(directory, filename);
  if (!fs.existsSync(first)) return first;
  const extension = path.extname(filename);
  const stem = path.basename(filename, extension);
  return path.join(directory, `${stem}_${suffix}${extension}`);
}

function moveVerified(source, destination) {
  ensureDir(path.dirname(destination));
  try {
    fs.renameSync(source, destination);
  } catch (error) {
    if (error.code !== 'EXDEV') throw error;
    fs.copyFileSync(source, destination, fs.constants.COPYFILE_EXCL);
    if (sha256File(source) !== sha256File(destination)) {
      fs.rmSync(destination, { force: true });
      throw new Error(`Checksum mismatch while copying ${source}`);
    }
    fs.unlinkSync(source);
  }
}

function isContextFailure(record) {
  if (!record || record.status !== 'failed' || record.completed === true) {
    return false;
  }
  return /exceed(?:s|ed)? the available context size|exceed_context_size_error/i
    .test(String(record.last_error || ''));
}

function argumentsFrom(argv) {
  const output = { configPath: '', apply: false };
  for (let index = 2; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--config') output.configPath = argv[++index] || '';
    else if (argument === '--apply') output.apply = true;
    else fail(`Unknown argument: ${argument}`);
  }
  if (!output.configPath) fail('Use --config PATH.');
  return output;
}

function main() {
  const args = argumentsFrom(process.argv);
  const config = readJson(args.configPath, 'camera-photo configuration');
  const statePath = String(config.paths?.state || '');
  const inboxPath = String(config.paths?.camera_inbox || '');
  const failedRoot = String(config.paths?.failed || '');
  const logsPath = String(config.paths?.logs || '');

  for (const target of [statePath, inboxPath, failedRoot, logsPath]) {
    const unsafeProductionPath = process.env.CAMERA_PHOTO_TEST_MODE !== '1' &&
      !target.startsWith('/Capture/Documents');
    if (!path.isAbsolute(target) || unsafeProductionPath) {
      fail(`Unsafe recovery path: ${target}`);
    }
  }
  if (!fs.existsSync(statePath)) fail(`State file does not exist: ${statePath}`);

  const state = readJson(statePath, 'camera-photo state');
  state.files = state.files || {};
  const candidates = [];

  for (const [expectedHash, record] of Object.entries(state.files)) {
    if (!isContextFailure(record)) continue;
    const sourcePath = String(record.failed_path || '');
    const item = {
      source_sha256: expectedHash,
      source_name: record.source_name,
      failed_path: sourcePath,
      result: 'eligible',
    };
    if (!sourcePath.startsWith(`${failedRoot}/`)) {
      item.result = 'unsafe_failed_path';
    } else if (!fs.existsSync(sourcePath)) {
      item.result = 'failed_copy_missing';
    } else {
      const actualHash = sha256File(sourcePath);
      if (actualHash !== expectedHash) {
        item.result = 'checksum_mismatch';
        item.actual_sha256 = actualHash;
      }
    }
    candidates.push(item);
  }

  if (!args.apply) {
    process.stdout.write(JSON.stringify({
      result: 'recovery_preview',
      recovery_version: RECOVERY_VERSION,
      eligible_count: candidates.filter((item) => item.result === 'eligible').length,
      items: candidates,
    }) + '\n');
    return;
  }

  const recovered = [];
  const skipped = [];
  const recoveredAt = nowIso();
  for (const item of candidates) {
    if (item.result !== 'eligible') {
      skipped.push(item);
      continue;
    }
    const record = state.files[item.source_sha256];
    const destination = uniquePath(
      inboxPath,
      safeName(record.source_name || path.basename(item.failed_path)),
      item.source_sha256.slice(0, 8)
    );
    moveVerified(item.failed_path, destination);
    const current = new Date();
    fs.utimesSync(destination, current, current);
    state.files[item.source_sha256] = {
      ...record,
      status: 'retry_queued_after_context_fix',
      completed: false,
      processing_path: null,
      failed_path: null,
      retry_after: null,
      previous_error: record.last_error,
      last_error: null,
      recovery: {
        recovery_version: RECOVERY_VERSION,
        recovered_at: recoveredAt,
        previous_failed_path: item.failed_path,
        retry_inbox_path: destination,
        reason: 'ollama_context_limit_fixed',
      },
    };
    state.worker_version = RECOVERY_VERSION;
    state.updated_at = nowIso();
    atomicWriteJson(statePath, state);
    recovered.push({
      source_sha256: item.source_sha256,
      source_name: record.source_name,
      retry_inbox_path: destination,
    });
  }

  state.worker_version = RECOVERY_VERSION;
  state.updated_at = nowIso();
  atomicWriteJson(statePath, state);
  ensureDir(logsPath);
  fs.appendFileSync(
    path.join(logsPath, 'camera-photo-events.jsonl'),
    JSON.stringify({
      event: 'context_limit_failures_requeued',
      at: nowIso(),
      recovery_version: RECOVERY_VERSION,
      recovered_count: recovered.length,
      skipped_count: skipped.length,
      recovered,
      skipped,
    }) + '\n',
    'utf8'
  );

  process.stdout.write(JSON.stringify({
    result: 'context_limit_failures_requeued',
    recovery_version: RECOVERY_VERSION,
    recovered_count: recovered.length,
    skipped_count: skipped.length,
    recovered,
    skipped,
  }) + '\n');
}

try {
  main();
} catch (error) {
  process.stderr.write(`${error.stack || error.message || error}\n`);
  process.exitCode = 1;
}
