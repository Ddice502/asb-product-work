#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import os from 'node:os';
import { pathToFileURL } from 'node:url';

const COLLECTOR_VERSION = '0.2.4';
const SCHEMA_VERSION = '1.0.0';
const SAFE_CONTENT_EXTENSIONS = new Set([
  '.md', '.txt', '.json', '.yaml', '.yml', '.js', '.mjs', '.cjs', '.sh',
  '.py', '.css', '.toml', '.conf', '.ini', '.sql', '.xml', '.csv', '.tsv',
]);
const SAFE_PLUGIN_CONFIG_NAMES = new Set([
  'app.json', 'appearance.json', 'backlink.json', 'bookmarks.json',
  'community-plugins.json', 'core-plugins.json', 'daily-notes.json',
  'data.json', 'graph.json', 'hotkeys.json', 'manifest.json', 'config.json',
  'settings.json', 'styles.css', 'templates.json', 'theme.css',
]);
const SENSITIVE_KEY = /(?:api[_-]?key|apikey|password|passwd|secret|token|authorization|cookie|private[_-]?key|client[_-]?secret|refresh[_-]?token|access[_-]?token|encryption[_-]?key)/i;
const SECRET_TEXT_PATTERNS = [
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/i,
  /\bBearer\s+[A-Za-z0-9._~+\/-]{16,}/i,
  /\b(?:sk|pk|ghp|github_pat|xox[baprs])-?[A-Za-z0-9_-]{16,}\b/i,
  /\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\b/,
  /[?&](?:api_?key|token|secret|signature|password)=[^&#\s]{4,}/i,
];

function redactSecretPatternsInMetadata(value) {
  let output = String(value);

  for (const [index, pattern] of SECRET_TEXT_PATTERNS.entries()) {
    const flags = pattern.flags.includes('g')
      ? pattern.flags
      : `${pattern.flags}g`;

    const globalPattern = new RegExp(
      pattern.source,
      flags,
    );

    output = output.replace(
      globalPattern,
      (matched) => {
        const digest = crypto
          .createHash('sha256')
          .update(matched)
          .digest('hex')
          .slice(0, 16);

        return `[REDACTED_SECRET_PATTERN_${index}_SHA256_${digest}]`;
      },
    );
  }

  return output;
}

const FILESYSTEM_PATH_METADATA_KEYS = new Set([
  'relative_path',
  'root',
  'scope_root',
  'symlink_target',
]);

function sanitizeFilesystemPathMetadata(value, currentKey = null) {
  if (typeof value === 'string') {
    if (currentKey && FILESYSTEM_PATH_METADATA_KEYS.has(currentKey)) {
      return redactSecretPatternsInMetadata(value);
    }

    return value;
  }

  if (Array.isArray(value)) {
    return value.map(
      (item) => sanitizeFilesystemPathMetadata(
        item,
        currentKey,
      ),
    );
  }

  if (value && typeof value === 'object') {
    const result = {};

    for (const [key, child] of Object.entries(value)) {
      result[key] = sanitizeFilesystemPathMetadata(
        child,
        key,
      );
    }

    return result;
  }

  return value;
}

const COLLECTION_ERROR_METADATA_KEYS = new Set([
  'message',
  'path',
  'root',
]);

function sanitizeCollectionErrorMetadata(value, currentKey = null) {
  if (typeof value === 'string') {
    if (currentKey && COLLECTION_ERROR_METADATA_KEYS.has(currentKey)) {
      return redactSecretPatternsInMetadata(value);
    }

    return value;
  }

  if (Array.isArray(value)) {
    return value.map(
      (item) => sanitizeCollectionErrorMetadata(
        item,
        currentKey,
      ),
    );
  }

  if (value && typeof value === 'object') {
    const result = {};

    for (const [key, child] of Object.entries(value)) {
      result[key] = sanitizeCollectionErrorMetadata(
        child,
        key,
      );
    }

    return result;
  }

  return value;
}

const SAFE_TOKEN_LIKE_OBJECT_KEYS = new Set([
  'skipped_due_to_file_limit',
  'skipped_due_to_size',
]);

function fail(message) {
  throw new Error(message);
}

function sha256(value) {
  return `sha256:${crypto.createHash('sha256').update(value).digest('hex')}`;
}

export function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') {
    const result = {};
    for (const key of Object.keys(value).sort()) result[key] = canonicalize(value[key]);
    return result;
  }
  return value;
}

export function canonicalStringify(value) {
  return JSON.stringify(canonicalize(value));
}

function parseJson(text, label) {
  try {
    return JSON.parse(text);
  } catch (error) {
    fail(`${label} is not valid JSON: ${error.message}`);
  }
}

function decodeBase64Json(encoded, label) {
  if (!encoded || !/^[A-Za-z0-9+/=]+$/.test(encoded)) fail(`${label} is missing or malformed.`);
  return parseJson(Buffer.from(encoded, 'base64').toString('utf8'), label);
}

function getArgument(name, required = true) {
  const index = process.argv.indexOf(name);
  if (index >= 0 && process.argv[index + 1]) return process.argv[index + 1];
  if (required) fail(`Missing required argument ${name}.`);
  return null;
}

function validateAbsolutePath(value, label) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || value.includes('\0')) {
    fail(`${label} must be an absolute path.`);
  }
  const normalized = path.normalize(value);
  if (normalized !== value || value.split(path.sep).includes('..')) fail(`${label} is not canonical.`);
}

export function validateConfig(config) {
  if (!config || typeof config !== 'object' || Array.isArray(config)) fail('Configuration must be an object.');
  if (config.schema_version !== SCHEMA_VERSION) fail(`Unsupported configuration schema_version ${config.schema_version}.`);
  if (config.phase !== 1 || config.mode !== 'collection_only') fail('Configuration must be Phase 1 collection_only.');
  if (config.timezone !== 'America/Kentucky/Louisville') fail('The approved timezone is required.');
  if (config.schedule_cron !== '0 4 * * *') fail('The approved daily schedule must remain 0 4 * * *.');

  const requiredPaths = {
    charter: '/Obsidian/90 System/AI Second Brain Charter.md',
    state_root: '/State/Second Brain/Auditor',
    snapshot_root: '/Archive/Second Brain Auditor/Snapshots',
    log_root: '/Logs/Second Brain Auditor',
    export_root: '/Exports/Second Brain Auditor',
    config_root: '/Config/Second Brain/Auditor',
  };
  for (const [key, expected] of Object.entries(requiredPaths)) {
    if (config.paths?.[key] !== expected) fail(`Configuration path ${key} must be ${expected}.`);
  }

  const scopeKeys = [
    'tree_metadata_roots', 'operational_metadata_roots', 'markdown_metadata_roots',
    'full_content_roots', 'safe_plugin_config_roots', 'explicit_full_content_files',
  ];
  for (const key of scopeKeys) {
    if (!Array.isArray(config.scope?.[key])) fail(`scope.${key} must be an array.`);
    for (const [index, item] of config.scope[key].entries()) validateAbsolutePath(item, `scope.${key}[${index}]`);
    if (new Set(config.scope[key]).size !== config.scope[key].length) fail(`scope.${key} contains duplicates.`);
  }
  if (!Array.isArray(config.scope?.exclude_globs) || config.scope.exclude_globs.length === 0) {
    fail('scope.exclude_globs must be a non-empty array.');
  }

  if (!Number.isInteger(config.execution_window?.maximum_lookback_days)
      || config.execution_window.maximum_lookback_days < 1
      || config.execution_window.maximum_lookback_days > 10) {
    fail('execution_window.maximum_lookback_days must be between 1 and 10.');
  }
  if (!Number.isInteger(config.execution_window?.maximum_metadata_records)
      || config.execution_window.maximum_metadata_records < 250
      || config.execution_window.maximum_metadata_records > 50000) {
    fail('execution_window.maximum_metadata_records must be between 250 and 50000.');
  }

  const numericLimits = [
    'maximum_tree_entries', 'maximum_markdown_files', 'maximum_markdown_bytes_per_file',
    'maximum_frontmatter_bytes_per_file', 'maximum_full_content_bytes_per_file',
    'maximum_total_full_content_bytes', 'maximum_snapshot_bytes', 'lock_stale_after_minutes',
  ];
  for (const key of numericLimits) {
    if (!Number.isInteger(config.limits?.[key]) || config.limits[key] <= 0) fail(`limits.${key} must be a positive integer.`);
  }
  if (config.thresholds?.capacity_warning_percent_remaining !== 25
      || config.thresholds?.capacity_critical_percent_remaining !== 15
      || config.thresholds?.queue_limits?.voice !== 10
      || config.thresholds?.queue_limits?.documents !== 15
      || config.thresholds?.queue_limits?.auditor_review !== 15
      || config.thresholds?.stalled_after_minutes !== 90) {
    fail('Approved capacity, queue, or stall thresholds were changed.');
  }
  for (const [key, value] of Object.entries(config.publication || {})) {
    if (value !== false) fail(`publication.${key} must remain false in Phase 1.`);
  }
  const requiredPublication = [
    'write_findings', 'write_obsidian_report', 'write_deletion_requests',
    'write_portable_review_package', 'send_success_notification',
  ];
  for (const key of requiredPublication) {
    if (!(key in (config.publication || {}))) fail(`publication.${key} is required.`);
  }
  return config;
}

function assertExistingDirectory(target, label) {
  let stat;
  try { stat = fs.statSync(target); } catch { fail(`${label} is missing: ${target}`); }
  if (!stat.isDirectory()) fail(`${label} is not a directory: ${target}`);
}

function assertExistingFile(target, label) {
  let stat;
  try { stat = fs.statSync(target); } catch { fail(`${label} is missing: ${target}`); }
  if (!stat.isFile()) fail(`${label} is not a file: ${target}`);
}

function atomicWriteJson(target, value, mode = 0o640) {
  const directory = path.dirname(target);
  const temporary = path.join(directory, `.${path.basename(target)}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`);
  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, 'wx', mode);
    fs.writeFileSync(descriptor, serialized, 'utf8');
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = null;
    fs.renameSync(temporary, target);
    const directoryDescriptor = fs.openSync(directory, 'r');
    fs.fsyncSync(directoryDescriptor);
    fs.closeSync(directoryDescriptor);
  } catch (error) {
    if (descriptor !== null && descriptor !== undefined) {
      try { fs.closeSync(descriptor); } catch {}
    }
    try { fs.unlinkSync(temporary); } catch {}
    throw error;
  }
  return Buffer.byteLength(serialized);
}

function readOptionalJson(target, label) {
  try { return parseJson(fs.readFileSync(target, 'utf8'), label); }
  catch (error) {
    if (error?.code === 'ENOENT') return null;
    throw error;
  }
}

function currentDate() {
  const override = process.env.AUDITOR_TEST_NOW;
  const date = override ? new Date(override) : new Date();
  if (Number.isNaN(date.getTime())) fail('AUDITOR_TEST_NOW is invalid.');
  return date;
}

function runIdentifier(now) {
  const compact = now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  return `${compact}-${crypto.randomBytes(4).toString('hex')}`;
}

function isInside(child, parent) {
  const relative = path.relative(parent, child);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function ensureAuditorDirectories(config) {
  assertExistingDirectory(config.paths.state_root, 'Auditor state root');
  assertExistingDirectory(path.dirname(config.paths.snapshot_root), 'Auditor archive root');
  assertExistingDirectory(config.paths.log_root, 'Auditor log root');
  assertExistingDirectory(config.paths.export_root, 'Auditor export root');
  assertExistingDirectory(config.paths.config_root, 'Auditor configuration root');
  for (const target of [
    path.join(config.paths.state_root, 'Staging'),
    config.paths.snapshot_root,
  ]) fs.mkdirSync(target, { recursive: true, mode: 0o750 });
}

function lockPaths(config) {
  const directory = path.join(config.paths.state_root, 'phase1.lock');
  return { directory, record: path.join(directory, 'lock.json') };
}

function acquireLock(config, record) {
  const lock = lockPaths(config);
  try {
    fs.mkdirSync(lock.directory, { mode: 0o750 });
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const stat = fs.statSync(lock.directory);
    const ageMinutes = (Date.now() - stat.mtimeMs) / 60000;
    const condition = ageMinutes >= config.limits.lock_stale_after_minutes ? 'stale' : 'active';
    fail(`An ${condition} Phase 1 lock already exists at ${lock.directory} (${ageMinutes.toFixed(1)} minutes old). Inspect it before manual removal.`);
  }
  try {
    atomicWriteJson(lock.record, record);
  } catch (error) {
    try { fs.rmdirSync(lock.directory); } catch {}
    throw error;
  }
  return lock;
}

function releaseLock(config, runId) {
  const lock = lockPaths(config);
  const record = readOptionalJson(lock.record, 'Phase 1 lock');
  if (!record) return;
  if (record.run_id !== runId) fail(`Refusing to release a lock owned by ${record.run_id}.`);
  fs.unlinkSync(lock.record);
  fs.rmdirSync(lock.directory);
}

function writeHeartbeat(config, value) {
  atomicWriteJson(path.join(config.paths.state_root, 'heartbeat.json'), value);
}

function writeRunState(config, value) {
  atomicWriteJson(path.join(config.paths.state_root, 'current-run.json'), value);
}

function computeWindow(now, config, lastSuccess) {
  const maximumStart = new Date(now.getTime() - config.execution_window.maximum_lookback_days * 86400000);
  let start = maximumStart;
  if (lastSuccess?.last_success) {
    const prior = new Date(lastSuccess.last_success);
    if (!Number.isNaN(prior.getTime()) && prior > maximumStart && prior < now) start = prior;
  }
  return { start: start.toISOString(), end: now.toISOString() };
}

function prepareRun(config) {
  validateConfig(config);
  ensureAuditorDirectories(config);
  assertExistingFile(config.paths.charter, 'Approved charter');

  const now = currentDate();
  const configCanonical = canonicalStringify(config);
  const configHash = sha256(configCanonical);
  const charterHash = sha256(fs.readFileSync(config.paths.charter));
  const runId = runIdentifier(now);
  const lastSuccess = readOptionalJson(path.join(config.paths.state_root, 'last-success.json'), 'Last-success state');
  const window = computeWindow(now, config, lastSuccess);
  const stagingDirectory = path.join(config.paths.state_root, 'Staging');
  const inputPath = path.join(stagingDirectory, `${runId}.collection-input.json`);
  const configPath = path.join(stagingDirectory, `${runId}.config.json`);
  const snapshotPath = path.join(config.paths.snapshot_root, `${runId}.snapshot.json`);

  acquireLock(config, {
    schema_version: SCHEMA_VERSION,
    phase: 1,
    run_id: runId,
    acquired_at: now.toISOString(),
    pid: process.pid,
    hostname: os.hostname(),
  });

  const runState = {
    schema_version: SCHEMA_VERSION,
    phase: 1,
    run_id: runId,
    current_phase: 'collecting',
    last_started: now.toISOString(),
    last_success: lastSuccess?.last_success || null,
    execution_window_start: window.start,
    execution_window_end: window.end,
    config_hash: configHash,
    charter_hash: charterHash,
    snapshot_path: snapshotPath,
    heartbeat_status: 'running',
  };
  atomicWriteJson(configPath, config);
  writeRunState(config, runState);
  writeHeartbeat(config, {
    schema_version: SCHEMA_VERSION,
    phase: 1,
    run_id: runId,
    last_trigger: now.toISOString(),
    last_success: lastSuccess?.last_success || null,
    current_phase: 'collecting',
    schedule_mode: 'daily_4am_inactive_during_gate',
    snapshot_id: null,
    snapshot_published: false,
    notification_status: 'not_requested',
  });

  return {
    schema_version: SCHEMA_VERSION,
    phase: 1,
    mode: 'collection_only',
    run_id: runId,
    window_start: window.start,
    window_end: window.end,
    config_hash: configHash,
    charter_hash: charterHash,
    collection_input_path: inputPath,
    snapshot_path: snapshotPath,
    maximum_metadata_records: config.execution_window.maximum_metadata_records,
  };
}

function globToRegExp(glob) {
  let expression = '^';
  for (let index = 0; index < glob.length; index += 1) {
    const character = glob[index];
    if (character === '*') {
      if (glob[index + 1] === '*') {
        index += 1;
        if (glob[index + 1] === '/') {
          index += 1;
          expression += '(?:.*/)?';
        } else expression += '.*';
      } else expression += '[^/]*';
    } else if (character === '?') expression += '[^/]';
    else expression += character.replace(/[|\\{}()[\]^$+?.]/g, '\\$&');
  }
  return new RegExp(`${expression}$`, 'i');
}

function exclusionMatcher(globs) {
  const patterns = globs.map(globToRegExp);
  return (relativePath) => patterns.some((pattern) => pattern.test(relativePath.replaceAll(path.sep, '/')));
}

function fileKind(stat) {
  if (stat.isFile()) return 'file';
  if (stat.isDirectory()) return 'directory';
  if (stat.isSymbolicLink()) return 'symlink';
  if (stat.isSocket()) return 'socket';
  if (stat.isFIFO()) return 'fifo';
  if (stat.isBlockDevice()) return 'block_device';
  if (stat.isCharacterDevice()) return 'character_device';
  return 'unknown';
}

function metadataRecord(root, absolute, stat) {
  const relative = path.relative(root, absolute).replaceAll(path.sep, '/');
  const record = {
    relative_path: relative || '.',
    kind: fileKind(stat),
    size_bytes: stat.size,
    modified_at: stat.mtime.toISOString(),
    mode: `0${(stat.mode & 0o7777).toString(8)}`,
    uid: stat.uid,
    gid: stat.gid,
  };
  if (stat.isSymbolicLink()) {
    try { record.symlink_target = fs.readlinkSync(absolute); }
    catch (error) { record.symlink_error = error.code || error.message; }
  }
  return record;
}

function walkMetadata(root, config, errors, category) {
  const result = { root, category, exists: false, entries: [], truncated: false };
  let rootStat;
  try { rootStat = fs.lstatSync(root); }
  catch (error) {
    errors.push({ stage: 'filesystem', root, code: error.code || 'READ_ERROR', message: `${root}: ${error.message}` });
    return result;
  }
  result.exists = true;
  result.entries.push(metadataRecord(root, root, rootStat));
  if (!rootStat.isDirectory()) return result;

  const excluded = exclusionMatcher(config.scope.exclude_globs);
  const stack = [root];
  while (stack.length > 0) {
    const directory = stack.pop();
    let names;
    try { names = fs.readdirSync(directory).sort((a, b) => a.localeCompare(b)); }
    catch (error) {
      errors.push({ stage: 'filesystem', root, path: directory, code: error.code || 'READ_ERROR', message: error.message });
      continue;
    }
    const directories = [];
    for (const name of names) {
      const absolute = path.join(directory, name);
      const relative = path.relative(root, absolute).replaceAll(path.sep, '/');
      if (excluded(relative)) continue;
      if (result.entries.length >= config.limits.maximum_tree_entries) {
        result.truncated = true;
        return result;
      }
      try {
        const stat = fs.lstatSync(absolute);
        result.entries.push(metadataRecord(root, absolute, stat));
        if (stat.isDirectory()) directories.push(absolute);
      } catch (error) {
        errors.push({ stage: 'filesystem', root, path: absolute, code: error.code || 'STAT_ERROR', message: error.message });
      }
    }
    for (let index = directories.length - 1; index >= 0; index -= 1) stack.push(directories[index]);
  }
  return result;
}

function redactUrlSecrets(text) {
  return text.replace(/([?&](?:api_?key|token|secret|signature|password)=)[^&#\s]+/gi, '$1[REDACTED]');
}

export function sanitizeText(text) {
  let value = String(text);
  value = value.replace(/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/gi, '[REDACTED PRIVATE KEY]');
  value = value.replace(/\bBearer\s+[A-Za-z0-9._~+\/-]{8,}/gi, 'Bearer [REDACTED]');
  value = value.replace(/\b(?:sk|pk|ghp|github_pat|xox[baprs])-?[A-Za-z0-9_-]{16,}\b/gi, '[REDACTED TOKEN]');
  value = value.replace(/\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\b/g, '[REDACTED JWT]');
  value = redactUrlSecrets(value);
  value = value.replace(/^(\s*(?:[-A-Za-z0-9_.]*(?:api_?key|password|passwd|secret|token|authorization|cookie|private_?key|client_?secret|encryption_?key)[-A-Za-z0-9_.]*)(?:\s*[:=]\s*)).+$/gim, '$1[REDACTED]');
  return value;
}

export function sanitizeObject(value, key = '') {
  if (SENSITIVE_KEY.test(key)) return '[REDACTED]';
  if (typeof value === 'string') return sanitizeText(value);
  if (Array.isArray(value)) return value.map((item) => sanitizeObject(item));
  if (value && typeof value === 'object') {
    const result = {};
    for (const [childKey, childValue] of Object.entries(value)) {
      if (/^(?:data|binaryData|executionData|runData|pinData|staticData)$/i.test(childKey)) continue;
      result[childKey] = sanitizeObject(childValue, childKey);
    }
    return result;
  }
  return value;
}

export function sanitizeFullContent(raw, absolute) {
  if (path.extname(absolute).toLowerCase() !== '.json') return sanitizeText(raw);
  const parsed = parseJson(raw, `Allowlisted JSON file ${absolute}`);
  return `${JSON.stringify(sanitizeObject(parsed), null, 2)}\n`;
}

function parseSimpleFrontmatter(raw) {
  const keys = [];
  const scalarValues = {};
  const lines = raw.split(/\r?\n/);
  for (const line of lines) {
    if (/^\s/.test(line) || /^\s*(?:#|$)/.test(line)) continue;
    const match = line.match(/^([A-Za-z0-9_.-]+):(?:\s*(.*))?$/);
    if (!match) continue;
    const key = match[1];
    keys.push(key);
    const rawValue = (match[2] || '').trim();
    if (rawValue && rawValue.length <= 2048) {
      scalarValues[key] = SENSITIVE_KEY.test(key) ? '[REDACTED]' : sanitizeText(rawValue);
    }
  }
  return { keys: [...new Set(keys)].sort(), scalar_values: canonicalize(scalarValues), sha256: sha256(raw) };
}

function stripFencedCodeBlocks(text) {
  const lines = String(text).split(/\r?\n/);
  const output = [];
  let fence = null;

  for (const line of lines) {
    const marker = line.match(/^\s*(`{3,}|~{3,})/);

    if (!fence && marker) {
      fence = {
        character: marker[1][0],
        length: marker[1].length,
      };
      output.push('');
      continue;
    }

    if (fence) {
      const closing = line.match(/^\s*(`{3,}|~{3,})\s*$/);

      if (
        closing
        && closing[1][0] === fence.character
        && closing[1].length >= fence.length
      ) {
        fence = null;
      }

      output.push('');
      continue;
    }

    output.push(line);
  }

  return output.join('\n');
}

export function extractMarkdownMetadata(content, frontmatterLimit = 65536) {
  let frontmatter = { present: false, keys: [], scalar_values: {}, sha256: null, truncated: false };
  let bodyStart = 0;
  if (content.startsWith('---\n') || content.startsWith('---\r\n')) {
    const firstNewline = content.indexOf('\n');
    const closing = content.indexOf('\n---', firstNewline + 1);
    if (closing >= 0) {
      const raw = content.slice(firstNewline + 1, closing);
      const clipped = raw.slice(0, frontmatterLimit);
      frontmatter = {
        present: true,
        ...parseSimpleFrontmatter(clipped),
        truncated: raw.length > frontmatterLimit,
      };
      bodyStart = content.indexOf('\n', closing + 4) + 1;
    } else {
      frontmatter = { present: true, keys: [], scalar_values: {}, sha256: sha256(content.slice(0, frontmatterLimit)), truncated: content.length > frontmatterLimit, closing_delimiter_missing: true };
    }
  }
  const body = stripFencedCodeBlocks(
    content.slice(Math.max(0, bodyStart)),
  );
  const links = new Set();
  for (const match of body.matchAll(/!?\[\[([^\]|#\n]+)(?:#[^\]|\n]+)?(?:\|[^\]\n]+)?\]\]/g)) {
    const target = match[1].trim();
    if (target) links.add(sanitizeText(target));
  }
  for (const match of body.matchAll(/\[[^\]\n]*\]\(([^)\n]+)\)/g)) {
    const target = match[1].trim().replace(/^<|>$/g, '');
    if (target) links.add(sanitizeText(target));
  }
  return { frontmatter, outbound_link_targets: [...links].sort((a, b) => a.localeCompare(b)) };
}

function collectMarkdownMetadata(root, config, errors) {
  const result = { root, exists: false, files: [], truncated: false };
  try {
    if (!fs.statSync(root).isDirectory()) return result;
    result.exists = true;
  } catch (error) {
    errors.push({ stage: 'markdown_metadata', root, code: error.code || 'READ_ERROR', message: error.message });
    return result;
  }
  const excluded = exclusionMatcher(config.scope.exclude_globs);
  const stack = [root];
  while (stack.length > 0) {
    const directory = stack.pop();
    let names;
    try { names = fs.readdirSync(directory).sort((a, b) => a.localeCompare(b)); }
    catch (error) {
      errors.push({ stage: 'markdown_metadata', root, path: directory, code: error.code || 'READ_ERROR', message: error.message });
      continue;
    }
    const directories = [];
    for (const name of names) {
      const absolute = path.join(directory, name);
      const relative = path.relative(root, absolute).replaceAll(path.sep, '/');
      if (excluded(relative)) continue;
      let stat;
      try { stat = fs.lstatSync(absolute); }
      catch (error) {
        errors.push({ stage: 'markdown_metadata', root, path: absolute, code: error.code || 'STAT_ERROR', message: error.message });
        continue;
      }
      if (stat.isDirectory()) directories.push(absolute);
      if (!stat.isFile() || path.extname(name).toLowerCase() !== '.md') continue;
      if (result.files.length >= config.limits.maximum_markdown_files) {
        result.truncated = true;
        return result;
      }
      if (stat.size > config.limits.maximum_markdown_bytes_per_file) {
        errors.push({ stage: 'markdown_metadata', root, path: absolute, code: 'FILE_LIMIT', message: `Markdown file exceeds ${config.limits.maximum_markdown_bytes_per_file} bytes.` });
        result.files.push({ relative_path: relative, size_bytes: stat.size, skipped_due_to_size: true });
        continue;
      }
      try {
        const content = fs.readFileSync(absolute, 'utf8');
        const metadata = extractMarkdownMetadata(content, config.limits.maximum_frontmatter_bytes_per_file);
        result.files.push({
          relative_path: relative,
          size_bytes: stat.size,
          modified_at: stat.mtime.toISOString(),
          ...metadata,
        });
      } catch (error) {
        errors.push({ stage: 'markdown_metadata', root, path: absolute, code: error.code || 'READ_ERROR', message: error.message });
      }
    }
    for (let index = directories.length - 1; index >= 0; index -= 1) stack.push(directories[index]);
  }
  return result;
}

function isSafeContentFile(absolute, pluginRoot = false) {
  const name = path.basename(absolute).toLowerCase();
  if (pluginRoot) return SAFE_PLUGIN_CONFIG_NAMES.has(name) || path.extname(name) === '.css';
  return SAFE_CONTENT_EXTENSIONS.has(path.extname(name));
}

function collectFullContent(config, errors) {
  const records = [];
  let totalBytes = 0;
  let truncated = false;
  const excluded = exclusionMatcher(config.scope.exclude_globs);

  const addFile = (absolute, scopeRoot, scopeType) => {
    const relative = path.relative(scopeRoot, absolute).replaceAll(path.sep, '/');
    if (excluded(relative)) return;
    let stat;
    try { stat = fs.statSync(absolute); }
    catch (error) {
      errors.push({ stage: 'full_content', path: absolute, code: error.code || 'STAT_ERROR', message: error.message });
      return;
    }
    if (!stat.isFile() || !isSafeContentFile(absolute, scopeType === 'plugin_config')) return;
    if (stat.size > config.limits.maximum_full_content_bytes_per_file) {
      records.push({ scope_root: scopeRoot, relative_path: relative || path.basename(absolute), scope_type: scopeType, size_bytes: stat.size, skipped_due_to_file_limit: true });
      return;
    }
    if (totalBytes + stat.size > config.limits.maximum_total_full_content_bytes) {
      truncated = true;
      return;
    }
    try {
      const raw = fs.readFileSync(absolute, 'utf8');
      const content = sanitizeFullContent(raw, absolute);
      records.push({
        scope_root: scopeRoot,
        relative_path: relative || path.basename(absolute),
        scope_type: scopeType,
        size_bytes: stat.size,
        modified_at: stat.mtime.toISOString(),
        source_sha256: sha256(raw),
        content,
      });
      totalBytes += stat.size;
    } catch (error) {
      errors.push({ stage: 'full_content', path: absolute, code: error.code || 'READ_ERROR', message: error.message });
    }
  };

  const scanRoot = (root, scopeType) => {
    let stat;
    try { stat = fs.statSync(root); }
    catch (error) {
      errors.push({ stage: 'full_content', root, code: error.code || 'READ_ERROR', message: error.message });
      return;
    }
    if (stat.isFile()) return addFile(root, path.dirname(root), scopeType);
    if (!stat.isDirectory()) return;
    const stack = [root];
    while (stack.length > 0 && !truncated) {
      const directory = stack.pop();
      let names;
      try { names = fs.readdirSync(directory).sort((a, b) => a.localeCompare(b)); }
      catch (error) {
        errors.push({ stage: 'full_content', root, path: directory, code: error.code || 'READ_ERROR', message: error.message });
        continue;
      }
      const directories = [];
      for (const name of names) {
        const absolute = path.join(directory, name);
        const relative = path.relative(root, absolute).replaceAll(path.sep, '/');
        if (excluded(relative)) continue;
        let childStat;
        try { childStat = fs.lstatSync(absolute); }
        catch (error) {
          errors.push({ stage: 'full_content', root, path: absolute, code: error.code || 'STAT_ERROR', message: error.message });
          continue;
        }
        if (childStat.isDirectory()) directories.push(absolute);
        else if (childStat.isFile()) addFile(absolute, root, scopeType);
        if (truncated) break;
      }
      for (let index = directories.length - 1; index >= 0; index -= 1) stack.push(directories[index]);
    }
  };

  for (const root of config.scope.full_content_roots) scanRoot(root, 'allowlisted_root');
  for (const root of config.scope.safe_plugin_config_roots) scanRoot(root, 'plugin_config');
  for (const file of config.scope.explicit_full_content_files) scanRoot(file, 'explicit_file');

  records.sort((a, b) => `${a.scope_root}/${a.relative_path}`.localeCompare(`${b.scope_root}/${b.relative_path}`));
  return { files: records, source_bytes_read: totalBytes, truncated };
}

function filesystemCapacity(target) {
  const stat = fs.statfsSync(target, { bigint: true });
  const total = stat.blocks * stat.bsize;
  const available = stat.bavail * stat.bsize;
  const inodeTotal = stat.files;
  const inodeFree = stat.ffree;
  const percent = total > 0n ? Number((available * 10000n) / total) / 100 : null;
  const inodePercent = inodeTotal > 0n ? Number((inodeFree * 10000n) / inodeTotal) / 100 : null;
  return {
    path: target,
    total_bytes: total.toString(),
    available_bytes: available.toString(),
    capacity_percent_remaining: percent,
    total_inodes: inodeTotal.toString(),
    free_inodes: inodeFree.toString(),
    inode_percent_remaining: inodePercent,
  };
}

function collectCapacities(config, errors) {
  const targets = [
    ...config.scope.tree_metadata_roots,
    ...config.scope.operational_metadata_roots,
    config.paths.state_root,
    config.paths.snapshot_root,
    config.paths.log_root,
    config.paths.export_root,
    config.paths.config_root,
  ];
  const records = [];
  const seen = new Set();
  for (const target of targets) {
    try {
      const record = filesystemCapacity(target);
      const key = `${record.total_bytes}:${record.available_bytes}:${record.total_inodes}:${record.free_inodes}`;
      if (seen.has(key)) continue;
      seen.add(key);
      records.push(record);
    } catch (error) {
      errors.push({ stage: 'capacity', path: target, code: error.code || 'STATFS_ERROR', message: error.message });
    }
  }
  return records.sort((a, b) => a.path.localeCompare(b.path));
}

function diagnosticPathSegment(key) {
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) return `.${key}`;
  return `.[key-sha256:${crypto.createHash('sha256').update(key).digest('hex').slice(0, 12)}]`;
}

export function findSecretPattern(value, currentPath = '$') {
  if (typeof value === 'string') {
    for (const [index, pattern] of SECRET_TEXT_PATTERNS.entries()) {
      const match = value.match(pattern);
      if (match) {
        return {
          json_path: currentPath,
          location_kind: 'string_value',
          pattern_index: index,
          matched_length: match[0].length,
          matched_sha256_prefix: crypto.createHash('sha256').update(match[0]).digest('hex').slice(0, 16),
          matched_value_printed: false,
        };
      }
    }
    return null;
  }
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      const finding = findSecretPattern(value[index], `${currentPath}[${index}]`);
      if (finding) return finding;
    }
    return null;
  }
  if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      if (!SAFE_TOKEN_LIKE_OBJECT_KEYS.has(key)) {
        for (const [index, pattern] of SECRET_TEXT_PATTERNS.entries()) {
          const match = key.match(pattern);
          if (match) {
            return {
              json_path: currentPath,
              location_kind: 'object_key',
              pattern_index: index,
              matched_length: match[0].length,
              matched_sha256_prefix: crypto.createHash('sha256').update(match[0]).digest('hex').slice(0, 16),
              matched_value_printed: false,
            };
          }
        }
      }
      const finding = findSecretPattern(child, `${currentPath}${diagnosticPathSegment(key)}`);
      if (finding) return finding;
    }
  }
  return null;
}

function assertNoSecretText(value, label) {
  const finding = findSecretPattern(value);
  if (finding) {
    fail(`${label} failed the secret-pattern scan at ${finding.json_path} `
      + `(location_kind=${finding.location_kind}, pattern_index=${finding.pattern_index}, matched_length=${finding.matched_length}, `
      + `matched_sha256_prefix=${finding.matched_sha256_prefix}, matched_value_printed=false).`);
  }
}

function validateCollectionInput(input, prepared, config) {
  if (!input || input.schema_version !== SCHEMA_VERSION || input.phase !== 1) fail('Collection input has the wrong schema or phase.');
  if (input.run?.run_id !== prepared.run_id) fail('Collection input run_id does not match current-run state.');
  if (input.run?.config_hash !== prepared.config_hash || input.run?.charter_hash !== prepared.charter_hash) fail('Collection input hashes do not match current-run state.');
  if (input.run?.window_start !== prepared.execution_window_start || input.run?.window_end !== prepared.execution_window_end) fail('Collection input window does not match current-run state.');
  if (!Array.isArray(input.active_workflows) || !Array.isArray(input.execution_metadata)) fail('Collection input arrays are missing.');
  if (input.active_workflows.some((workflow) => workflow.active !== true)) fail('Collection input contains an inactive workflow.');
  if (input.execution_metadata.length > config.execution_window.maximum_metadata_records) fail('Execution metadata exceeds the configured safety limit.');
  if (input.privacy?.active_only_enforced !== true
      || input.privacy?.credential_values_collected !== false
      || input.privacy?.execution_payloads_collected !== false) {
    fail('Collection input privacy assertions failed.');
  }
  assertNoSecretText(input, 'Collection input');
}

export function buildEvidenceHash(snapshot) {
  const evidence = {
    configuration: snapshot.configuration,
    active_workflows: snapshot.active_workflows,
    execution_metadata: snapshot.execution_metadata,
    filesystem: snapshot.filesystem,
    collection_errors: snapshot.collection_errors,
  };
  return sha256(canonicalStringify(evidence));
}

function finalizeRun(inputPath) {
  const currentStateRoot = '/State/Second Brain/Auditor';
  const prepared = readOptionalJson(path.join(currentStateRoot, 'current-run.json'), 'Current-run state');
  if (!prepared || prepared.phase !== 1 || prepared.current_phase !== 'collecting') fail('No collecting Phase 1 run is available to finalize.');
  const runId = prepared.run_id;
  const expectedStaging = path.join(currentStateRoot, 'Staging');
  const normalizedInput = path.resolve(inputPath);
  if (!isInside(normalizedInput, expectedStaging) || !normalizedInput.endsWith('.collection-input.json')) {
    fail(`Collection input must be an auditor staging file under ${expectedStaging}.`);
  }
  const configPath = path.join(expectedStaging, `${runId}.config.json`);
  const config = validateConfig(parseJson(fs.readFileSync(configPath, 'utf8'), 'Staged configuration'));
  const rawInput = parseJson(fs.readFileSync(normalizedInput, 'utf8'), 'Collection input');
  if ((rawInput.execution_metadata || []).some((record) =>
    record && typeof record === 'object'
      && ['data', 'binaryData', 'executionData', 'runData'].some((key) => Object.prototype.hasOwnProperty.call(record, key)))) {
    fail('Collection input contains forbidden execution payload fields.');
  }
  const input = sanitizeObject(rawInput);
  validateCollectionInput(input, prepared, config);

  const errors = [];
  const tree = [];
  for (const root of config.scope.tree_metadata_roots) tree.push(walkMetadata(root, config, errors, 'vault'));
  for (const root of config.scope.operational_metadata_roots) tree.push(walkMetadata(root, config, errors, 'operational'));
  const markdown = config.scope.markdown_metadata_roots.map((root) => collectMarkdownMetadata(root, config, errors));
  const fullContent = collectFullContent(config, errors);
  const capacities = collectCapacities(config, errors);

  const statuses = {};
  const modes = {};
  const workflowCounts = {};
  for (const execution of input.execution_metadata) {
    statuses[execution.status || 'unknown'] = (statuses[execution.status || 'unknown'] || 0) + 1;
    modes[execution.mode || 'unknown'] = (modes[execution.mode || 'unknown'] || 0) + 1;
    const workflowId = execution.workflow_id || 'unknown';
    workflowCounts[workflowId] = (workflowCounts[workflowId] || 0) + 1;
  }
  const executions = [...input.execution_metadata].sort((a, b) => {
    const time = String(a.started_at || '').localeCompare(String(b.started_at || ''));
    return time || String(a.id || '').localeCompare(String(b.id || ''));
  });

  const snapshot = {
    schema_version: SCHEMA_VERSION,
    phase: 1,
    mode: 'collection_only',
    run: {
      run_id: runId,
      started_at: prepared.last_started,
      completed_at: currentDate().toISOString(),
      window_start: prepared.execution_window_start,
      window_end: prepared.execution_window_end,
      config_hash: prepared.config_hash,
      charter_hash: prepared.charter_hash,
      collector_version: COLLECTOR_VERSION,
    },
    privacy: {
      ordinary_note_bodies_collected: false,
      credential_values_collected: false,
      execution_payloads_collected: false,
      external_network_calls_performed: false,
      findings_written: false,
    },
    configuration: config,
    runtime: {
      hostname: os.hostname(),
      platform: process.platform,
      architecture: process.arch,
      node_version: process.version,
      uid: typeof process.getuid === 'function' ? process.getuid() : null,
      gid: typeof process.getgid === 'function' ? process.getgid() : null,
      timezone: process.env.GENERIC_TIMEZONE || process.env.TZ || null,
    },
    active_workflows: [...input.active_workflows].sort((a, b) => String(a.id).localeCompare(String(b.id))),
    execution_metadata: {
      window_start: prepared.execution_window_start,
      window_end: prepared.execution_window_end,
      count: executions.length,
      status_counts: canonicalize(statuses),
      mode_counts: canonicalize(modes),
      workflow_counts: canonicalize(workflowCounts),
      records: executions,
    },
    filesystem: {
      trees: tree,
      markdown_metadata: markdown,
      allowlisted_full_content: fullContent,
      capacities,
    },
    collection_errors: errors.sort((a, b) => canonicalStringify(a).localeCompare(canonicalStringify(b))),
    evidence_hash: null,
  };
  snapshot.filesystem = sanitizeFilesystemPathMetadata(snapshot.filesystem);
  snapshot.collection_errors = sanitizeCollectionErrorMetadata(snapshot.collection_errors);
  snapshot.evidence_hash = buildEvidenceHash(snapshot);
  assertNoSecretText(snapshot, 'Final snapshot');
  const serialized = `${JSON.stringify(snapshot, null, 2)}\n`;
  if (Buffer.byteLength(serialized) > config.limits.maximum_snapshot_bytes) {
    fail(`Snapshot exceeds the ${config.limits.maximum_snapshot_bytes}-byte safety limit.`);
  }

  const snapshotPath = prepared.snapshot_path;
  if (!isInside(snapshotPath, config.paths.snapshot_root)) fail('Prepared snapshot path escaped the approved snapshot root.');
  const bytes = atomicWriteJson(snapshotPath, snapshot);
  const verified = parseJson(fs.readFileSync(snapshotPath, 'utf8'), 'Published snapshot');
  if (verified.evidence_hash !== snapshot.evidence_hash) fail('Published snapshot verification failed.');

  const completed = currentDate().toISOString();
  const successState = {
    schema_version: SCHEMA_VERSION,
    phase: 1,
    last_started: prepared.last_started,
    last_success: completed,
    previous_snapshot_id: runId,
    previous_snapshot_path: snapshotPath,
    execution_window_start: prepared.execution_window_start,
    execution_window_end: prepared.execution_window_end,
    config_hash: prepared.config_hash,
    charter_hash: prepared.charter_hash,
    evidence_hash: snapshot.evidence_hash,
    active_workflow_count: snapshot.active_workflows.length,
    execution_metadata_count: executions.length,
    collection_error_count: errors.length,
  };
  atomicWriteJson(path.join(config.paths.state_root, 'last-success.json'), successState);
  writeRunState(config, { ...prepared, current_phase: 'succeeded', last_success: completed, heartbeat_status: 'success', evidence_hash: snapshot.evidence_hash });
  writeHeartbeat(config, {
    schema_version: SCHEMA_VERSION,
    phase: 1,
    run_id: runId,
    last_trigger: prepared.last_started,
    last_success: completed,
    current_phase: 'succeeded',
    schedule_mode: 'daily_4am_inactive_during_gate',
    snapshot_id: runId,
    snapshot_published: true,
    notification_status: 'not_requested',
  });
  fs.unlinkSync(normalizedInput);
  fs.unlinkSync(configPath);
  releaseLock(config, runId);

  return {
    schema_version: SCHEMA_VERSION,
    phase: 1,
    mode: 'collection_only',
    status: errors.length > 0 ? 'collected_with_errors' : 'collected',
    run_id: runId,
    snapshot_path: snapshotPath,
    snapshot_bytes: bytes,
    evidence_hash: snapshot.evidence_hash,
    active_workflow_count: snapshot.active_workflows.length,
    execution_metadata_count: executions.length,
    collection_error_count: errors.length,
    findings_written: false,
    obsidian_report_written: false,
    external_network_calls_performed: false,
  };
}

function markFailed(message) {
  const stateRoot = '/State/Second Brain/Auditor';
  const prepared = readOptionalJson(path.join(stateRoot, 'current-run.json'), 'Current-run state');
  if (!prepared?.run_id) fail('No current Phase 1 run exists to mark failed.');
  const configPath = path.join(stateRoot, 'Staging', `${prepared.run_id}.config.json`);
  const config = validateConfig(parseJson(fs.readFileSync(configPath, 'utf8'), 'Staged configuration'));
  const failedAt = currentDate().toISOString();
  const safeMessage = sanitizeText(message).slice(0, 4000);
  writeRunState(config, { ...prepared, current_phase: 'failed', failed_at: failedAt, heartbeat_status: 'failed', failure_message: safeMessage });
  writeHeartbeat(config, {
    schema_version: SCHEMA_VERSION,
    phase: 1,
    run_id: prepared.run_id,
    last_trigger: prepared.last_started,
    last_success: prepared.last_success || null,
    current_phase: 'failed',
    schedule_mode: 'daily_4am_inactive_during_gate',
    snapshot_id: null,
    snapshot_published: false,
    notification_status: 'collector_error_requires_review',
    failure_message: safeMessage,
  });
  releaseLock(config, prepared.run_id);
  return { schema_version: SCHEMA_VERSION, phase: 1, status: 'failed_recorded', run_id: prepared.run_id };
}

function usage() {
  return [
    'Usage:',
    "  phase1-collector.mjs prepare --config-base64 BASE64",
    "  phase1-collector.mjs finalize --input-file '/State/Second Brain/Auditor/Staging/...json'",
    '  phase1-collector.mjs fail --message-base64 BASE64',
  ].join('\n');
}

async function main() {
  const command = process.argv[2];
  if (!command || command === '--help' || command === '-h') {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  let result;
  if (command === 'prepare') {
    const config = decodeBase64Json(getArgument('--config-base64'), 'Configuration');
    result = prepareRun(config);
  } else if (command === 'finalize') {
    result = finalizeRun(getArgument('--input-file'));
  } else if (command === 'fail') {
    const message = Buffer.from(getArgument('--message-base64'), 'base64').toString('utf8');
    result = markFailed(message);
  } else fail(`Unknown command ${command}.\n${usage()}`);
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

const isEntryPoint = process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (isEntryPoint) {
  main().catch((error) => {
    process.stderr.write(`${error.stack || error.message}\n`);
    process.exitCode = 1;
  });
}
