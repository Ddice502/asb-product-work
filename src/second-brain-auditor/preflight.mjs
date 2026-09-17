#!/usr/bin/env node

import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const COLLECTOR_VERSION = '0.1.0';
const startedAt = new Date();
const checks = [];

function addCheck(id, status, message, evidence = undefined) {
  const item = { id, status, message };
  if (evidence !== undefined) item.evidence = evidence;
  checks.push(item);
  return item;
}

function parseArguments(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--config-base64') {
      parsed.configBase64 = argv[index + 1];
      index += 1;
    } else if (argument === '--config-file') {
      parsed.configFile = argv[index + 1];
      index += 1;
    } else if (argument === '--help') {
      parsed.help = true;
    } else {
      throw new Error(`Unknown argument: ${argument}`);
    }
  }
  return parsed;
}

function loadConfig(argumentsObject) {
  if (argumentsObject.help) {
    process.stdout.write(
      'Usage: preflight.mjs --config-base64 BASE64\n' +
      '   or: preflight.mjs --config-file /absolute/path/config.json\n',
    );
    process.exit(0);
  }

  if (Boolean(argumentsObject.configBase64) === Boolean(argumentsObject.configFile)) {
    throw new Error('Provide exactly one of --config-base64 or --config-file.');
  }

  let raw;
  if (argumentsObject.configBase64) {
    if (!/^[A-Za-z0-9+/=]+$/.test(argumentsObject.configBase64)) {
      throw new Error('The base64 configuration contains invalid characters.');
    }
    raw = Buffer.from(argumentsObject.configBase64, 'base64').toString('utf8');
  } else {
    const configPath = path.resolve(argumentsObject.configFile);
    raw = fs.readFileSync(configPath, 'utf8');
  }

  const parsed = JSON.parse(raw);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('The configuration must be a JSON object.');
  }
  return parsed;
}

function validateUrl(value, fieldName) {
  if (typeof value !== 'string' || value.trim() === '') {
    return { valid: false, reason: `${fieldName} is empty.` };
  }
  if (/REPLACE|CHANGEME|WORKSTATION_IP/i.test(value)) {
    return { valid: false, reason: `${fieldName} still contains a placeholder.` };
  }
  try {
    const parsed = new URL(value);
    if (!['http:', 'https:'].includes(parsed.protocol)) {
      return { valid: false, reason: `${fieldName} must use HTTP or HTTPS.` };
    }
    if (parsed.username || parsed.password) {
      return { valid: false, reason: `${fieldName} must not contain embedded credentials.` };
    }
    return { valid: true, url: parsed };
  } catch {
    return { valid: false, reason: `${fieldName} is not a valid URL.` };
  }
}

function validateConfig(config) {
  const errors = [];
  if (config.phase !== 0) errors.push('phase must be 0');
  if (config.mode !== 'read_only') errors.push('mode must be read_only');
  if (config.timezone !== 'America/Kentucky/Louisville') {
    errors.push('timezone must be America/Kentucky/Louisville');
  }
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: config.timezone }).format();
  } catch {
    errors.push('timezone is not recognized by this runtime');
  }
  if (!Array.isArray(config.path_checks) || config.path_checks.length === 0) {
    errors.push('path_checks must be a non-empty array');
  }
  const pathIds = new Set();
  for (const entry of config.path_checks || []) {
    if (!entry || typeof entry !== 'object') {
      errors.push('every path_checks entry must be an object');
      continue;
    }
    if (!entry.id || pathIds.has(entry.id)) errors.push(`invalid or duplicate path id: ${entry.id}`);
    pathIds.add(entry.id);
    if (typeof entry.path !== 'string' || !path.isAbsolute(entry.path)) {
      errors.push(`path must be absolute for ${entry.id || 'unknown entry'}`);
    }
    if (!['read', 'read_write'].includes(entry.access)) {
      errors.push(`access must be read or read_write for ${entry.id || 'unknown entry'}`);
    }
    if (!['file', 'directory', 'either'].includes(entry.kind)) {
      errors.push(`kind must be file, directory, or either for ${entry.id || 'unknown entry'}`);
    }
  }
  const warning = Number(config.thresholds?.capacity_warning_percent_remaining);
  const critical = Number(config.thresholds?.capacity_critical_percent_remaining);
  if (!(warning === 25 && critical === 15)) {
    errors.push('capacity thresholds must be Warning 25% and Critical 15% remaining');
  }
  if (Number(config.execution_window?.maximum_lookback_days) !== 10) {
    errors.push('maximum execution lookback must be 10 days');
  }
  if (Number(config.execution_window?.phase0_sample_limit) < 1 ||
      Number(config.execution_window?.phase0_sample_limit) > 250) {
    errors.push('phase0 execution sample limit must be between 1 and 250');
  }

  if (errors.length > 0) {
    addCheck('CONFIG_SCHEMA', 'blocked', 'The owner-editable configuration is invalid.', errors);
  } else {
    addCheck('CONFIG_SCHEMA', 'pass', 'The owner-editable configuration passed Phase 0 validation.');
  }
  return errors.length === 0;
}

function decodeMountPath(value) {
  return value
    .replaceAll('\\040', ' ')
    .replaceAll('\\011', '\t')
    .replaceAll('\\012', '\n')
    .replaceAll('\\134', '\\');
}

function readMountInfo() {
  const mounts = [];
  const raw = fs.readFileSync('/proc/self/mountinfo', 'utf8');
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue;
    const fields = line.split(' ');
    const separator = fields.indexOf('-');
    if (separator < 0 || fields.length < separator + 3) continue;
    mounts.push({
      mount_id: fields[0],
      parent_id: fields[1],
      major_minor: fields[2],
      root: decodeMountPath(fields[3]),
      mount_point: decodeMountPath(fields[4]),
      mount_options: fields[5].split(','),
      filesystem_type: fields[separator + 1],
      source: decodeMountPath(fields[separator + 2]),
      super_options: (fields[separator + 3] || '').split(',').filter(Boolean),
    });
  }
  return mounts.sort((a, b) => b.mount_point.length - a.mount_point.length);
}

function mountForPath(targetPath, mounts) {
  const normalized = path.resolve(targetPath);
  return mounts.find((mount) =>
    normalized === mount.mount_point || normalized.startsWith(`${mount.mount_point.replace(/\/$/, '')}/`),
  ) || null;
}

function kindOf(stat) {
  if (stat.isDirectory()) return 'directory';
  if (stat.isFile()) return 'file';
  if (stat.isSymbolicLink()) return 'symlink';
  if (stat.isSocket()) return 'socket';
  if (stat.isFIFO()) return 'fifo';
  if (stat.isBlockDevice()) return 'block_device';
  if (stat.isCharacterDevice()) return 'character_device';
  return 'other';
}

function checkPath(entry, mounts) {
  const result = {
    id: entry.id,
    path: entry.path,
    required: Boolean(entry.required),
    expected_kind: entry.kind,
    requested_access: entry.access,
    exists: false,
    readable: false,
    writable_by_runtime: false,
  };

  try {
    const linkStat = fs.lstatSync(entry.path);
    result.exists = true;
    result.link_kind = kindOf(linkStat);
    result.mode = `0${(linkStat.mode & 0o777).toString(8)}`;
    result.uid = linkStat.uid;
    result.gid = linkStat.gid;
    if (linkStat.isSymbolicLink()) result.symlink_target = fs.readlinkSync(entry.path);
    try {
      result.real_path = fs.realpathSync(entry.path);
      result.resolved_kind = kindOf(fs.statSync(entry.path));
    } catch (error) {
      result.resolution_error = error.code || error.message;
    }
    try {
      fs.accessSync(entry.path, fs.constants.R_OK);
      result.readable = true;
    } catch {}
    try {
      fs.accessSync(entry.path, fs.constants.W_OK);
      result.writable_by_runtime = true;
    } catch {}
  } catch (error) {
    result.error = error.code || error.message;
  }

  const mount = mountForPath(entry.path, mounts);
  if (mount) {
    result.mount = {
      mount_point: mount.mount_point,
      root: mount.root,
      filesystem_type: mount.filesystem_type,
      source: mount.source,
      read_only: mount.mount_options.includes('ro'),
    };
  }

  let status = 'pass';
  const problems = [];
  if (!result.exists) problems.push('missing');
  if (result.exists && !result.readable) problems.push('not readable by the n8n runtime user');
  if (result.exists && entry.access === 'read_write' && !result.writable_by_runtime) {
    problems.push('not writable by the n8n runtime user');
  }
  const actualKind = result.resolved_kind || result.link_kind;
  if (result.exists && entry.kind !== 'either' && actualKind !== entry.kind) {
    problems.push(`expected ${entry.kind}, found ${actualKind}`);
  }

  if (problems.length > 0) status = entry.required ? 'blocked' : 'warning';
  addCheck(`PATH_${entry.id.toUpperCase()}`, status,
    problems.length === 0 ? `${entry.path} passed the access check.` : `${entry.path}: ${problems.join('; ')}.`,
  );
  return result;
}

function percentage(numerator, denominator) {
  if (denominator === 0n) return null;
  return Number((numerator * 10000n) / denominator) / 100;
}

function filesystemForPath(pathResult, config) {
  if (!pathResult.exists) return null;
  try {
    const stat = fs.statfsSync(pathResult.path, { bigint: true });
    const availableBytes = stat.bavail * stat.bsize;
    const totalBytes = stat.blocks * stat.bsize;
    const freeInodes = stat.ffree;
    const totalInodes = stat.files;
    const capacityRemaining = percentage(availableBytes, totalBytes);
    const inodeRemaining = percentage(freeInodes, totalInodes);
    const warning = Number(config.thresholds.capacity_warning_percent_remaining);
    const critical = Number(config.thresholds.capacity_critical_percent_remaining);

    let capacityStatus = 'pass';
    if (capacityRemaining !== null && capacityRemaining <= critical) capacityStatus = 'blocked';
    else if (capacityRemaining !== null && capacityRemaining <= warning) capacityStatus = 'warning';

    let inodeStatus = 'pass';
    if (inodeRemaining !== null && inodeRemaining <= critical) inodeStatus = 'blocked';
    else if (inodeRemaining !== null && inodeRemaining <= warning) inodeStatus = 'warning';

    return {
      mount_point: pathResult.mount?.mount_point || null,
      source: pathResult.mount?.source || null,
      filesystem_type: pathResult.mount?.filesystem_type || null,
      sampled_path: pathResult.path,
      available_bytes: availableBytes.toString(),
      total_bytes: totalBytes.toString(),
      capacity_percent_remaining: capacityRemaining,
      capacity_status: capacityStatus,
      free_inodes: freeInodes.toString(),
      total_inodes: totalInodes.toString(),
      inode_percent_remaining: inodeRemaining,
      inode_status: inodeStatus,
    };
  } catch (error) {
    addCheck(`FILESYSTEM_${pathResult.id.toUpperCase()}`, 'warning',
      `Could not read filesystem statistics for ${pathResult.path}.`, error.code || error.message);
    return null;
  }
}

function checkFilesystems(pathResults, config) {
  const unique = new Map();
  for (const pathResult of pathResults) {
    const filesystem = filesystemForPath(pathResult, config);
    if (!filesystem) continue;
    const key = `${filesystem.mount_point || ''}|${filesystem.source || ''}`;
    if (!unique.has(key)) unique.set(key, filesystem);
  }
  for (const filesystem of unique.values()) {
    const label = filesystem.mount_point || filesystem.sampled_path;
    addCheck(`CAPACITY_${crypto.createHash('sha1').update(label).digest('hex').slice(0, 8)}`,
      filesystem.capacity_status,
      `${label} has ${filesystem.capacity_percent_remaining}% capacity remaining.`);
    addCheck(`INODES_${crypto.createHash('sha1').update(label).digest('hex').slice(0, 8)}`,
      filesystem.inode_status,
      `${label} has ${filesystem.inode_percent_remaining}% inodes remaining.`);
  }
  return [...unique.values()];
}

function checkPathGroups(config) {
  const results = [];
  for (const group of config.exclusive_path_groups || []) {
    const matches = [];
    for (const candidate of group.candidates || []) {
      try {
        const stat = fs.statSync(candidate);
        matches.push({ path: candidate, kind: kindOf(stat) });
      } catch {}
    }
    let status = 'pass';
    let message;
    if (matches.length === 1) {
      message = `${group.id} resolved to ${matches[0].path}.`;
    } else if (matches.length === 0) {
      status = group.required ? 'blocked' : 'warning';
      message = `${group.id} did not match any approved candidate path.`;
    } else {
      status = 'blocked';
      message = `${group.id} matched more than one candidate and is ambiguous.`;
    }
    addCheck(`PATH_GROUP_${group.id.toUpperCase()}`, status, message, matches);
    results.push({ id: group.id, status, matches });
  }
  return results;
}

function inspectArchiveCandidates(config) {
  const results = [];
  for (const candidate of config.archive_candidates || []) {
    const result = { path: candidate, exists: false };
    try {
      const stat = fs.statSync(candidate);
      result.exists = true;
      result.kind = kindOf(stat);
      if (stat.isDirectory()) result.entry_count = fs.readdirSync(candidate).length;
    } catch (error) {
      result.error = error.code || error.message;
    }
    results.push(result);
  }

  const existing = results.filter((item) => item.exists);
  const canonical = config.canonical_archive_root;
  if (existing.length === 1 && existing[0].path === canonical) {
    addCheck('ARCHIVE_CANONICAL_PATH', 'pass', `The approved archive root exists at ${canonical}.`);
  } else if (existing.length === 0) {
    addCheck('ARCHIVE_CANONICAL_PATH', 'warning',
      `Neither archive candidate exists inside n8n. Phase 1 may create ${canonical} only after its mount is approved.`);
  } else {
    addCheck('ARCHIVE_CANONICAL_PATH', 'blocked',
      'The live archive layout conflicts with the approved singular canonical path. Do not migrate or alias it automatically.',
      existing);
  }
  return results;
}

function inspectComposeCandidates(config) {
  for (const candidate of config.compose_candidate_paths || []) {
    try {
      const stat = fs.statSync(candidate);
      if (!stat.isFile()) continue;
      if (stat.size > 1024 * 1024) {
        addCheck('COMPOSE_METADATA', 'warning', `${candidate} is larger than the one-megabyte inspection limit.`);
        return { path: candidate, size_bytes: stat.size, inspected: false };
      }
      const raw = fs.readFileSync(candidate, 'utf8');
      const safeMarkers = ['/AI/', ':/Obsidian', ':/Capture', ':/Scripts', ':/State',
        ':/Archive', ':/Archives', ':/Exports', ':/Logs', ':/Config'];
      const relevantMountLines = raw.split('\n')
        .map((line) => line.trim())
        .filter((line) => safeMarkers.some((marker) => line.includes(marker)))
        .filter((line) => /^-\s*["']?\//.test(line))
        .slice(0, 100);
      addCheck('COMPOSE_METADATA', 'pass', `Read safe mount metadata from ${candidate}.`);
      return {
        path: candidate,
        size_bytes: stat.size,
        sha256: crypto.createHash('sha256').update(raw).digest('hex'),
        relevant_mount_lines: relevantMountLines,
        inspected: true,
      };
    } catch {}
  }
  addCheck('COMPOSE_METADATA', 'warning',
    'No approved compose file was visible inside the container. Mount inspection will rely on /proc/self/mountinfo.');
  return { inspected: false, path: null, relevant_mount_lines: [] };
}

function inspectN8nVersion(config) {
  for (const candidate of config.n8n_package_candidates || []) {
    try {
      const parsed = JSON.parse(fs.readFileSync(candidate, 'utf8'));
      if (parsed.name === 'n8n' && typeof parsed.version === 'string') {
        addCheck('N8N_VERSION', 'pass', `Detected n8n ${parsed.version}.`);
        return { version: parsed.version, source: candidate };
      }
    } catch {}
  }
  addCheck('N8N_VERSION', 'warning', 'Could not identify the n8n package version from approved paths.');
  return { version: null, source: null };
}

async function inspectOllama(config) {
  const base = config.services?.ollama_base_url;
  const expectedModel = String(config.services?.ollama_expected_model || '').trim();
  const validation = validateUrl(base, 'ollama_base_url');
  if (!validation.valid) {
    addCheck('OLLAMA_ENDPOINT', 'blocked', validation.reason);
    return { configured: false, reachable: false, expected_model: expectedModel, models: [] };
  }
  if (!expectedModel || /REPLACE|CHANGEME/i.test(expectedModel)) {
    addCheck('OLLAMA_MODEL', 'blocked', 'ollama_expected_model is missing or still contains a placeholder.');
  }
  try {
    const endpoint = new URL('/api/tags', validation.url);
    const response = await fetch(endpoint, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(Number(config.services?.probe_timeout_ms || 5000)),
    });
    if (!response.ok) {
      addCheck('OLLAMA_ENDPOINT', 'blocked', `Ollama returned HTTP ${response.status}.`);
      return { configured: true, reachable: false, expected_model: expectedModel, models: [] };
    }
    const body = await response.json();
    const models = Array.isArray(body.models)
      ? body.models.map((item) => String(item.name || item.model || '')).filter(Boolean).slice(0, 50)
      : [];
    addCheck('OLLAMA_ENDPOINT', 'pass', 'The configured Ollama endpoint is reachable from n8n.');
    const exact = models.includes(expectedModel);
    addCheck('OLLAMA_MODEL', exact ? 'pass' : 'blocked',
      exact ? `Found the approved model ${expectedModel}.` : `The approved model ${expectedModel} was not returned by Ollama.`,
      models);
    return { configured: true, reachable: true, expected_model: expectedModel, models };
  } catch (error) {
    addCheck('OLLAMA_ENDPOINT', 'blocked', 'The configured Ollama endpoint is not reachable from n8n.',
      error.name || error.message);
    return { configured: true, reachable: false, expected_model: expectedModel, models: [] };
  }
}

function inspectNtfy(config) {
  const base = config.services?.ntfy_base_url;
  const topic = String(config.services?.ntfy_topic || '').trim();
  const validation = validateUrl(base, 'ntfy_base_url');
  const topicValid = Boolean(topic) && !/REPLACE|CHANGEME/i.test(topic) && /^[A-Za-z0-9_-]+$/.test(topic);
  if (!validation.valid || !topicValid) {
    addCheck('NTFY_CONFIGURATION', 'blocked',
      !validation.valid ? validation.reason : 'ntfy_topic is missing, unsafe, or still contains a placeholder.');
    return { configured: false, network_test_performed: false };
  }
  addCheck('NTFY_CONFIGURATION', 'pass',
    'The ntfy URL and topic format are configured. No notification was sent during this read-only preflight.');
  return {
    configured: true,
    base_origin: validation.url.origin,
    topic_configured: true,
    network_test_performed: false,
  };
}

function inspectTimezone(config) {
  const configured = config.timezone;
  const runtimeTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  const envTimezone = process.env.GENERIC_TIMEZONE || process.env.TZ || null;
  const matches = runtimeTimezone === configured || envTimezone === configured;
  addCheck('TIMEZONE', matches ? 'pass' : 'warning',
    matches
      ? `The runtime exposes the approved timezone ${configured}.`
      : `The workflow requests ${configured}, but the runtime reports ${runtimeTimezone || 'unknown'}. Verify the n8n workflow and instance timezone before activation.`);
  return { configured, runtime: runtimeTimezone, environment: envTimezone };
}

function summarize() {
  const summary = { pass: 0, warning: 0, blocked: 0 };
  for (const check of checks) summary[check.status] += 1;
  const overallStatus = summary.blocked > 0 ? 'blocked' : summary.warning > 0 ? 'warning' : 'verified';
  return { summary, overallStatus };
}

async function main() {
  const argumentsObject = parseArguments(process.argv.slice(2));
  const config = loadConfig(argumentsObject);
  const configHash = crypto.createHash('sha256').update(JSON.stringify(config)).digest('hex');
  validateConfig(config);

  const mounts = readMountInfo();
  const pathResults = (config.path_checks || []).map((entry) => checkPath(entry, mounts));
  const filesystems = checkFilesystems(pathResults, config);
  const pathGroups = checkPathGroups(config);
  const archiveCandidates = inspectArchiveCandidates(config);
  const compose = inspectComposeCandidates(config);
  const n8n = inspectN8nVersion(config);
  const timezone = inspectTimezone(config);
  const ntfy = inspectNtfy(config);
  const ollama = await inspectOllama(config);
  const { summary, overallStatus } = summarize();

  const output = {
    schema_version: '1.0.0',
    collector_version: COLLECTOR_VERSION,
    read_only: true,
    generated_at: new Date().toISOString(),
    duration_ms: Date.now() - startedAt.getTime(),
    overall_status: overallStatus,
    summary,
    config_hash: `sha256:${configHash}`,
    runtime: {
      hostname: os.hostname(),
      platform: process.platform,
      architecture: process.arch,
      node_version: process.version,
      uid: typeof process.getuid === 'function' ? process.getuid() : null,
      gid: typeof process.getgid === 'function' ? process.getgid() : null,
      timezone,
      n8n,
    },
    checks,
    paths: pathResults,
    path_groups: pathGroups,
    archive_candidates: archiveCandidates,
    filesystems,
    compose,
    services: { ollama, ntfy },
    manual_actions: checks
      .filter((check) => check.status !== 'pass')
      .map((check) => ({ check_id: check.id, status: check.status, instruction: check.message })),
  };

  process.stdout.write(`${JSON.stringify(output)}\n`);
}

main().catch((error) => {
  const output = {
    schema_version: '1.0.0',
    collector_version: COLLECTOR_VERSION,
    read_only: true,
    generated_at: new Date().toISOString(),
    overall_status: 'error',
    fatal_error: {
      name: error.name || 'Error',
      message: error.message || String(error),
    },
  };
  process.stdout.write(`${JSON.stringify(output)}\n`);
  process.exitCode = 1;
});

