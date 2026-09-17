#!/usr/bin/env node

import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const WORKER_VERSION = '1.0.0';
const DEFAULT_CONFIG_PATH = '/Config/Second Brain/Backups/backup-config.json';
const CONFIG_PATH = process.env.SECOND_BRAIN_BACKUP_CONFIG || DEFAULT_CONFIG_PATH;
const ACTION = process.argv[2] || 'help';
// KI-124 ad-hoc proof support. argv[3] optionally names one explicit run id so a fresh,
// uniquely identified end-to-end run can be proven without waiting for, or disturbing, a
// scheduled daily run. It is parsed lazily inside runAction() so a bad value is reported
// through the normal failure path. When absent, every branch guarded by it is skipped and
// behaviour is identical to the scheduled engine.
const RUN_ID_ARG = process.argv[3] ?? null;
const ADHOC_TIER = 'adhoc';

let loadedConfig = null;

function nowIso() {
  return new Date().toISOString();
}

function cleanHeader(value) {
  return String(value ?? '').replace(/[\r\n]+/g, ' ').slice(0, 240);
}

function asError(error) {
  if (error instanceof Error) {
    return {
      name: error.name,
      message: error.message,
      code: error.code ?? null,
    };
  }
  return { name: 'Error', message: String(error), code: null };
}

function emit(result) {
  process.stdout.write(`${JSON.stringify({
    schema_version: '1.0.0',
    worker_version: WORKER_VERSION,
    action: ACTION,
    completed_at: nowIso(),
    ...result,
  })}\n`);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function ensureDirectory(directoryPath, mode = 0o750) {
  fs.mkdirSync(directoryPath, { recursive: true, mode });
}

function atomicWriteJson(filePath, value, mode = 0o640) {
  ensureDirectory(path.dirname(filePath));
  const temporaryPath = `${filePath}.tmp-${process.pid}-${crypto.randomBytes(4).toString('hex')}`;
  fs.writeFileSync(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, { mode });
  fs.renameSync(temporaryPath, filePath);
}

function appendJsonLine(filePath, value) {
  ensureDirectory(path.dirname(filePath));
  fs.appendFileSync(filePath, `${JSON.stringify(value)}\n`, { mode: 0o640 });
}

function assertAbsoluteSafePath(candidate, label) {
  if (typeof candidate !== 'string' || !path.isAbsolute(candidate)) {
    throw new Error(`${label} must be an absolute path`);
  }
  const normalized = path.normalize(candidate);
  if (normalized === '/' || normalized === '/root' || normalized === '/home') {
    throw new Error(`${label} is too broad: ${normalized}`);
  }
  if (candidate.includes('\0') || candidate.includes('\n') || candidate.includes('\r')) {
    throw new Error(`${label} contains an unsafe character`);
  }
  return normalized;
}

function assertPathInside(candidate, allowedRoot, label) {
  const normalizedCandidate = assertAbsoluteSafePath(candidate, label);
  const normalizedRoot = assertAbsoluteSafePath(allowedRoot, `${label} root`);
  const relative = path.relative(normalizedRoot, normalizedCandidate);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`${label} must remain inside ${normalizedRoot}`);
  }
  return normalizedCandidate;
}

function localDateParts(timeZone, date = new Date()) {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
  });
  const parts = Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]));
  const weekdayMap = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 };
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    yearMonth: `${parts.year}-${parts.month}`,
    day: Number(parts.day),
    isoWeekday: weekdayMap[parts.weekday],
  };
}

function loadConfig() {
  const config = readJson(CONFIG_PATH);
  if (config.schema_version !== '1.0.0') {
    throw new Error(`Unsupported backup config schema: ${config.schema_version ?? 'missing'}`);
  }
  if (!Array.isArray(config.sources) || config.sources.length === 0) {
    throw new Error('backup-config.json must define at least one source');
  }

  config.paths.repository = assertAbsoluteSafePath(config.paths.repository, 'repository');
  config.paths.password_file = assertAbsoluteSafePath(config.paths.password_file, 'password_file');
  config.paths.state_root = assertAbsoluteSafePath(config.paths.state_root, 'state_root');
  config.paths.restore_root = assertAbsoluteSafePath(config.paths.restore_root, 'restore_root');
  config.paths.evidence_root = assertAbsoluteSafePath(config.paths.evidence_root, 'evidence_root');
  config.paths.log_root = assertAbsoluteSafePath(config.paths.log_root, 'log_root');
  config.paths.cache_root = assertAbsoluteSafePath(config.paths.cache_root, 'cache_root');
  config.paths.n8n_export_root = assertAbsoluteSafePath(config.paths.n8n_export_root, 'n8n_export_root');

  for (const source of config.sources) {
    source.path = assertAbsoluteSafePath(source.path, 'source.path');
    source.required = source.required !== false;
  }

  if (!Number.isInteger(config.retention.daily) || config.retention.daily < 1) {
    throw new Error('retention.daily must be a positive integer');
  }
  if (!Number.isInteger(config.retention.weekly) || config.retention.weekly < 1) {
    throw new Error('retention.weekly must be a positive integer');
  }
  if (config.retention.monthly !== 'manual') {
    throw new Error('retention.monthly must remain "manual"; monthly snapshots are never auto-deleted');
  }

  if (!Number.isInteger(config.calendar.weekly_iso_day) || config.calendar.weekly_iso_day < 1 || config.calendar.weekly_iso_day > 7) {
    throw new Error('calendar.weekly_iso_day must be from 1 through 7');
  }
  if (!Number.isInteger(config.calendar.monthly_day) || config.calendar.monthly_day < 1 || config.calendar.monthly_day > 28) {
    throw new Error('calendar.monthly_day must be from 1 through 28');
  }

  loadedConfig = config;
  return config;
}

function commandResult(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env ?? process.env,
    encoding: 'utf8',
    timeout: options.timeoutMs ?? 12 * 60 * 60 * 1000,
    maxBuffer: options.maxBuffer ?? 128 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    const stderr = String(result.stderr ?? '').trim().slice(-8000);
    const stdout = String(result.stdout ?? '').trim().slice(-4000);
    throw new Error(`${command} exited ${result.status}: ${stderr || stdout || 'no diagnostic output'}`);
  }
  return {
    stdout: String(result.stdout ?? ''),
    stderr: String(result.stderr ?? ''),
    exitCode: result.status,
  };
}

function resticEnvironment(config, repository, passwordFile) {
  return {
    ...process.env,
    RESTIC_REPOSITORY: repository,
    RESTIC_PASSWORD_FILE: passwordFile,
    RESTIC_CACHE_DIR: config.paths.cache_root,
    RESTIC_PROGRESS_FPS: '0.016666',
    TZ: config.timezone,
  };
}

function runRestic(config, repository, passwordFile, args, options = {}) {
  ensureDirectory(config.paths.cache_root);
  return commandResult(config.restic_binary, ['--retry-lock', config.restic.retry_lock, ...args], {
    ...options,
    env: {
      ...resticEnvironment(config, repository, passwordFile),
      ...(options.env ?? {}),
    },
    timeoutMs: options.timeoutMs ?? config.restic.command_timeout_hours * 60 * 60 * 1000,
  });
}

function parseJsonLines(text) {
  const values = [];
  for (const line of String(text).split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) continue;
    try {
      values.push(JSON.parse(trimmed));
    } catch {
      // Some commands mix human-readable output with JSON. Ignore non-JSON lines.
    }
  }
  return values;
}

function parseBackupSummary(stdout) {
  const messages = parseJsonLines(stdout);
  const summary = [...messages].reverse().find((entry) => entry?.message_type === 'summary' && entry?.snapshot_id);
  if (!summary) {
    throw new Error('restic backup completed without a JSON summary or snapshot_id');
  }
  return summary;
}

function runLogPath(config) {
  const date = localDateParts(config.timezone).date;
  return path.join(config.paths.log_root, `${date}.jsonl`);
}

function logEvent(config, event, details = {}) {
  appendJsonLine(runLogPath(config), {
    timestamp: nowIso(),
    worker_version: WORKER_VERSION,
    action: ACTION,
    event,
    ...details,
  });
}

function acquireLock(config) {
  const lockRoot = path.join(config.paths.state_root, 'Locks');
  ensureDirectory(lockRoot);
  const lockPath = path.join(lockRoot, 'backup-operation.lock');
  try {
    fs.mkdirSync(lockPath, { mode: 0o750 });
  } catch (error) {
    if (error?.code !== 'EEXIST') throw error;
    const stat = fs.statSync(lockPath);
    const ageMinutes = (Date.now() - stat.mtimeMs) / 60000;
    if (ageMinutes <= config.lock_stale_after_minutes) {
      throw new Error(`Another backup operation holds the lock (${ageMinutes.toFixed(1)} minutes old)`);
    }
    assertPathInside(lockPath, lockRoot, 'stale lock');
    fs.rmSync(lockPath, { recursive: true, force: true });
    fs.mkdirSync(lockPath, { mode: 0o750 });
    logEvent(config, 'stale_lock_replaced', { age_minutes: Number(ageMinutes.toFixed(1)) });
  }
  atomicWriteJson(path.join(lockPath, 'owner.json'), {
    pid: process.pid,
    hostname: os.hostname(),
    action: ACTION,
    started_at: nowIso(),
  });
  return () => {
    if (fs.existsSync(lockPath)) {
      assertPathInside(lockPath, lockRoot, 'lock cleanup');
      fs.rmSync(lockPath, { recursive: true, force: true });
    }
  };
}

function getSourcePaths(config) {
  const missingRequired = [];
  const available = [];
  const missingOptional = [];
  for (const source of config.sources) {
    if (fs.existsSync(source.path)) {
      available.push(source.path);
    } else if (source.required) {
      missingRequired.push(source.path);
    } else {
      missingOptional.push(source.path);
    }
  }
  if (missingRequired.length > 0) {
    throw new Error(`Required backup source(s) missing: ${missingRequired.join(', ')}`);
  }
  return { available, missingOptional };
}

function verifyPasswordFile(config, passwordFile = config.paths.password_file) {
  if (!fs.existsSync(passwordFile)) {
    throw new Error(`Restic password file is missing: ${passwordFile}`);
  }
  const stat = fs.statSync(passwordFile);
  if (!stat.isFile() || stat.size < 16) {
    throw new Error('Restic password file must be a regular file containing at least 16 bytes');
  }
  if ((stat.mode & 0o007) !== 0) {
    throw new Error(`Restic password file is accessible to other users; expected mode 600 or 640: ${passwordFile}`);
  }
}

function repositoryIsInitialized(config, repository, passwordFile) {
  try {
    runRestic(config, repository, passwordFile, ['cat', 'config'], { timeoutMs: 2 * 60 * 1000 });
    return true;
  } catch {
    return false;
  }
}

function ensureLocalRepository(config) {
  verifyPasswordFile(config);
  ensureDirectory(config.paths.repository);
  if (repositoryIsInitialized(config, config.paths.repository, config.paths.password_file)) return;
  if (fs.readdirSync(config.paths.repository).length > 0) {
    throw new Error(`Repository path is non-empty but not a readable restic repository: ${config.paths.repository}`);
  }
  runRestic(config, config.paths.repository, config.paths.password_file, ['init', '--repository-version', '2']);
  logEvent(config, 'local_repository_initialized', { repository_role: 'server' });
}

function destinationConfig(config, destination) {
  const passwordFile = destination.password_file || config.paths.password_file;
  verifyPasswordFile(config, passwordFile);
  if (typeof destination.repository !== 'string' || destination.repository.trim() === '') {
    throw new Error('Destination repository is not configured');
  }
  return { repository: destination.repository, passwordFile };
}

function ensureDestinationRepository(config, destination) {
  const { repository, passwordFile } = destinationConfig(config, destination);
  if (repositoryIsInitialized(config, repository, passwordFile)) {
    return { repository, passwordFile, initialized: false };
  }
  runRestic(config, repository, passwordFile, [
    'init',
    '--from-repo', config.paths.repository,
    '--from-password-file', config.paths.password_file,
    '--copy-chunker-params',
  ]);
  return { repository, passwordFile, initialized: true };
}

function writeExcludeFile(config) {
  const exclusionFile = path.join(config.paths.state_root, 'restic-excludes.txt');
  const mandatory = [
    config.paths.repository,
    `${config.paths.repository}/**`,
    config.paths.restore_root,
    `${config.paths.restore_root}/**`,
    config.paths.cache_root,
    `${config.paths.cache_root}/**`,
  ];
  if (config.workstation?.enabled && typeof config.workstation.repository === 'string' && path.isAbsolute(config.workstation.repository)) {
    mandatory.push(config.workstation.repository, `${config.workstation.repository}/**`);
  }
  const lines = [...new Set([...(config.excludes ?? []), ...mandatory])];
  fs.writeFileSync(exclusionFile, `${lines.join('\n')}\n`, { mode: 0o640 });
  return exclusionFile;
}

function runN8nExports(config, runId) {
  if (!config.n8n_export.enabled) {
    return { enabled: false, status: 'disabled' };
  }
  const exportRoot = path.join(config.paths.n8n_export_root, runId);
  assertPathInside(exportRoot, config.paths.n8n_export_root, 'n8n export directory');
  ensureDirectory(exportRoot);
  const workflowsDirectory = path.join(exportRoot, 'workflows');
  const credentialsDirectory = path.join(exportRoot, 'credentials');
  const entitiesDirectory = path.join(exportRoot, 'entities');
  ensureDirectory(workflowsDirectory);
  ensureDirectory(credentialsDirectory);
  if (config.n8n_export.entities_enabled) ensureDirectory(entitiesDirectory);

  const cli = config.n8n_export.cli_binary || 'n8n';
  const workflowResult = commandResult(cli, [
    'export:workflow',
    '--backup',
    `--output=${workflowsDirectory}`,
  ], { timeoutMs: config.n8n_export.timeout_minutes * 60 * 1000 });

  const credentialResult = commandResult(cli, [
    'export:credentials',
    '--backup',
    `--output=${credentialsDirectory}`,
  ], { timeoutMs: config.n8n_export.timeout_minutes * 60 * 1000 });

  let entitiesResult = null;
  if (config.n8n_export.entities_enabled) {
    entitiesResult = commandResult(cli, [
      'export:entities',
      `--outputDir=${entitiesDirectory}`,
      `--includeExecutionHistoryDataTables=${config.n8n_export.include_execution_history_data_tables === true}`,
    ], { timeoutMs: config.n8n_export.timeout_minutes * 60 * 1000 });
  }

  const manifest = {
    schema_version: '1.0.0',
    created_at: nowIso(),
    run_id: runId,
    workflow_export_completed: workflowResult.exitCode === 0,
    credential_export_completed: credentialResult.exitCode === 0,
    entity_export_enabled: config.n8n_export.entities_enabled === true,
    entity_export_completed: entitiesResult ? entitiesResult.exitCode === 0 : null,
    execution_history_data_tables_included: config.n8n_export.include_execution_history_data_tables === true,
    decrypted_credentials_exported: false,
  };
  atomicWriteJson(path.join(exportRoot, 'export-manifest.json'), manifest);
  return { enabled: true, status: 'completed', export_root: exportRoot };
}

// KI-124: an explicit run id may never look like a calendar date, because a calendar date is
// how a scheduled daily record is named. Rejecting that shape is what makes collision with,
// or replacement of, an existing daily record impossible rather than merely unlikely.
function parseAdhocRunId(value) {
  if (value === null || value === undefined || value === '') return null;
  const id = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(id)) {
    throw new Error('An explicit run id may not be a calendar date; those are reserved for scheduled daily runs');
  }
  if (!/^adhoc-[0-9A-Za-z][0-9A-Za-z-]{0,47}$/.test(id)) {
    throw new Error('An explicit run id must match adhoc-<token> using letters, digits and hyphens');
  }
  return id;
}

// KI-124: the one pending-run file for an explicit ad-hoc id. Scheduled validation never sees
// these, because pendingRunFiles() matches only YYYY-MM-DD.json, and an ad-hoc id can never
// take that shape.
function adhocRunFiles(config, adhocRunId) {
  const filePath = runFile(config, adhocRunId);
  if (!fs.existsSync(filePath)) return [];
  const status = readJson(filePath).status;
  return [
    'creating',
    'backup_created',
    'validation_failed',
    'local_validated_waiting_workstation',
    'validation_passed_pending_retention',
    'retention_failed',
  ].includes(status) ? [filePath] : [];
}

function runFile(config, runId) {
  return path.join(config.paths.state_root, 'Runs', `${runId}.json`);
}

function sentinelFile(config, runId) {
  return path.join(config.paths.state_root, 'Sentinels', `${runId}.json`);
}

function makeSentinel(config, runId) {
  const sentinelPath = sentinelFile(config, runId);
  if (!fs.existsSync(sentinelPath)) {
    atomicWriteJson(sentinelPath, {
      schema_version: '1.0.0',
      purpose: 'Second Brain backup restore sentinel',
      run_id: runId,
      created_at: nowIso(),
      nonce: crypto.randomBytes(48).toString('base64url'),
    }, 0o640);
  }
  const hash = crypto.createHash('sha256').update(fs.readFileSync(sentinelPath)).digest('hex');
  return { path: sentinelPath, sha256: hash };
}

function dueTiers(config, localDate) {
  const tiers = ['daily'];
  if (localDate.isoWeekday === config.calendar.weekly_iso_day) tiers.push('weekly');
  if (localDate.day === config.calendar.monthly_day) tiers.push('monthly');
  return tiers;
}

function createSnapshot(config, sources, exclusionFile, runId, tier) {
  const args = [
    'backup',
    '--json',
    '--host', config.snapshot_host,
    '--tag', 'second-brain',
    '--tag', tier,
    '--tag', `run-${runId}`,
    '--exclude-file', exclusionFile,
    '--exclude-caches',
    ...sources,
  ];
  const result = runRestic(config, config.paths.repository, config.paths.password_file, args);
  const summary = parseBackupSummary(result.stdout);
  return {
    tier,
    snapshot_id: summary.snapshot_id,
    files_new: summary.files_new ?? null,
    files_changed: summary.files_changed ?? null,
    files_unmodified: summary.files_unmodified ?? null,
    total_files_processed: summary.total_files_processed ?? null,
    total_bytes_processed: summary.total_bytes_processed ?? null,
    data_added: summary.data_added ?? null,
    backup_started_at: summary.backup_start ?? null,
    backup_completed_at: summary.backup_end ?? nowIso(),
  };
}

function snapshotList(config, repository, passwordFile) {
  const result = runRestic(config, repository, passwordFile, ['snapshots', '--json']);
  const parsed = JSON.parse(result.stdout);
  if (!Array.isArray(parsed)) throw new Error('restic snapshots --json did not return an array');
  return parsed;
}

function findCopiedSnapshot(config, repository, passwordFile, runId, tier) {
  const matches = snapshotList(config, repository, passwordFile)
    .filter((snapshot) => Array.isArray(snapshot.tags)
      && snapshot.tags.includes(`run-${runId}`)
      && snapshot.tags.includes(tier))
    .sort((a, b) => String(b.time).localeCompare(String(a.time)));
  if (matches.length === 0) {
    throw new Error(`Destination repository does not contain the ${tier} snapshot for run ${runId}`);
  }
  return matches[0].id || matches[0].short_id;
}

function findRunTierSnapshots(config, repository, passwordFile, runId, tier) {
  return snapshotList(config, repository, passwordFile)
    .filter((snapshot) => Array.isArray(snapshot.tags)
      && snapshot.tags.includes(`run-${runId}`)
      && snapshot.tags.includes(tier))
    .sort((a, b) => String(b.time).localeCompare(String(a.time)));
}

function snapshotRecordFromExisting(snapshot, tier) {
  return {
    tier,
    snapshot_id: snapshot.id || snapshot.short_id,
    files_new: null,
    files_changed: null,
    files_unmodified: null,
    total_files_processed: null,
    total_bytes_processed: null,
    data_added: null,
    backup_started_at: null,
    backup_completed_at: snapshot.time || nowIso(),
    reconciled_existing_snapshot: true,
  };
}

// KI-124: a snapshot carrying this run's tags is NOT evidence that this run completed. restic commits a
// partial snapshot on exit 3 (unreadable sources), so a run that ended in creation_failed can leave a
// tagged-but-incomplete snapshot behind. Adoption must therefore be refused whenever the previous
// creation attempt for this run id is known to have failed. Fail closed: when in doubt, do not adopt.
function runCreationIsKnownFailed(record, priorStatus, priorError) {
  if (priorStatus === 'creation_failed') return true;
  if (priorError) return true;
  if (record && record.creation_resumed_from_status === 'creation_failed') return true;
  if (record && record.creation_error) return true;
  return false;
}

function checkRepository(config, repository, passwordFile, fullRead = false, readSubset = true) {
  const args = ['check', '--json'];
  if (fullRead) {
    args.push('--read-data');
  } else if (readSubset && config.validation.read_data_subset) {
    args.push(`--read-data-subset=${config.validation.read_data_subset}`);
  }
  const result = runRestic(config, repository, passwordFile, args);
  const summaries = parseJsonLines(result.stdout);
  const summary = [...summaries].reverse().find((entry) => entry?.message_type === 'summary');
  if (summary && Number(summary.num_errors ?? 0) > 0) {
    throw new Error(`restic check reported ${summary.num_errors} error(s)`);
  }
  return { full_read: fullRead, read_data_subset: fullRead ? null : config.validation.read_data_subset };
}

function snapshotStats(config, repository, passwordFile, snapshotId) {
  const result = runRestic(config, repository, passwordFile, ['stats', '--json', snapshotId]);
  const parsed = JSON.parse(result.stdout);
  return {
    total_size: parsed.total_size ?? null,
    total_file_count: parsed.total_file_count ?? null,
    snapshots_count: parsed.snapshots_count ?? null,
  };
}

function removeGeneratedDirectory(candidate, allowedRoot, label) {
  if (!fs.existsSync(candidate)) return;
  assertPathInside(candidate, allowedRoot, label);
  fs.rmSync(candidate, { recursive: true, force: true });
}

function verifySentinelRestore(config, repository, passwordFile, snapshotId, runId, expectedSha256, role, tier) {
  const target = path.join(config.paths.restore_root, runId, `${role}-${tier}-sentinel`);
  removeGeneratedDirectory(target, config.paths.restore_root, 'sentinel restore cleanup');
  ensureDirectory(target);
  const sentinel = sentinelFile(config, runId);
  const includePath = sentinel;
  runRestic(config, repository, passwordFile, [
    'restore', snapshotId,
    '--target', target,
    '--include', includePath,
  ]);
  const restoredPath = path.join(target, sentinel.replace(/^\/+/, ''));
  if (!fs.existsSync(restoredPath)) {
    throw new Error(`Sentinel was not restored from ${role}/${tier} snapshot ${snapshotId}`);
  }
  const actualSha256 = crypto.createHash('sha256').update(fs.readFileSync(restoredPath)).digest('hex');
  if (actualSha256 !== expectedSha256) {
    throw new Error(`Sentinel hash mismatch for ${role}/${tier} snapshot ${snapshotId}`);
  }
  removeGeneratedDirectory(target, config.paths.restore_root, 'sentinel restore cleanup');
  return { restored: true, sha256_match: true };
}

function availableBytes(directoryPath) {
  ensureDirectory(directoryPath);
  const stats = fs.statfsSync(directoryPath);
  return Number(stats.bavail) * Number(stats.bsize);
}

function fullRestoreTest(config, repository, passwordFile, snapshotId, runId, role) {
  const stats = snapshotStats(config, repository, passwordFile, snapshotId);
  const requiredBytes = stats.total_size == null
    ? config.validation.minimum_full_restore_free_bytes
    : Math.ceil(Number(stats.total_size) * config.validation.full_restore_free_space_multiplier);
  const freeBytes = availableBytes(config.paths.restore_root);
  if (freeBytes < requiredBytes) {
    throw new Error(`Full restore test needs ${requiredBytes} free bytes but only ${freeBytes} are available`);
  }

  const target = path.join(config.paths.restore_root, runId, `${role}-full-restore`);
  removeGeneratedDirectory(target, config.paths.restore_root, 'full restore cleanup');
  ensureDirectory(target);
  const startedAt = nowIso();
  runRestic(config, repository, passwordFile, ['restore', snapshotId, '--target', target]);

  const charterPath = path.join(target, 'Obsidian', '90 System', 'AI Second Brain Charter.md');
  if (config.validation.require_charter_in_full_restore && !fs.existsSync(charterPath)) {
    throw new Error('Full restore completed but the canonical AI Second Brain Charter was not restored');
  }

  const sentinelRoot = path.join(target, config.paths.state_root.replace(/^\/+/, ''), 'Sentinels');
  if (!fs.existsSync(sentinelRoot)) {
    throw new Error('Full restore completed but the backup sentinel directory was not restored');
  }

  const result = {
    test_type: 'full_snapshot_restore',
    repository_role: role,
    snapshot_id: snapshotId,
    started_at: startedAt,
    completed_at: nowIso(),
    restore_command_completed: true,
    charter_restored: fs.existsSync(charterPath),
    snapshot_total_size: stats.total_size,
    snapshot_total_file_count: stats.total_file_count,
    available_bytes_before_test: freeBytes,
  };
  removeGeneratedDirectory(target, config.paths.restore_root, 'full restore cleanup');
  return result;
}

function copySnapshots(config, destination, snapshotIds) {
  const { repository, passwordFile } = ensureDestinationRepository(config, destination);
  runRestic(config, repository, passwordFile, [
    'copy',
    '--from-repo', config.paths.repository,
    '--from-password-file', config.paths.password_file,
    ...snapshotIds,
  ]);
  return { repository, passwordFile };
}

function applyRetention(config, repository, passwordFile, tiers) {
  const prunedTiers = [];
  if (tiers.includes('daily')) {
    runRestic(config, repository, passwordFile, [
      'forget', '--host', config.snapshot_host, '--tag', 'daily', '--keep-last', String(config.retention.daily),
    ]);
    prunedTiers.push('daily');
  }
  if (tiers.includes('weekly')) {
    runRestic(config, repository, passwordFile, [
      'forget', '--host', config.snapshot_host, '--tag', 'weekly', '--keep-last', String(config.retention.weekly),
    ]);
    prunedTiers.push('weekly');
  }
  if (prunedTiers.length > 0) {
    runRestic(config, repository, passwordFile, ['prune']);
    checkRepository(config, repository, passwordFile, false);
  }
  return { pruned_tiers: prunedTiers, monthly_pruned: false };
}

function evidencePath(config, filename) {
  return path.join(config.paths.evidence_root, filename);
}

function writeEvidence(config, filename, content) {
  const target = evidencePath(config, filename);
  assertPathInside(target, config.paths.evidence_root, 'backup evidence');
  atomicWriteJson(target, {
    schema_version: '1.0.0',
    generated_by: `second-brain-backup-worker/${WORKER_VERSION}`,
    ...content,
  });
  return target;
}

async function sendNtfy(config, title, body, priority = 'default', tags = 'floppy_disk') {
  if (!config.notifications?.enabled) return { sent: false, reason: 'disabled' };
  const baseUrl = String(config.notifications.base_url).replace(/\/+$/, '');
  const topic = encodeURIComponent(String(config.notifications.topic));
  const response = await fetch(`${baseUrl}/${topic}`, {
    method: 'POST',
    headers: {
      Title: cleanHeader(title),
      Priority: cleanHeader(priority),
      Tags: cleanHeader(tags),
      'Content-Type': 'text/plain; charset=utf-8',
    },
    body: String(body),
    signal: AbortSignal.timeout(config.notifications.timeout_seconds * 1000),
  });
  if (!response.ok) {
    throw new Error(`ntfy returned HTTP ${response.status}`);
  }
  return { sent: true };
}

async function bestEffortNtfy(config, title, body, priority = 'default', tags = 'floppy_disk') {
  try {
    return await sendNtfy(config, title, body, priority, tags);
  } catch (error) {
    try {
      logEvent(config, 'notification_failed', { error: asError(error), title: cleanHeader(title) });
    } catch {
      // Notification failure must not rewrite a completed backup result.
    }
    return { sent: false, error: asError(error) };
  }
}

async function notifyFailure(config, action, error) {
  try {
    const retentionMessage = error?.retentionAuthorized === true
      ? 'Restore validation had already authorized retention. Inspect the run record before retrying.'
      : 'No retention deletion was authorized by this failed run.';
    return await sendNtfy(
      config,
      'Second Brain backup failed',
      `${action} failed at ${nowIso()}. ${asError(error).message}\n\n${retentionMessage}`,
      'high',
      'warning,floppy_disk',
    );
  } catch (notificationError) {
    return { sent: false, error: asError(notificationError) };
  }
}

async function preflight(config) {
  const sourceResult = getSourcePaths(config);
  verifyPasswordFile(config);
  for (const directory of [
    config.paths.state_root,
    config.paths.restore_root,
    config.paths.evidence_root,
    config.paths.log_root,
    config.paths.cache_root,
    config.paths.n8n_export_root,
    path.dirname(config.paths.repository),
  ]) {
    ensureDirectory(directory);
    fs.accessSync(directory, fs.constants.R_OK | fs.constants.W_OK);
  }
  const resticVersion = commandResult(config.restic_binary, ['version'], { timeoutMs: 30_000 }).stdout.trim();
  ensureLocalRepository(config);
  checkRepository(config, config.paths.repository, config.paths.password_file, false, false);
  return {
    status: 'completed',
    summary: 'Backup preflight passed',
    restic_version: resticVersion,
    source_count: sourceResult.available.length,
    missing_optional_sources: sourceResult.missingOptional,
    repository_initialized: true,
    monthly_auto_delete: false,
  };
}

async function createBackups(config, adhocRunId = null) {
  const release = acquireLock(config);
  let recordPath = null;
  let record = null;
  try {
    const localDate = localDateParts(config.timezone);
    const runId = adhocRunId ?? localDate.date;
    recordPath = runFile(config, runId);

    await preflight(config);
    const { available, missingOptional } = getSourcePaths(config);
    const sentinel = makeSentinel(config, runId);
    const exclusionFile = writeExcludeFile(config);
    // KI-124: an ad-hoc proof run uses its own tier tag. Daily/weekly retention matches on the
    // 'daily' and 'weekly' tags only, so an ad-hoc snapshot can never cause a historical snapshot
    // to be forgotten - not by this run, and not by any later scheduled run either.
    const tiers = adhocRunId ? [ADHOC_TIER] : dueTiers(config, localDate);

    // KI-124: the resume path below clears the previous attempt's status and error. Carry both out
    // of that branch so the reconcile loop can refuse to adopt a snapshot left by a failed creation.
    let priorCreationStatus = null;
    let priorCreationError = null;

    if (fs.existsSync(recordPath)) {
      record = readJson(recordPath);
      if (['backup_created', 'local_validated_waiting_workstation', 'validation_passed_pending_retention', 'validated'].includes(record.status)) {
        return {
          status: 'skipped',
          summary: `Backup run ${runId} already exists with status ${record.status}`,
          run_id: runId,
          existing_status: record.status,
          snapshots: record.snapshots ?? [],
        };
      }
      if (!['creating', 'creation_failed', 'validation_failed', 'retention_failed'].includes(record.status)) {
        throw new Error(`Backup run ${runId} has unsupported resumable status ${record.status}`);
      }
      priorCreationStatus = record.status;
      priorCreationError = record.creation_error ?? null;
      record.creation_resumed_from_status = priorCreationStatus;
      record.status = 'creating';
      record.creation_resumed_at = nowIso();
      record.creation_error = null;
      record.tiers_due = Array.isArray(record.tiers_due) && record.tiers_due.length > 0 ? record.tiers_due : tiers;
      record.sentinel = record.sentinel || sentinel;
      record.snapshots = Array.isArray(record.snapshots) ? record.snapshots : [];
      record.missing_optional_sources = missingOptional;
      if (!record.n8n_export || record.n8n_export.status !== 'completed') {
        record.n8n_export = runN8nExports(config, runId);
      }
      atomicWriteJson(recordPath, record);
    } else {
      const n8nExport = runN8nExports(config, runId);
      record = {
        schema_version: '1.0.0',
        run_id: runId,
        status: 'creating',
        created_at: nowIso(),
        timezone: config.timezone,
        tiers_due: tiers,
        missing_optional_sources: missingOptional,
        sentinel,
        n8n_export: n8nExport,
        snapshots: [],
        retention_authorized: false,
        monthly_auto_delete: false,
        ...(adhocRunId ? { adhoc_proof_run: true } : {}),
      };
      atomicWriteJson(recordPath, record);
    }

    for (const tier of record.tiers_due) {
      const alreadyRecorded = record.snapshots.find((snapshot) => snapshot.tier === tier && snapshot.snapshot_id);
      if (alreadyRecorded) continue;

      const existingSnapshots = findRunTierSnapshots(
        config,
        config.paths.repository,
        config.paths.password_file,
        runId,
        tier,
      );
      if (existingSnapshots.length > 0 && runCreationIsKnownFailed(record, priorCreationStatus, priorCreationError)) {
        // KI-124: refuse the adoption, preserve the snapshot, and fall through to a genuine backup.
        const refusedIds = existingSnapshots.map((snapshot) => snapshot.id || snapshot.short_id);
        record.refused_incomplete_adoptions = [
          ...(record.refused_incomplete_adoptions || []),
          {
            tier,
            refused_snapshot_ids: refusedIds,
            prior_creation_status: priorCreationStatus,
            refused_at: nowIso(),
            action: 'preserved_no_deletion_fresh_snapshot_created',
          },
        ];
        atomicWriteJson(recordPath, record);
        logEvent(config, 'snapshot_adoption_refused_incomplete_run', {
          run_id: runId,
          tier,
          refused_snapshot_ids: refusedIds,
          prior_creation_status: priorCreationStatus,
          reason: 'previous_creation_attempt_failed_snapshot_completeness_unproven',
          phase: 'create',
        });
      } else if (existingSnapshots.length > 0) {
        const reconciled = snapshotRecordFromExisting(existingSnapshots[0], tier);
        record.snapshots.push(reconciled);
        record.reconciled_snapshot_count = Number(record.reconciled_snapshot_count || 0) + 1;
        if (existingSnapshots.length > 1) {
          record.duplicate_snapshot_candidates = [
            ...(record.duplicate_snapshot_candidates || []),
            {
              tier,
              count: existingSnapshots.length,
              retained_snapshot_ids: existingSnapshots.map((snapshot) => snapshot.id || snapshot.short_id),
              action: 'preserved_for_review_no_automatic_deletion',
            },
          ];
        }
        atomicWriteJson(recordPath, record);
        logEvent(config, 'snapshot_reconciled_after_interruption', {
          run_id: runId,
          tier,
          snapshot_id: reconciled.snapshot_id,
          matching_snapshot_count: existingSnapshots.length,
        });
        continue;
      }

      const snapshot = createSnapshot(config, available, exclusionFile, runId, tier);
      record.snapshots.push(snapshot);
      atomicWriteJson(recordPath, record);
      logEvent(config, 'snapshot_created', { run_id: runId, tier, snapshot_id: snapshot.snapshot_id });
    }

    const missingTiers = record.tiers_due.filter(
      (tier) => !record.snapshots.some((snapshot) => snapshot.tier === tier && snapshot.snapshot_id),
    );
    if (missingTiers.length > 0) {
      throw new Error(`Backup run ${runId} is missing required tier snapshot(s): ${missingTiers.join(', ')}`);
    }

    record.status = 'backup_created';
    record.backup_completed_at = nowIso();
    record.creation_error = null;
    atomicWriteJson(recordPath, record);

    if (config.notifications.successful_backup_created) {
      await bestEffortNtfy(
        config,
        'Second Brain backup created',
        `Created or reconciled ${record.snapshots.length} encrypted snapshot(s) for ${runId}. Validation is pending; no retention deletion has occurred.`,
        'low',
        'floppy_disk',
      );
    }
    return {
      status: 'completed',
      summary: `Created or reconciled ${record.snapshots.length} encrypted snapshot(s); validation is pending`,
      run_id: runId,
      tiers: record.tiers_due,
      snapshots: record.snapshots,
      validation_pending: true,
      retention_performed: false,
      monthly_auto_delete: false,
    };
  } catch (error) {
    if (recordPath && record) {
      record.status = 'creation_failed';
      record.creation_failed_at = nowIso();
      record.creation_error = asError(error);
      record.retention_authorized = false;
      try {
        atomicWriteJson(recordPath, record);
        logEvent(config, 'backup_creation_failed', { run_id: record.run_id, error: asError(error) });
      } catch {}
    }
    throw error;
  } finally {
    release();
  }
}

function pendingRunFiles(config) {
  const root = path.join(config.paths.state_root, 'Runs');
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root)
    .filter((name) => /^\d{4}-\d{2}-\d{2}\.json$/.test(name))
    .sort()
    .map((name) => path.join(root, name))
    .filter((filePath) => {
      const status = readJson(filePath).status;
      return [
        'creating',
        'backup_created',
        'validation_failed',
        'local_validated_waiting_workstation',
        'validation_passed_pending_retention',
        'retention_failed',
      ].includes(status);
    });
}

function validateLocalRun(config, record) {
  const check = checkRepository(config, config.paths.repository, config.paths.password_file, false);
  const validations = [];
  for (const snapshot of record.snapshots) {
    const sentinel = verifySentinelRestore(
      config,
      config.paths.repository,
      config.paths.password_file,
      snapshot.snapshot_id,
      record.run_id,
      record.sentinel.sha256,
      'server',
      snapshot.tier,
    );
    validations.push({
      tier: snapshot.tier,
      snapshot_id: snapshot.snapshot_id,
      repository_check: check,
      sentinel_restore: sentinel,
      stats: snapshotStats(config, config.paths.repository, config.paths.password_file, snapshot.snapshot_id),
    });
  }
  return validations;
}

function validateWorkstationRun(config, record) {
  if (!config.workstation.enabled) {
    return { enabled: false, completed: false, reason: 'workstation_repository_not_configured' };
  }
  const copied = copySnapshots(config, config.workstation, record.snapshots.map((snapshot) => snapshot.snapshot_id));
  const check = checkRepository(config, copied.repository, copied.passwordFile, false);
  const validations = [];
  for (const snapshot of record.snapshots) {
    const destinationSnapshotId = findCopiedSnapshot(
      config,
      copied.repository,
      copied.passwordFile,
      record.run_id,
      snapshot.tier,
    );
    const sentinel = verifySentinelRestore(
      config,
      copied.repository,
      copied.passwordFile,
      destinationSnapshotId,
      record.run_id,
      record.sentinel.sha256,
      'workstation',
      snapshot.tier,
    );
    validations.push({
      tier: snapshot.tier,
      source_snapshot_id: snapshot.snapshot_id,
      destination_snapshot_id: destinationSnapshotId,
      repository_check: check,
      sentinel_restore: sentinel,
    });
  }
  return { enabled: true, completed: true, repository: copied.repository, passwordFile: copied.passwordFile, validations };
}

async function validateBackups(config, adhocRunId = null) {
  const release = acquireLock(config);
  try {
    await preflight(config);
    const allFiles = adhocRunId ? adhocRunFiles(config, adhocRunId) : pendingRunFiles(config);
    if (allFiles.length === 0) {
      return {
        status: 'skipped',
        summary: 'No backup run is waiting for validation',
        retention_performed: false,
      };
    }

    const eligibleFiles = [];
    const incompleteCreations = [];
    for (const filePath of allFiles) {
      const record = readJson(filePath);
      if (record.status !== 'creating') {
        eligibleFiles.push(filePath);
        continue;
      }

      const tiers = Array.isArray(record.tiers_due) ? record.tiers_due : [];
      const reconciledSnapshots = Array.isArray(record.snapshots) ? [...record.snapshots] : [];
      for (const tier of tiers) {
        if (reconciledSnapshots.some((snapshot) => snapshot.tier === tier && snapshot.snapshot_id)) continue;
        const matches = findRunTierSnapshots(
          config,
          config.paths.repository,
          config.paths.password_file,
          record.run_id,
          tier,
        );
        if (matches.length > 0 && runCreationIsKnownFailed(record, record.status, record.creation_error)) {
          // KI-124 (second site): same defect on the validation path. Refusing leaves the tier missing,
          // and the existing else-branch below terminalizes the run as creation_failed and notifies.
          logEvent(config, 'snapshot_adoption_refused_incomplete_run', {
            run_id: record.run_id,
            tier,
            refused_snapshot_ids: matches.map((snapshot) => snapshot.id || snapshot.short_id),
            prior_creation_status: record.creation_resumed_from_status ?? record.status,
            reason: 'previous_creation_attempt_failed_snapshot_completeness_unproven',
            phase: 'validate',
          });
          continue;
        }
        if (matches.length > 0) reconciledSnapshots.push(snapshotRecordFromExisting(matches[0], tier));
      }
      record.snapshots = reconciledSnapshots;

      const missing = tiers.filter(
        (tier) => !record.snapshots.some((snapshot) => snapshot.tier === tier && snapshot.snapshot_id),
      );
      if (tiers.length > 0 && missing.length === 0) {
        record.status = 'backup_created';
        record.backup_completed_at = record.backup_completed_at || nowIso();
        record.reconciled_after_interruption_at = nowIso();
        atomicWriteJson(filePath, record);
        eligibleFiles.push(filePath);
        logEvent(config, 'interrupted_creation_reconciled_for_validation', {
          run_id: record.run_id,
          snapshot_count: record.snapshots.length,
        });
      } else {
        record.status = 'creation_failed';
        record.creation_failed_at = nowIso();
        record.creation_error = {
          message: `Interrupted backup creation is incomplete; missing tier snapshot(s): ${missing.join(', ') || 'unknown'}`,
          name: 'InterruptedBackupCreation',
        };
        record.retention_authorized = false;
        atomicWriteJson(filePath, record);
        incompleteCreations.push({ run_id: record.run_id, missing_tiers: missing });
        logEvent(config, 'interrupted_creation_terminalized', {
          run_id: record.run_id,
          missing_tiers: missing,
        });
      }
    }

    if (incompleteCreations.length > 0) {
      await bestEffortNtfy(
        config,
        'Second Brain backup creation needs attention',
        `${incompleteCreations.length} interrupted backup run(s) were terminalized as creation_failed. No retention deletion was performed.`,
        'high',
        'warning,floppy_disk',
      );
    }

    if (eligibleFiles.length === 0) {
      return {
        status: incompleteCreations.length > 0 ? 'warning' : 'skipped',
        summary: incompleteCreations.length > 0
          ? 'Interrupted backup run(s) were terminalized; no complete run is ready for validation'
          : 'No complete backup run is waiting for validation',
        incomplete_creations: incompleteCreations,
        retention_performed: false,
      };
    }

    const results = [];
    const batchRecords = [];
    let waitingForWorkstation = false;
    let workstationForRetention = null;
    const retentionTiers = new Set();

    for (const filePath of eligibleFiles) {
      const record = readJson(filePath);
      try {
        if (record.status !== 'validation_passed_pending_retention' && record.status !== 'retention_failed') {
          const localValidations = validateLocalRun(config, record);
          record.local_validation = {
            status: 'passed',
            completed_at: nowIso(),
            snapshots: localValidations,
          };
          writeEvidence(config, 'server-daily.json', {
            evidence_type: 'server-daily',
            status: 'verified',
            completed_at: nowIso(),
            run_id: record.run_id,
            snapshot_ids: record.snapshots.map((snapshot) => snapshot.snapshot_id),
            validated_tiers: record.snapshots.map((snapshot) => snapshot.tier),
            repository_check: 'passed',
            sentinel_restore: 'passed',
            secrets_included_in_evidence: false,
          });

          const monthlySnapshot = record.snapshots.find((snapshot) => snapshot.tier === 'monthly');
          if (monthlySnapshot && config.validation.full_restore_on_monthly_snapshot) {
            const restoreResult = fullRestoreTest(
              config,
              config.paths.repository,
              config.paths.password_file,
              monthlySnapshot.snapshot_id,
              record.run_id,
              'server',
            );
            record.full_restore_test = restoreResult;
            writeEvidence(config, 'restore-test.json', {
              evidence_type: 'restore-test',
              status: 'verified',
              completed_at: nowIso(),
              run_id: record.run_id,
              ...restoreResult,
              secrets_included_in_evidence: false,
            });
          }

          const workstation = validateWorkstationRun(config, record);
          record.workstation_validation = workstation;
          if (!workstation.completed && config.retention.require_workstation_validation_before_local_prune) {
            record.status = 'local_validated_waiting_workstation';
            record.retention_authorized = false;
            atomicWriteJson(filePath, record);
            waitingForWorkstation = true;
            results.push({
              run_id: record.run_id,
              status: record.status,
              local_validation: 'passed',
              workstation_validation: 'not_configured',
              retention_performed: false,
            });
            continue;
          }

          if (workstation.completed) {
            workstationForRetention = workstation;
            writeEvidence(config, 'workstation-daily.json', {
              evidence_type: 'workstation-daily',
              status: 'verified',
              completed_at: nowIso(),
              run_id: record.run_id,
              validated_tiers: record.snapshots.map((snapshot) => snapshot.tier),
              repository_check: 'passed',
              sentinel_restore: 'passed',
              secrets_included_in_evidence: false,
            });
          }

          record.retention_authorized = true;
          record.retention_authorized_at = nowIso();
          record.status = 'validation_passed_pending_retention';
          record.validation_passed_at = nowIso();
          delete record.validation_error;
          atomicWriteJson(filePath, record);
        } else if (record.workstation_validation?.completed) {
          workstationForRetention = record.workstation_validation;
        }

        for (const snapshot of record.snapshots || []) {
          if (snapshot.tier !== 'monthly') retentionTiers.add(snapshot.tier);
        }
        batchRecords.push({ filePath, record });
        results.push({
          run_id: record.run_id,
          status: 'validation_passed_pending_retention',
          local_validation: 'passed',
          workstation_validation: record.workstation_validation?.completed ? 'passed' : 'not_required',
          retention_performed: false,
        });
      } catch (error) {
        record.status = 'validation_failed';
        record.validation_failed_at = nowIso();
        record.validation_error = asError(error);
        record.retention_authorized = false;
        atomicWriteJson(filePath, record);
        logEvent(config, 'validation_failed_before_retention', { run_id: record.run_id, error: asError(error) });
        throw error;
      }
    }

    if (waitingForWorkstation) {
      const notification = await bestEffortNtfy(
        config,
        'Second Brain backup retention is on hold',
        'The server backup passed its restore test, but the workstation repository is not configured or not yet validated. All older daily and weekly snapshots were kept.',
        'default',
        'warning,floppy_disk',
      );
      return {
        status: 'warning',
        summary: 'Local validation passed, but retention is held until every pending run has a validated workstation copy',
        results,
        incomplete_creations: incompleteCreations,
        notification,
        retention_performed: false,
        monthly_auto_delete: false,
      };
    }

    if (batchRecords.length === 0) {
      return {
        status: incompleteCreations.length > 0 ? 'warning' : 'skipped',
        summary: 'No fully validated backup run is ready for retention',
        results,
        incomplete_creations: incompleteCreations,
        retention_performed: false,
      };
    }

    const tiers = [...retentionTiers];
    let localRetention = null;
    let workstationRetention = null;
    if (adhocRunId) {
      // KI-124: an ad-hoc proof run never participates in retention. Its snapshots carry the
      // 'adhoc' tag, which applyRetention() would ignore anyway; skipping the pass outright makes
      // "no snapshot was forgotten and no prune ran" a property of the control flow, not an
      // inference about tag matching.
      localRetention = { pruned_tiers: [], monthly_pruned: false, skipped: true, reason: 'adhoc_proof_run' };
      workstationRetention = config.workstation.enabled
        ? { pruned_tiers: [], monthly_pruned: false, skipped: true, reason: 'adhoc_proof_run' }
        : null;
      logEvent(config, 'adhoc_retention_skipped', { run_id: adhocRunId, tiers });
    } else {
    try {
      localRetention = applyRetention(
        config,
        config.paths.repository,
        config.paths.password_file,
        tiers,
      );
      if (config.workstation.enabled) {
        if (!workstationForRetention?.completed) {
          throw new Error('Workstation validation is required but no validated workstation repository is available for retention');
        }
        workstationRetention = applyRetention(
          config,
          workstationForRetention.repository,
          workstationForRetention.passwordFile,
          tiers,
        );
      }
    } catch (error) {
      for (const item of batchRecords) {
        item.record.status = 'retention_failed';
        item.record.retention_failed_at = nowIso();
        item.record.retention_error = asError(error);
        item.record.retention_authorized = true;
        atomicWriteJson(item.filePath, item.record);
      }
      logEvent(config, 'batch_retention_failed_after_validation', {
        run_ids: batchRecords.map((item) => item.record.run_id),
        error: asError(error),
      });
      throw error;
    }
    }

    for (const item of batchRecords) {
      item.record.retention = {
        completed_at: nowIso(),
        local: localRetention,
        workstation: workstationRetention,
        daily_keep: config.retention.daily,
        weekly_keep: config.retention.weekly,
        monthly_auto_delete: false,
        batch_run_ids: batchRecords.map((entry) => entry.record.run_id),
      };
      item.record.status = 'validated';
      item.record.validated_at = nowIso();
      item.record.retention_authorized = true;
      delete item.record.retention_error;
      atomicWriteJson(item.filePath, item.record);
      logEvent(config, 'run_validated_and_batch_retention_completed', {
        run_id: item.record.run_id,
        tiers,
        batch_size: batchRecords.length,
      });
    }

    for (const result of results) {
      if (batchRecords.some((item) => item.record.run_id === result.run_id)) {
        result.status = 'validated';
        result.retention_performed = true;
      }
    }

    if (config.notifications.successful_validation) {
      await bestEffortNtfy(
        config,
        'Second Brain backup verified',
        `Validated ${batchRecords.length} backup run(s), copied every pending run to the workstation, then applied daily/weekly retention once. Monthly snapshots were untouched.`,
        'low',
        'white_check_mark,floppy_disk',
      );
    }
    return {
      status: incompleteCreations.length > 0 ? 'warning' : 'completed',
      summary: `Validated ${batchRecords.length} backup run(s) and safely applied one batch retention pass`,
      results,
      incomplete_creations: incompleteCreations,
      retention_performed: true,
      monthly_auto_delete: false,
    };
  } finally {
    release();
  }
}

function evidenceIsCurrentMonth(config, filename) {
  const target = evidencePath(config, filename);
  if (!fs.existsSync(target)) return false;
  try {
    const evidence = readJson(target);
    const completed = new Date(evidence.completed_at);
    if (Number.isNaN(completed.getTime())) return false;
    return localDateParts(config.timezone, completed).yearMonth === localDateParts(config.timezone).yearMonth
      && evidence.status === 'verified';
  } catch {
    return false;
  }
}

async function monthlyReminder(config) {
  const localDate = localDateParts(config.timezone);
  if (evidenceIsCurrentMonth(config, 'monthly-external-drive.json')) {
    return {
      status: 'skipped',
      summary: 'This month\'s external-drive backup already has verified evidence',
      current_month: localDate.yearMonth,
    };
  }
  if (!config.calendar.monthly_reminder_days.includes(localDate.day)) {
    return {
      status: 'skipped',
      summary: 'No monthly off-site reminder is due today',
      current_month: localDate.yearMonth,
    };
  }
  const notification = await sendNtfy(
    config,
    'Monthly Second Brain off-site backup is due',
    `Connect the external backup drive to the server and confirm it is mounted at ${config.external.mount_path}. Then manually run workflow B3: Monthly Off-Site Copy. The workflow will copy the complete retained set, read-check it, perform a restore test, and leave every monthly snapshot for manual deletion.`,
    'default',
    'calendar,floppy_disk',
  );
  return {
    status: 'action_required',
    summary: 'Monthly off-site backup reminder sent',
    current_month: localDate.yearMonth,
    notification,
    monthly_auto_delete: false,
  };
}

function verifyExternalTarget(config) {
  const mountPath = assertAbsoluteSafePath(config.external.mount_path, 'external.mount_path');
  if (!fs.existsSync(mountPath) || !fs.statSync(mountPath).isDirectory()) {
    throw new Error(`External drive mount is not available: ${mountPath}`);
  }
  const markerPath = path.join(mountPath, config.external.marker_filename);
  if (!fs.existsSync(markerPath)) {
    throw new Error(`External target marker is missing; refusing to write to an unverified mount: ${markerPath}`);
  }
  const markerValue = fs.readFileSync(markerPath, 'utf8').trim();
  if (markerValue !== config.external.marker_value) {
    throw new Error('External target marker does not match the configured drive marker');
  }
  const repository = path.join(mountPath, config.external.repository_relative_path);
  assertPathInside(repository, mountPath, 'external repository');
  return repository;
}

async function offsiteCopy(config) {
  const release = acquireLock(config);
  try {
    await preflight(config);
    const externalRepository = verifyExternalTarget(config);
    const externalDestination = {
      repository: externalRepository,
      password_file: config.external.password_file || config.paths.password_file,
    };
    const destination = ensureDestinationRepository(config, externalDestination);
    runRestic(config, destination.repository, destination.passwordFile, [
      'copy',
      '--from-repo', config.paths.repository,
      '--from-password-file', config.paths.password_file,
    ]);
    const repositoryCheck = checkRepository(config, destination.repository, destination.passwordFile, true);

    const snapshots = snapshotList(config, destination.repository, destination.passwordFile)
      .filter((snapshot) => Array.isArray(snapshot.tags) && snapshot.tags.includes('second-brain'))
      .sort((a, b) => String(b.time).localeCompare(String(a.time)));
    if (snapshots.length === 0) {
      throw new Error('External repository contains no Second Brain snapshots after copy');
    }
    const monthly = snapshots.find((snapshot) => snapshot.tags.includes('monthly'));
    const selected = monthly || snapshots[0];
    const runTag = selected.tags.find((tag) => tag.startsWith('run-'));
    if (!runTag) throw new Error('Selected external snapshot is missing its run tag');
    const runId = runTag.slice(4);
    const runRecordPath = runFile(config, runId);
    if (!fs.existsSync(runRecordPath)) throw new Error(`Run record is missing for external snapshot: ${runId}`);
    const runRecord = readJson(runRecordPath);
    verifySentinelRestore(
      config,
      destination.repository,
      destination.passwordFile,
      selected.id,
      runId,
      runRecord.sentinel.sha256,
      'external',
      selected.tags.includes('monthly') ? 'monthly' : 'latest',
    );

    let fullRestore = null;
    if (config.validation.full_restore_on_external_copy) {
      fullRestore = fullRestoreTest(
        config,
        destination.repository,
        destination.passwordFile,
        selected.id,
        runId,
        'external',
      );
      writeEvidence(config, 'restore-test.json', {
        evidence_type: 'restore-test',
        status: 'verified',
        completed_at: nowIso(),
        run_id: runId,
        ...fullRestore,
        secrets_included_in_evidence: false,
      });
    }

    let externalRetention;
    try {
      externalRetention = applyRetention(
        config,
        destination.repository,
        destination.passwordFile,
        ['daily', 'weekly'],
      );
    } catch (error) {
      error.retentionAuthorized = true;
      throw error;
    }
    const evidence = writeEvidence(config, 'monthly-external-drive.json', {
      evidence_type: 'monthly-external-drive',
      status: 'verified',
      completed_at: nowIso(),
      run_id: runId,
      repository_check: repositoryCheck.full_read ? 'full-data-read-passed' : 'passed',
      sentinel_restore: 'passed',
      full_restore_test: fullRestore ? 'passed' : 'not_configured',
      retained_snapshot_count_before_tier_prune: snapshots.length,
      daily_keep: config.retention.daily,
      weekly_keep: config.retention.weekly,
      monthly_auto_delete: false,
      secrets_included_in_evidence: false,
    });
    logEvent(config, 'external_copy_validated', { run_id: runId, evidence });
    const notification = await bestEffortNtfy(
      config,
      'Monthly Second Brain off-site backup verified',
      'The external drive received the complete encrypted retained set, passed a full repository read, and passed its restore test. Daily/weekly retention was applied; no monthly snapshot was deleted. The drive is safe to disconnect.',
      'default',
      'white_check_mark,floppy_disk',
    );
    return {
      status: 'completed',
      summary: 'External retained set copied, fully checked, restored, and safely pruned by daily/weekly tier',
      run_id: runId,
      evidence_path: evidence,
      repository_check: repositoryCheck,
      full_restore_test: fullRestore,
      retention: externalRetention,
      notification,
      monthly_auto_delete: false,
    };
  } finally {
    release();
  }
}

async function runAction() {
  const config = loadConfig();
  ensureDirectory(config.paths.log_root);
  const adhocRunId = parseAdhocRunId(RUN_ID_ARG);
  if (adhocRunId && !['create', 'validate'].includes(ACTION)) {
    throw new Error(`An explicit run id is only supported for create and validate, not ${ACTION}`);
  }
  logEvent(config, 'action_started', adhocRunId ? { adhoc_run_id: adhocRunId } : {});
  let result;
  switch (ACTION) {
    case 'preflight':
      result = await preflight(config);
      break;
    case 'create':
      result = await createBackups(config, adhocRunId);
      break;
    case 'validate':
      result = await validateBackups(config, adhocRunId);
      break;
    case 'monthly-reminder':
      result = await monthlyReminder(config);
      break;
    case 'offsite-copy':
      result = await offsiteCopy(config);
      break;
    case 'help':
      result = {
        status: 'completed',
        summary: 'Usage: second-brain-backup.mjs preflight|create|validate|monthly-reminder|offsite-copy [adhoc-<id> for create|validate]',
      };
      break;
    default:
      throw new Error(`Unknown action: ${ACTION}`);
  }
  logEvent(config, 'action_completed', { status: result.status });
  emit(result);
}

runAction().catch(async (error) => {
  const config = loadedConfig;
  let notification = { sent: false, reason: 'config_not_loaded' };
  if (config) {
    try {
      logEvent(config, 'action_failed', { error: asError(error), retention_authorized: false });
    } catch {
      // Preserve the original failure even if logging also fails.
    }
    notification = await notifyFailure(config, ACTION, error);
  }
  const retentionAuthorized = error?.retentionAuthorized === true;
  emit({
    status: 'failed',
    summary: retentionAuthorized
      ? `${ACTION} failed after restore validation authorized retention; inspect the run record before retrying`
      : `${ACTION} failed; no retention deletion was authorized`,
    error: asError(error),
    retention_authorized: retentionAuthorized,
    monthly_auto_delete: false,
    notification,
  });
});
