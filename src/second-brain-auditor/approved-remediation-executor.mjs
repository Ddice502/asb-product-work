#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const VERSION = '1.0.0';

function fail(message) { throw new Error(message); }
function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]));
  return value;
}
function sha256(value) { return `sha256:${crypto.createHash('sha256').update(value).digest('hex')}`; }
function expectedActionHash(action) {
  const copy = { ...action };
  delete copy.action_hash;
  return sha256(JSON.stringify(canonicalize(copy)));
}
function parseArguments(argv) {
  const command = argv[0];
  if (!['plan', 'apply', 'mark-n8n-applied'].includes(command)) fail('Use plan, apply, or mark-n8n-applied.');
  const values = { command, reportFile: '/Obsidian/90 System/Second-Brain Audit Review.md', manifestFile: null, vaultRoot: '/Obsidian', backupRoot: '/Archive/Second Brain Auditor/Remediation Backups' };
  for (let index = 1; index < argv.length; index += 2) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (!value) fail(`Missing value for ${flag}.`);
    const key = { '--report-file': 'reportFile', '--manifest-file': 'manifestFile', '--vault-root': 'vaultRoot', '--backup-root': 'backupRoot' }[flag];
    if (!key) fail(`Unknown option ${flag}.`);
    values[key] = value;
  }
  if (!values.manifestFile) {
    fail('Explicit --manifest-file is required; no historical remediation manifest is selected automatically.');
  }
  return values;
}
function isInside(candidate, root) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}
function ensureInside(candidate, root, label) {
  if (!isInside(candidate, root)) fail(`${label} escaped its approved root.`);
}
function mapVaultPath(manifestPath, vaultRoot) {
  if (manifestPath === '/Obsidian') return path.resolve(vaultRoot);
  if (!manifestPath.startsWith('/Obsidian/')) fail(`Manifest path is outside /Obsidian: ${manifestPath}`);
  const resolved = path.resolve(vaultRoot, manifestPath.slice('/Obsidian/'.length));
  ensureInside(resolved, vaultRoot, 'Vault path');
  return resolved;
}
function readJson(file, label) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch (error) { fail(`${label} is not valid readable JSON: ${error.message}`); }
}
function parseDecisions(markdown) {
  const decisions = new Map();
  for (const match of markdown.matchAll(/<!-- auditor-finding:([^>]+) -->\s*```yaml\s*([\s\S]*?)```/g)) {
    const values = {};
    for (const line of match[2].split(/\r?\n/)) {
      const field = line.match(/^(status|decision_reason|decision_date|review_after):\s*(.*)$/);
      if (!field) continue;
      try { values[field[1]] = JSON.parse(field[2]); } catch { values[field[1]] = field[2].trim(); }
    }
    decisions.set(match[1].trim(), values);
  }
  return decisions;
}
function validateManifest(manifest, decisions, command) {
  if (manifest.schema_version !== '1.0.0' || manifest.mode !== 'owner_approved_remediation') fail('Manifest schema or mode is not approved.');
  const safety = manifest.safety || {};
  if (safety.automatic_deletion !== false || safety.entity_creation !== false || safety.entity_merge !== false || safety.credential_changes !== false || safety.arbitrary_shell_from_ai !== false || safety.backup_before_write !== true || safety.rollback_on_validation_failure !== true) fail('Manifest safety assertions failed.');
  if (!Array.isArray(manifest.automatic_source_fixes) || !Array.isArray(manifest.n8n_workflow_updates)) fail('Manifest operation arrays are missing.');
  const permitted = new Set(['unlink_wikilink', 'repair_wikilink_target', 'create_file_if_missing']);
  for (const action of manifest.automatic_source_fixes) {
    if (!permitted.has(action.operation)) fail(`Operation ${action.operation} is not allowlisted.`);
    if (action.action_hash !== expectedActionHash(action)) fail(`Action hash mismatch for ${action.finding_id}.`);
    const status = decisions.get(action.finding_id)?.status;
    const permittedStatuses = new Set(['approved_for_execution', 'applied', 'manual_follow_up']);
    if (!permittedStatuses.has(status)) fail(`Finding ${action.finding_id} has an invalid report status for ${command}: ${status || 'missing'}.`);
  }
  for (const action of manifest.n8n_workflow_updates) {
    if (action.operation !== 'assign_n8n_error_workflow') fail(`n8n operation ${action.operation} is not allowlisted.`);
    if (action.action_hash !== expectedActionHash(action)) fail(`Action hash mismatch for ${action.finding_id}.`);
    const status = decisions.get(action.finding_id)?.status;
    if (!new Set(['approved_for_execution', 'applied']).has(status)) fail(`n8n finding ${action.finding_id} is not approved or applied in the report.`);
  }
}
function walkMarkdown(root) {
  const files = [];
  const stack = [root];
  while (stack.length) {
    const directory = stack.pop();
    let entries;
    try { entries = fs.readdirSync(directory, { withFileTypes: true }); } catch { continue; }
    for (const entry of entries) {
      if (entry.name === '.trash' || entry.name === '.git') continue;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory() && !entry.isSymbolicLink()) stack.push(absolute);
      else if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) files.push(absolute);
    }
  }
  return files;
}
function buildTargetIndex(vaultRoot) {
  const index = new Map();
  for (const file of walkMarkdown(vaultRoot)) {
    const relative = path.relative(vaultRoot, file).replaceAll(path.sep, '/');
    const keys = [relative, relative.replace(/\.md$/i, ''), path.posix.basename(relative), path.posix.basename(relative, '.md')];
    for (const key of keys) {
      if (!index.has(key)) index.set(key, []);
      index.get(key).push(file);
    }
  }
  return index;
}
function targetExists(index, target) {
  const normalized = String(target).replace(/^\//, '');
  return (index.get(normalized) || []).length > 0 || (index.get(normalized.replace(/\.md$/i, '')) || []).length > 0;
}
function transformWikiLinks(content, action) {
  let changed = 0;
  const output = content.replace(/(!?)\[\[([^\]]+)\]\]/g, (whole, embed, inner) => {
    const divider = inner.indexOf('|');
    const target = divider >= 0 ? inner.slice(0, divider) : inner;
    const alias = divider >= 0 ? inner.slice(divider + 1) : '';
    if (action.operation === 'unlink_wikilink' && !embed && target === action.target) {
      changed += 1;
      return alias || target;
    }
    if (action.operation === 'repair_wikilink_target' && target === action.original_target) {
      changed += 1;
      return `${embed}[[${action.replacement_target}${alias ? `|${alias}` : ''}]]`;
    }
    return whole;
  });
  return { output, changed };
}
function atomicWrite(file, content) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o750 });
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`);
  const handle = fs.openSync(temporary, 'wx', 0o640);
  try {
    fs.writeFileSync(handle, content, 'utf8');
    fs.fsyncSync(handle);
  } finally { fs.closeSync(handle); }
  fs.renameSync(temporary, file);
}
function backupFile(file, vaultRoot, backupRoot) {
  const relative = path.relative(vaultRoot, file);
  ensureInside(file, vaultRoot, 'Backup source');
  const destination = path.join(backupRoot, 'vault', relative);
  ensureInside(destination, backupRoot, 'Backup destination');
  fs.mkdirSync(path.dirname(destination), { recursive: true, mode: 0o750 });
  fs.copyFileSync(file, destination, fs.constants.COPYFILE_EXCL);
  return destination;
}
function updateReportStatuses(markdown, results) {
  let output = markdown;
  for (const result of results) {
    const status = result.status === 'applied' || result.status === 'already_applied' ? 'applied' : 'manual_follow_up';
    const reason = status === 'applied'
      ? `Approved remediation completed: ${result.message}`
      : `Approved remediation could not be applied safely: ${result.message}`;
    const escapedId = result.finding_id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const expression = new RegExp(`(<!-- auditor-finding:${escapedId} -->\\s*\\x60{3}yaml\\s*[\\s\\S]*?status:)\\s*[^\\n]+([\\s\\S]*?decision_reason:)\\s*[^\\n]+`);
    output = output.replace(expression, `$1 ${status}$2 ${JSON.stringify(reason)}`);
  }
  return output;
}
function markN8nApplied(args, reportMarkdown, manifest) {
  const timestamp = new Date().toISOString().replace(/[-:.]/g, '').replace('Z', 'Z');
  const runBackupRoot = path.resolve(args.backupRoot, `${manifest.manifest_id}-${timestamp}`);
  ensureInside(runBackupRoot, args.backupRoot, 'Run backup root');
  fs.mkdirSync(runBackupRoot, { recursive: true, mode: 0o750 });
  const reportBackup = path.join(runBackupRoot, 'Second-Brain Audit Review.before-n8n-status-update.md');
  fs.copyFileSync(args.reportFile, reportBackup, fs.constants.COPYFILE_EXCL);
  const results = manifest.n8n_workflow_updates.map((action) => ({
    finding_id: action.finding_id,
    status: 'applied',
    message: `assigned ${action.error_workflow_name} (${action.error_workflow_id}) to ${action.workflow_name} (${action.workflow_id}) and validated the saved workflow`,
  }));
  atomicWrite(args.reportFile, updateReportStatuses(reportMarkdown, results));
  return {
    schema_version: '1.0.0',
    executor_version: VERSION,
    mode: args.command,
    status: 'completed',
    manifest_id: manifest.manifest_id,
    n8n_workflow_updates_applied: results.length,
    automatic_deletions_performed: false,
    entity_changes_performed: false,
    credential_changes_performed: false,
    backup_root: runBackupRoot,
    results,
  };
}
function execute(args) {
  const reportMarkdown = fs.readFileSync(args.reportFile, 'utf8');
  const decisions = parseDecisions(reportMarkdown);
  const manifest = readJson(args.manifestFile, 'Remediation manifest');
  validateManifest(manifest, decisions, args.command);
  if (args.command === 'mark-n8n-applied') return markN8nApplied(args, reportMarkdown, manifest);
  const timestamp = new Date().toISOString().replace(/[-:.]/g, '').replace('Z', 'Z');
  const runBackupRoot = path.resolve(args.backupRoot, `${manifest.manifest_id}-${timestamp}`);
  ensureInside(runBackupRoot, args.backupRoot, 'Run backup root');
  const index = buildTargetIndex(args.vaultRoot);
  const byFile = new Map();
  const createActions = [];
  const results = [];
  for (const action of manifest.automatic_source_fixes) {
    const decisionStatus = decisions.get(action.finding_id)?.status;
    if (decisionStatus === 'applied') {
      results.push({ finding_id: action.finding_id, status: 'already_applied', message: 'the approved report already records this remediation as applied' });
      continue;
    }
    if (decisionStatus === 'manual_follow_up') {
      results.push({ finding_id: action.finding_id, status: 'manual_follow_up', message: 'the approved report already routes this item to manual follow-up' });
      continue;
    }
    if (action.operation === 'create_file_if_missing') createActions.push(action);
    else {
      const file = mapVaultPath(action.source_file, args.vaultRoot);
      if (!byFile.has(file)) byFile.set(file, []);
      byFile.get(file).push(action);
    }
  }
  const planOnly = args.command === 'plan';
  for (const [file, actions] of byFile) {
    if (!fs.existsSync(file)) {
      for (const action of actions) results.push({ finding_id: action.finding_id, status: 'manual_follow_up', message: `source file is missing: ${action.source_file}` });
      continue;
    }
    const original = fs.readFileSync(file, 'utf8');
    let working = original;
    const applied = [];
    for (const action of actions) {
      if (action.operation === 'repair_wikilink_target' && action.require_replacement_target && !targetExists(index, action.replacement_target)) {
        results.push({ finding_id: action.finding_id, status: 'manual_follow_up', message: `corrected target is still missing: ${action.replacement_target}` });
        continue;
      }
      const transformed = transformWikiLinks(working, action);
      if (transformed.changed === 0) {
        results.push({ finding_id: action.finding_id, status: 'manual_follow_up', message: 'the exact approved wikilink is no longer present' });
        continue;
      }
      working = transformed.output;
      applied.push({ action, changed: transformed.changed });
    }
    if (!applied.length) continue;
    if (!planOnly) {
      const backup = backupFile(file, args.vaultRoot, runBackupRoot);
      try {
        atomicWrite(file, working);
        const verified = fs.readFileSync(file, 'utf8');
        if (verified !== working) fail('post-write content verification failed');
      } catch (error) {
        fs.copyFileSync(backup, file);
        for (const item of applied) results.push({ finding_id: item.action.finding_id, status: 'manual_follow_up', message: `write failed and the source was rolled back: ${error.message}` });
        continue;
      }
    }
    for (const item of applied) results.push({ finding_id: item.action.finding_id, status: planOnly ? 'planned' : 'applied', message: `${item.changed} exact wikilink occurrence${item.changed === 1 ? '' : 's'} ${planOnly ? 'would be changed' : 'changed'}` });
  }
  for (const action of createActions) {
    const destination = mapVaultPath(action.destination_file, args.vaultRoot);
    if (fs.existsSync(destination)) {
      results.push({ finding_id: action.finding_id, status: 'already_applied', message: `${action.destination_file} already exists` });
      continue;
    }
    if (!planOnly) atomicWrite(destination, action.content);
    results.push({ finding_id: action.finding_id, status: planOnly ? 'planned' : 'applied', message: `${action.destination_file} ${planOnly ? 'would be created' : 'was created'}` });
  }
  if (!planOnly) {
    fs.mkdirSync(runBackupRoot, { recursive: true, mode: 0o750 });
    const reportBackup = path.join(runBackupRoot, 'Second-Brain Audit Review.before-remediation.md');
    fs.copyFileSync(args.reportFile, reportBackup, fs.constants.COPYFILE_EXCL);
    atomicWrite(args.reportFile, updateReportStatuses(reportMarkdown, results));
    fs.writeFileSync(path.join(runBackupRoot, 'remediation-result.json'), `${JSON.stringify({ manifest_id: manifest.manifest_id, completed_at: new Date().toISOString(), results }, null, 2)}\n`, { mode: 0o640 });
  }
  const counts = results.reduce((accumulator, result) => { accumulator[result.status] = (accumulator[result.status] || 0) + 1; return accumulator; }, {});
  return { schema_version: '1.0.0', executor_version: VERSION, mode: args.command, status: Object.keys(counts).some((key) => key === 'manual_follow_up') ? 'completed_with_manual_follow_up' : 'completed', manifest_id: manifest.manifest_id, result_counts: counts, n8n_workflow_updates_pending: manifest.n8n_workflow_updates.length, automatic_deletions_performed: false, entity_changes_performed: false, credential_changes_performed: false, backup_root: planOnly ? null : runBackupRoot, results };
}

try { console.log(JSON.stringify(execute(parseArguments(process.argv.slice(2))))); }
catch (error) { console.error(String(error.stack || error)); process.exitCode = 1; }
