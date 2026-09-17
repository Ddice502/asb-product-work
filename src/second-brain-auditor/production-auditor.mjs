#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const AUDITOR_VERSION = '1.0.0';
const SCHEMA_VERSION = '1.0.0';
const SEVERITY_ORDER = { critical: 0, warning: 1, advisory: 2 };
const SECRET_PATTERNS = [
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/i,
  /\bBearer\s+[A-Za-z0-9._~+\/-]{16,}/i,
  /\b(?:sk|pk|ghp|github_pat|xox[baprs])-?[A-Za-z0-9_-]{16,}\b/i,
  /\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\b/,
  /[?&](?:api_?key|token|secret|signature|password)=[^&#\s]{4,}/i,
];
const SAFE_TOKEN_LIKE_KEYS = new Set(['skipped_due_to_file_limit', 'skipped_due_to_size']);

function fail(message) {
  throw new Error(message);
}

function sha256(value) {
  return `sha256:${crypto.createHash('sha256').update(value).digest('hex')}`;
}

function shortHash(value, length = 12) {
  return crypto.createHash('sha256').update(value).digest('hex').slice(0, length).toUpperCase();
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

function canonicalStringify(value) {
  return JSON.stringify(canonicalize(value));
}

function parseJson(text, label) {
  try { return JSON.parse(text); }
  catch (error) { fail(`${label} is not valid JSON: ${error.message}`); }
}

function readJson(file, label) {
  return parseJson(fs.readFileSync(file, 'utf8'), label);
}

function readOptional(file) {
  try { return fs.readFileSync(file, 'utf8'); }
  catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

function ensureDir(directory) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o750 });
}

export function atomicWriteText(file, content, testHook = null) {
  ensureDir(path.dirname(file));
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}.${process.pid}.${crypto.randomBytes(5).toString('hex')}.tmp`);
  let handle;
  try {
    handle = fs.openSync(temporary, 'wx', 0o640);
    fs.writeFileSync(handle, content, 'utf8');
    fs.fsyncSync(handle);
    fs.closeSync(handle);
    handle = null;
    if (testHook) testHook();
    fs.renameSync(temporary, file);
    const directoryHandle = fs.openSync(path.dirname(file), 'r');
    try { fs.fsyncSync(directoryHandle); } finally { fs.closeSync(directoryHandle); }
  } catch (error) {
    if (handle !== null && handle !== undefined) fs.closeSync(handle);
    try { fs.unlinkSync(temporary); } catch {}
    throw error;
  }
  return Buffer.byteLength(content);
}

function atomicWriteJson(file, value, testHook = null) {
  return atomicWriteText(file, `${JSON.stringify(value, null, 2)}\n`, testHook);
}

function isInside(candidate, root) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

export function findSecret(value, currentPath = '$') {
  if (typeof value === 'string') {
    for (const [patternIndex, pattern] of SECRET_PATTERNS.entries()) {
      const match = value.match(pattern);
      if (match) return { json_path: currentPath, location_kind: 'string_value', pattern_index: patternIndex, matched_length: match[0].length, matched_sha256_prefix: shortHash(match[0], 16).toLowerCase(), matched_value_printed: false };
    }
    return null;
  }
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      const finding = findSecret(value[index], `${currentPath}[${index}]`);
      if (finding) return finding;
    }
    return null;
  }
  if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      if (!SAFE_TOKEN_LIKE_KEYS.has(key)) {
        for (const [patternIndex, pattern] of SECRET_PATTERNS.entries()) {
          const match = key.match(pattern);
          if (match) return { json_path: currentPath, location_kind: 'object_key', pattern_index: patternIndex, matched_length: match[0].length, matched_sha256_prefix: shortHash(match[0], 16).toLowerCase(), matched_value_printed: false };
        }
      }
      const segment = /^[A-Za-z_][A-Za-z0-9_]*$/.test(key) ? `.${key}` : `.[key-sha256:${shortHash(key, 12).toLowerCase()}]`;
      const finding = findSecret(child, `${currentPath}${segment}`);
      if (finding) return finding;
    }
  }
  return null;
}

function assertNoSecret(value, label) {
  const finding = findSecret(value);
  if (finding) fail(`${label} failed the secret scan at ${finding.json_path} (location_kind=${finding.location_kind}, pattern_index=${finding.pattern_index}, matched_length=${finding.matched_length}, matched_sha256_prefix=${finding.matched_sha256_prefix}, matched_value_printed=false).`);
}

export function validateConfig(config) {
  if (!config || config.schema_version !== SCHEMA_VERSION || config.auditor_version !== AUDITOR_VERSION || config.mode !== 'production_advisory') fail('Production configuration has the wrong schema, version, or mode.');
  if (config.timezone !== 'America/Kentucky/Louisville' || config.schedule_cron !== '0 4 * * *') fail('Approved timezone or daily schedule changed.');
  if (config.publication?.automatic_source_fixes !== false || config.publication?.automatic_deletion !== false) fail('Automatic source fixes and deletions must remain disabled.');
  const requiredPaths = ['charter', 'homepage', 'report', 'deletion_requests', 'state_root', 'snapshot_root', 'audit_root', 'export_root', 'log_root', 'backup_evidence_root'];
  for (const key of requiredPaths) if (typeof config.paths?.[key] !== 'string' || !path.isAbsolute(config.paths[key])) fail(`Configuration path ${key} must be absolute.`);
  if (config.paths.report !== '/Obsidian/90 System/Second-Brain Audit Review.md'
      || config.paths.homepage !== '/Obsidian/Home.md'
      || config.paths.deletion_requests !== '/Obsidian/90 System/Deletion Requests') fail('Approved Obsidian publication paths changed.');
  if (config.thresholds.capacity_warning_percent_remaining !== 25
      || config.thresholds.capacity_critical_percent_remaining !== 15
      || config.thresholds.inode_warning_percent_remaining !== 25
      || config.thresholds.inode_critical_percent_remaining !== 15
      || config.thresholds.voice_queue_items !== 10
      || config.thresholds.document_queue_items !== 15
      || config.thresholds.auditor_review_items !== 15
      || config.thresholds.stalled_after_minutes !== 90
      || config.thresholds.temporary_retention_days !== 14) fail('Approved thresholds changed.');
  if (config.local_ai.base_url !== 'http://192.168.1.169:11434' || config.local_ai.model !== 'qwen3:8b') fail('Approved local model endpoint or model changed.');
  if (config.notifications.base_url !== 'https://ntfy.sh' || config.notifications.topic !== 'jayaroh-voice') fail('Approved ntfy configuration changed.');
  assertNoSecret(config, 'Production configuration');
  return config;
}

function validateSnapshot(snapshot) {
  if (!snapshot || snapshot.schema_version !== SCHEMA_VERSION || snapshot.phase !== 1 || snapshot.mode !== 'collection_only') fail('Input is not an accepted Phase 1 collection snapshot.');
  if (snapshot.run?.collector_version !== '0.2.4') fail('Production audit requires collector version 0.2.4.');
  if (snapshot.privacy?.ordinary_note_bodies_collected !== false
      || snapshot.privacy?.credential_values_collected !== false
      || snapshot.privacy?.execution_payloads_collected !== false) fail('Snapshot privacy assertions failed.');
  if (!Array.isArray(snapshot.active_workflows) || !Array.isArray(snapshot.collection_errors)) fail('Snapshot arrays are missing.');
  assertNoSecret(snapshot, 'Production input snapshot');
  return snapshot;
}

export function stableFindingId(ruleId, subject, evidenceKey) {
  const normalized = [ruleId, subject, evidenceKey].map((part) => String(part || '').trim().toLowerCase()).join('\0');
  return `${ruleId}-${shortHash(normalized, 12)}`;
}

function finding(ruleId, severity, subject, evidenceKey, values) {
  const value = {
    finding_id: stableFindingId(ruleId, subject, evidenceKey),
    rule_id: ruleId,
    source: values.source || 'deterministic',
    severity,
    confidence: values.confidence ?? 1,
    subject,
    title: values.title,
    charter_alignment: values.charter_alignment || 'Keep the second brain reliable, understandable, private, and owner-controlled.',
    why_it_matters: values.why_it_matters,
    evidence_refs: [...new Set(values.evidence_refs || [])].sort(),
    manual_instructions: values.manual_instructions || [],
    validation_steps: values.validation_steps || [],
    rollback_steps: values.rollback_steps || [],
    first_detected_at: values.first_detected_at || null,
    status: 'awaiting_decision',
    decision_reason: '',
    decision_date: '',
    review_after: '',
  };
  if (!['critical', 'warning', 'advisory'].includes(value.severity)) fail(`Invalid severity for ${ruleId}.`);
  return value;
}

function treeFor(snapshot, root) {
  return (snapshot.filesystem?.trees || []).find((tree) => tree.root === root) || null;
}

function queueFiles(snapshot, root) {
  const tree = treeFor(snapshot, root);
  return (tree?.entries || []).filter((entry) => {
    if (entry.kind !== 'file') return false;
    const segments = String(entry.relative_path || '').split('/');
    return !segments.includes('.stfolder');
  });
}

function ageMinutes(timestamp, now) {
  const stamp = new Date(timestamp).getTime();
  return Number.isFinite(stamp) ? Math.max(0, (now.getTime() - stamp) / 60000) : null;
}

function scheduleExpressions(workflow) {
  const expressions = [];
  for (const node of workflow.nodes || []) {
    if (node.type !== 'n8n-nodes-base.scheduleTrigger') continue;
    const text = canonicalStringify(node.parameters || {});
    for (const match of text.matchAll(/(?:expression|cronExpression)\\?"?:\\?"([^"\\]+(?: [^"\\]+){4,6})/g)) expressions.push(match[1]);
  }
  return [...new Set(expressions)].sort();
}

function workflowHasErrorHandler(workflow) {
  return Boolean(workflow.settings?.errorWorkflow || workflow.settings?.errorWorkflowId || workflow.settings?.error_workflow_id);
}

function isErrorHandler(workflow) {
  return /(?:error handler|^\[err-)/i.test(workflow.name || '') || (workflow.nodes || []).some((node) => node.type === 'n8n-nodes-base.errorTrigger');
}

function disconnectedNodes(workflow) {
  const nodes = new Set((workflow.nodes || []).filter((node) => node.type !== "n8n-nodes-base.stickyNote").map((node) => node.name));
  const connected = new Set();
  for (const [source, groups] of Object.entries(workflow.connections || {})) {
    const flattened = Array.isArray(groups) ? groups.flat(3) : Object.values(groups || {}).flat(3);
    const targets = flattened.filter((item) => item && typeof item === 'object' && item.node);
    if (targets.length > 0) connected.add(source);
    for (const target of targets) connected.add(target.node);
  }
  return [...nodes].filter((name) => !connected.has(name)).sort();
}

export function collectDeterministicFindings(snapshot, previous, config, now) {
  const findings = [];
  const push = (value) => findings.push(value);

  for (const error of snapshot.collection_errors || []) {
    const subject = error.path || error.root || error.stage || 'collector';
    push(finding('COLLECTION-ERROR', 'warning', subject, canonicalStringify(error), {
      title: 'Approved evidence could not be collected',
      why_it_matters: 'An evidence gap can hide a broken workflow or produce incomplete recommendations.',
      evidence_refs: [`collection-error:${shortHash(canonicalStringify(error), 12)}`],
      manual_instructions: ['Open the indicated path from the n8n container.', 'Correct only the reported permission, missing-path, or limit issue.', 'Run the production auditor manually and confirm this collection error disappears.'],
      validation_steps: ['Confirm collection_error_count returns to zero.'],
      rollback_steps: ['Restore the prior mount or permission if the correction affects another pipeline.'],
    }));
  }

  for (const capacity of snapshot.filesystem?.capacities || []) {
    const remaining = Number(capacity.capacity_percent_remaining);
    const inodeRemaining = Number(capacity.inode_percent_remaining);
    if (Number.isFinite(remaining) && remaining <= config.thresholds.capacity_critical_percent_remaining) {
      push(finding('STORAGE-CRITICAL', 'critical', capacity.path, `capacity:${remaining}`, {
        title: 'Filesystem capacity is critically low',
        why_it_matters: 'The second brain can stop ingesting or publishing notes when a monitored filesystem fills.',
        evidence_refs: [`capacity:${capacity.path}`],
        manual_instructions: [`Inspect usage on the server with: sudo du -xhd1 '${capacity.path}' | sort -h`, 'Move or approve deletion only after confirming backups and file ownership.', 'Do not delete original recordings, original documents, transcripts, or extracted text.'],
        validation_steps: ['Confirm at least 15% capacity remains, then rerun the auditor.'],
        rollback_steps: ['Restore moved data from the verified destination if a dependent workflow fails.'],
      }));
    } else if (Number.isFinite(remaining) && remaining <= config.thresholds.capacity_warning_percent_remaining) {
      push(finding('STORAGE-WARNING', 'warning', capacity.path, `capacity:${remaining}`, {
        title: 'Filesystem capacity has reached the warning threshold',
        why_it_matters: 'Early warning preserves enough room for voice-first ingest and backups.',
        evidence_refs: [`capacity:${capacity.path}`],
        manual_instructions: [`Review usage with: sudo du -xhd1 '${capacity.path}' | sort -h`, 'Prioritize expired temporary files and superseded auditor-owned generations for review.'],
        validation_steps: ['Confirm more than 25% capacity remains after approved cleanup.'],
        rollback_steps: ['Recover any mistakenly moved auditor-owned file from the retained backup.'],
      }));
    }
    if (Number.isFinite(inodeRemaining) && inodeRemaining <= config.thresholds.inode_critical_percent_remaining) {
      push(finding('INODES-CRITICAL', 'critical', capacity.path, `inodes:${inodeRemaining}`, {
        title: 'Filesystem inode capacity is critically low',
        why_it_matters: 'A filesystem with no free inodes cannot create new notes even when bytes remain available.',
        evidence_refs: [`capacity:${capacity.path}`],
        manual_instructions: [`Inspect inode-heavy directories with: sudo find '${capacity.path}' -xdev -type f -printf '%h\n' | sort | uniq -c | sort -n | tail -50`, 'Review generated temporary or log files before approving deletion.'],
        validation_steps: ['Confirm at least 15% of inodes remain.'],
        rollback_steps: ['Restore any required file from backup if cleanup affects a workflow.'],
      }));
    } else if (Number.isFinite(inodeRemaining) && inodeRemaining <= config.thresholds.inode_warning_percent_remaining) {
      push(finding('INODES-WARNING', 'warning', capacity.path, `inodes:${inodeRemaining}`, {
        title: 'Filesystem inode capacity has reached the warning threshold',
        why_it_matters: 'Many small files can stop note creation before storage bytes are exhausted.',
        evidence_refs: [`capacity:${capacity.path}`],
        manual_instructions: [`Review file counts with: sudo find '${capacity.path}' -xdev -type f | wc -l`, 'Identify generated temporary or log files for owner-approved cleanup.'],
        validation_steps: ['Confirm more than 25% of inodes remain.'],
        rollback_steps: ['Restore required files from backup if an approved cleanup removes a dependency.'],
      }));
    }
  }

  const names = new Map();
  const scheduleOwners = new Map();
  for (const workflow of snapshot.active_workflows) {
    const normalizedName = String(workflow.name || '').trim().toLowerCase();
    if (!names.has(normalizedName)) names.set(normalizedName, []);
    names.get(normalizedName).push(workflow.id);
    if (!isErrorHandler(workflow) && !workflowHasErrorHandler(workflow)) {
      push(finding('WF-ERROR-HANDLER', 'warning', workflow.id, 'missing-error-workflow', {
        title: 'Active workflow has no assigned error workflow',
        why_it_matters: 'Unhandled failures can leave queues stalled without an immediate ntfy notification.',
        evidence_refs: [`workflow:${workflow.id}`],
        manual_instructions: [`Open the n8n workflow “${workflow.name}”.`, 'Open Workflow Settings and select the appropriate existing error-handler workflow.', 'Save without changing the workflow activation state.'],
        validation_steps: ['Export or recollect the workflow and confirm settings.errorWorkflow is populated.'],
        rollback_steps: ['Restore the previous Workflow Settings value if the selected handler causes recursive alerts.'],
      }));
    }
    for (const node of workflow.nodes || []) {
      if (node.disabled === true) push(finding('WF-DISABLED-NODE', 'warning', `${workflow.id}:${node.id || node.name}`, 'disabled-active-node', {
        title: 'Active workflow contains a disabled node',
        why_it_matters: 'A disabled node can silently bypass a required validation or filing step.',
        evidence_refs: [`workflow:${workflow.id}`, `node:${workflow.id}:${node.id || node.name}`],
        manual_instructions: [`Open “${workflow.name}” and inspect the disabled node “${node.name}”.`, 'Determine whether it should be enabled or removed; do not change it until its incoming and outgoing data contract is understood.', 'Apply the decision manually and run the workflow with a representative test item.'],
        validation_steps: ['Confirm the representative item reaches the intended final or review location.'],
        rollback_steps: ['Disable the node again or restore the exported workflow if validation fails.'],
      }));
    }
    for (const nodeName of disconnectedNodes(workflow)) push(finding('WF-DISCONNECTED-NODE', 'warning', `${workflow.id}:${nodeName}`, 'not-in-connections', {
      title: 'Active workflow contains a disconnected node',
      why_it_matters: 'Disconnected nodes add ambiguity and may represent an incomplete safety or notification path.',
      evidence_refs: [`workflow:${workflow.id}`, `node:${workflow.id}:${nodeName}`],
      manual_instructions: [`Open “${workflow.name}” and locate “${nodeName}”.`, 'Confirm whether it is intentionally retained for reference.', 'Connect or remove it manually only after exporting the workflow.'],
      validation_steps: ['Run a manual representative execution and confirm the intended path.'],
      rollback_steps: ['Reimport the pre-change workflow export.'],
    }));
    for (const expression of scheduleExpressions(workflow)) {
      if (!scheduleOwners.has(expression)) scheduleOwners.set(expression, []);
      scheduleOwners.get(expression).push({ id: workflow.id, name: workflow.name });
    }
    if (!isErrorHandler(workflow) && /entity.*(?:creation|people|project)|(?:people|project).*entity/i.test(workflow.name || '')) {
      const workflowText = canonicalStringify(workflow);
      if (!/(?:approval|approved|manual review|owner)/i.test(workflowText)) push(finding('GOV-ENTITY-APPROVAL', 'critical', workflow.id, 'approval-marker-absent', {
        title: 'Entity-creation workflow lacks visible owner-approval evidence',
        why_it_matters: 'Project creation and entity updates must remain owner-controlled under the charter.',
        evidence_refs: [`workflow:${workflow.id}`],
        manual_instructions: [`Open “${workflow.name}” and verify that creation requires an explicit approved decision or an owner voice instruction to create a project.`, 'Add an approval gate manually if no such condition exists.', 'Keep person aliases separate until you approve a merge.'],
        validation_steps: ['Test an unapproved entity candidate and confirm no canonical entity is created.'],
        rollback_steps: ['Restore the exported workflow if the approval gate blocks valid owner-approved input.'],
      }));
    }
  }
  for (const [name, ids] of names) if (name && ids.length > 1) push(finding('WF-DUPLICATE-NAME', 'warning', name, ids.sort().join(','), {
    title: 'Multiple active workflows use the same name',
    why_it_matters: 'Duplicate names make errors, approvals, and manual recovery instructions ambiguous.',
    evidence_refs: ids.map((id) => `workflow:${id}`),
    manual_instructions: ['Open each listed workflow and identify its distinct responsibility.', 'Rename them with unique pipeline and stage identifiers without changing their IDs.'],
    validation_steps: ['Confirm active workflow names are unique in n8n.'],
    rollback_steps: ['Restore the prior names if an external reference depends on them.'],
  }));
  for (const [expression, owners] of scheduleOwners) if (owners.length > 1) push(finding('WF-SCHEDULE-OVERLAP', 'advisory', expression, owners.map((owner) => owner.id).sort().join(','), {
    title: 'Multiple active workflows share the same schedule',
    why_it_matters: 'Simultaneous work can compete with voice-first ingest or consume the same queue.',
    evidence_refs: owners.map((owner) => `workflow:${owner.id}`),
    manual_instructions: [`Review the workflows scheduled at “${expression}”.`, 'Confirm they do not consume the same files or compete for the local model.', 'Stagger only those proven to overlap.'],
    validation_steps: ['Observe one scheduled cycle and confirm queue movement and runtimes remain normal.'],
    rollback_steps: ['Restore the prior cron expressions if staggering changes required ordering.'],
  }));

  const executionRecords = snapshot.execution_metadata?.records || [];
  const errorsByWorkflow = new Map();
  for (const execution of executionRecords) if (execution.status === 'error') {
    if (!errorsByWorkflow.has(execution.workflow_id)) errorsByWorkflow.set(execution.workflow_id, []);
    errorsByWorkflow.get(execution.workflow_id).push(execution.id);
  }
  for (const [workflowId, ids] of errorsByWorkflow) push(finding('WF-EXECUTION-ERRORS', ids.length >= 3 ? 'critical' : 'warning', workflowId, ids.sort().join(','), {
    title: 'Active workflow failed during the audit window',
    why_it_matters: 'Repeated execution failures can stall voice or document processing.',
    evidence_refs: ids.map((id) => `execution:${id}`),
    manual_instructions: [`Open workflow execution history for workflow ID ${workflowId}.`, 'Inspect the failed node without copying credential or payload data into the audit report.', 'Correct the specific node manually and retry one quarantined or test item.'],
    validation_steps: ['Confirm a representative retry succeeds and the queue decreases.'],
    rollback_steps: ['Restore the prior workflow export if the correction produces duplicate processing.'],
  }));

  const queueDefinitions = [
    { key: 'voice', root: config.queues.voice, limit: config.thresholds.voice_queue_items, maxAge: config.thresholds.voice_age_minutes },
    { key: 'documents', root: config.queues.documents, limit: config.thresholds.document_queue_items, maxAge: config.thresholds.document_age_minutes },
  ];
  for (const queue of queueDefinitions) {
    const files = queueFiles(snapshot, queue.root);
    if (files.length > queue.limit) push(finding('QUEUE-DEPTH', 'critical', queue.key, `${files.length}>${queue.limit}`, {
      title: `${queue.key === 'voice' ? 'Voice' : 'Document'} queue exceeds its normal limit`,
      why_it_matters: 'Ingest and data processing have priority over advisory AI work.',
      evidence_refs: [`queue:${queue.root}`],
      manual_instructions: [`Inspect the queue from the server without deleting anything: docker exec n8n find '${queue.root}' -maxdepth 1 -type f -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort`, 'Review the corresponding pipeline execution history and error handler.', 'Hold advisory model work until the queue returns below its limit.'],
      validation_steps: [`Confirm ${queue.limit} or fewer items remain and new items continue moving.`],
      rollback_steps: ['Return any manually moved item to its original queue if the pipeline cannot detect it.'],
    }));
    for (const file of files) {
      const age = ageMinutes(file.modified_at, now);
      if (age !== null && age > queue.maxAge) push(finding('QUEUE-AGE', 'critical', `${queue.key}:${file.relative_path}`, `age>${queue.maxAge}`, {
        title: `${queue.key === 'voice' ? 'Voice' : 'Document'} item exceeded its processing deadline`,
        why_it_matters: `The approved ${queue.key} processing objective is ${queue.maxAge} minutes.`,
        evidence_refs: [`path:${queue.root}/${file.relative_path}`],
        manual_instructions: [`Inspect the item and pipeline state without opening its content: docker exec n8n stat -- '${queue.root}/${file.relative_path.replaceAll("'", "'\\''")}'`, 'Review the responsible workflow executions and error handler.', 'Move or retry the item only through the approved pipeline recovery procedure.'],
        validation_steps: ['Confirm the item reaches its completed note or manual-review destination.'],
        rollback_steps: ['Return the item to the original queue if the recovery attempt fails.'],
      }));
    }
    const previousFiles = previous ? queueFiles(previous, queue.root) : [];
    const unchanged = previousFiles.length > 0 && canonicalStringify(previousFiles.map((item) => [item.relative_path, item.size_bytes, item.modified_at]).sort()) === canonicalStringify(files.map((item) => [item.relative_path, item.size_bytes, item.modified_at]).sort());
    const baselineAge = previous?.run?.completed_at ? ageMinutes(previous.run.completed_at, now) : null;
    if (files.length > 0 && unchanged && baselineAge !== null && baselineAge >= config.thresholds.stalled_after_minutes) push(finding('QUEUE-STALLED', 'critical', queue.key, `unchanged:${previous.run.run_id}`, {
      title: `${queue.key === 'voice' ? 'Voice' : 'Document'} queue has not changed for 90 minutes`,
      why_it_matters: 'An unchanged queue indicates a stalled pipeline even when it has not exceeded the item-count limit.',
      evidence_refs: [`queue:${queue.root}`, `snapshot:${previous.run.run_id}`, `snapshot:${snapshot.run.run_id}`],
      manual_instructions: ['Check the responsible workflow status, recent errors, and filesystem permissions.', 'Do not clear or delete the queue.', 'Retry one representative item only after identifying the stalled stage.'],
      validation_steps: ['Confirm the queue changes and the representative item reaches its expected destination.'],
      rollback_steps: ['Return the representative item to the prior queue location if recovery fails.'],
    }));
  }

  const markdownFiles = (snapshot.filesystem?.markdown_metadata || []).flatMap((group) => (group.files || []).map((file) => ({ ...file, root: group.root })));
  const allPaths = new Set((treeFor(snapshot, '/Obsidian')?.entries || []).map((entry) => entry.relative_path));
  const markdownNoExt = new Set(markdownFiles.map((file) => file.relative_path.replace(/\.md$/i, '')));
  const baseNames = new Map();

  // Build a filename-only index from the physical vault as well as the
  // collected Markdown set. Excluded notes are not audited as sources,
  // but they must remain valid link destinations.
  const physicalBaseNames = new Map();
  const physicalStack = ['/Obsidian'];

  while (physicalStack.length > 0) {
    const directory = physicalStack.pop();

    let entries = [];
    try {
      entries = fs.readdirSync(directory, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      const absolute = path.posix.join(directory, entry.name);

      if (entry.isDirectory()) {
        physicalStack.push(absolute);
        continue;
      }

      if (!entry.isFile() || path.posix.extname(entry.name).toLowerCase() !== '.md') {
        continue;
      }

      const base = path.posix.basename(entry.name, '.md').toLowerCase();

      if (!physicalBaseNames.has(base)) {
        physicalBaseNames.set(base, []);
      }

      physicalBaseNames.get(base).push(
        path.posix.relative('/Obsidian', absolute),
      );
    }
  }

  for (const file of markdownFiles) {
    const base = path.posix.basename(file.relative_path, '.md').toLowerCase();
    if (!baseNames.has(base)) baseNames.set(base, []);
    baseNames.get(base).push(file.relative_path);
    if (file.frontmatter?.closing_delimiter_missing) push(finding('MD-FRONTMATTER', 'warning', file.relative_path, 'closing-delimiter-missing', {
      title: 'Markdown frontmatter has no closing delimiter',
      why_it_matters: 'Malformed frontmatter prevents reliable filing, templates, and metadata queries.',
      evidence_refs: [`path:/Obsidian/${file.relative_path}`],
      manual_instructions: [`Open “${file.relative_path}” in Obsidian.`, 'Add the missing closing --- delimiter immediately after the frontmatter fields.', 'Do not change the note body during this repair.'],
      validation_steps: ['Reopen the note properties and confirm Obsidian recognizes the fields.'],
      rollback_steps: ['Remove only the added delimiter if it was placed at the wrong boundary.'],
    }));
  }
  const decodeTarget = (target) => { try { return decodeURIComponent(target); } catch { return target; } };
  for (const file of markdownFiles) for (const rawTarget of file.outbound_link_targets || []) {
    const target = decodeTarget(String(rawTarget).split('#')[0].trim());
    if (!target || /^(?:https?:|mailto:|tel:|obsidian:|data:)/i.test(target)) continue;
    const withoutQuery = target.split('?')[0].replace(/^\.\//, '');
    const sourceDir = path.posix.dirname(file.relative_path);
    const relativeCandidate = path.posix.normalize(path.posix.join(sourceDir, withoutQuery));
    const candidates = new Set([withoutQuery.replace(/^\//, ''), relativeCandidate]);
    for (const candidate of [...candidates]) {
      if (!path.posix.extname(candidate)) { candidates.add(`${candidate}.md`); candidates.add(candidate.replace(/\.md$/i, '')); }
    }
    const targetBase = path.posix.basename(withoutQuery, '.md').toLowerCase();
    const baseMatches = baseNames.get(targetBase) || [];
    const physicalBaseMatches = physicalBaseNames.get(targetBase) || [];
    const physicalResolved = [...candidates].some((candidate) => {
      const clean = candidate.replace(/^\/+/, '');
      const absolute = path.posix.join('/Obsidian', clean);
      return fs.existsSync(absolute)
        || (!path.posix.extname(clean) && fs.existsSync(`${absolute}.md`));
    });
    const resolved = physicalResolved
      || [...candidates].some((candidate) => allPaths.has(candidate) || markdownNoExt.has(candidate.replace(/\.md$/i, '')))
      || baseMatches.length === 1
      || (
        !String(withoutQuery).includes('/')
        && physicalBaseMatches.length === 1
      );
    if (!resolved) push(finding('MD-BROKEN-LINK', 'warning', file.relative_path, target, {
      title: 'Markdown link target was not found',
      why_it_matters: 'Broken links reduce retrieval quality and make project context incomplete.',
      evidence_refs: [`path:/Obsidian/${file.relative_path}`, `link-target:${shortHash(target, 12)}`],
      manual_instructions: [`Open “${file.relative_path}” and locate the link to “${target}”.`, 'Correct it to an existing note or attachment, or remove the link if it is obsolete.'],
      validation_steps: ['Ctrl-click the corrected link and confirm it opens the intended target.'],
      rollback_steps: ['Restore the original link text from Obsidian Sync history if it pointed to a temporarily unavailable target.'],
    }));
    if (baseMatches.length > 1 && !String(withoutQuery).includes('/')) push(finding('MD-AMBIGUOUS-LINK', 'advisory', file.relative_path, `${target}:${baseMatches.sort().join(',')}`, {
      title: 'Unqualified wikilink matches multiple notes',
      why_it_matters: 'Ambiguous note names can connect the wrong project or person during retrieval.',
      evidence_refs: [`path:/Obsidian/${file.relative_path}`, ...baseMatches.map((match) => `path:/Obsidian/${match}`)],
      manual_instructions: [`Replace “[[${target}]]” with a path-qualified link to the intended note.`, 'Leave the visible alias unchanged if desired.'],
      validation_steps: ['Confirm the link opens the intended note.'],
      rollback_steps: ['Restore the unqualified link if the selected path was incorrect.'],
    }));
  }

  for (const [evidenceName, evidencePath] of Object.entries(config.backup_evidence_files || {})) {
    const relative = path.relative('/State/Second Brain', evidencePath).replaceAll(path.sep, '/');
    const record = (snapshot.filesystem?.allowlisted_full_content?.files || []).find((file) => file.scope_root === '/State/Second Brain' && file.relative_path === relative);
    if (!record?.content) {
      push(finding('BACKUP-EVIDENCE', evidenceName === 'restore_test' ? 'warning' : 'critical', evidenceName, 'evidence-file-missing', {
        title: `${evidenceName.replaceAll('_', ' ')} evidence is missing`,
        why_it_matters: 'The auditor cannot claim recovery readiness without timestamped, verifiable evidence.',
        evidence_refs: [`path:${evidencePath}`],
        manual_instructions: [`Complete the ${evidenceName.replaceAll('_', ' ')} procedure.`, `Write its sanitized result to “${evidencePath}” using the supplied backup-evidence script.`, 'Do not include encryption keys, passwords, or file contents in the evidence record.'],
        validation_steps: ['Rerun the auditor and confirm the evidence file is recognized and current.'],
        rollback_steps: ['Remove only the incorrect evidence record; do not remove a backup generation.'],
      }));
      continue;
    }
    try {
      const evidence = parseJson(record.content, evidencePath);
      const stamp = new Date(evidence.completed_at || evidence.verified_at || evidence.tested_at || 0);
      const isMonthlyEvidence = evidenceName === 'monthly_external_drive' || evidenceName === 'restore_test';
      const maxAgeMinutes = isMonthlyEvidence
        ? config.thresholds.restore_test_stale_days * 1440
        : config.thresholds.backup_stale_hours * 60;
      const staleSeverity = isMonthlyEvidence ? 'warning' : 'critical';
      if (Number.isNaN(stamp.getTime()) || ageMinutes(stamp, now) > maxAgeMinutes || evidence.status !== 'verified') push(finding('BACKUP-STALE', staleSeverity, evidenceName, canonicalStringify({ stamp: stamp.toISOString?.() || null, status: evidence.status }), {
        title: `${evidenceName.replaceAll('_', ' ')} evidence is stale or unverified`,
        why_it_matters: 'A backup that has not been verified may not satisfy the one-to-two-day data-loss objective.',
        evidence_refs: [`path:${evidencePath}`],
        manual_instructions: [`Run the ${evidenceName.replaceAll('_', ' ')} verification procedure.`, 'Confirm encryption, completeness, and the retained-generation count before replacing its evidence record.'],
        validation_steps: ['Confirm the evidence record has status verified and a current completion timestamp.'],
        rollback_steps: ['Keep the prior verified generation until the replacement verification succeeds.'],
      }));
    } catch {
      push(finding('BACKUP-EVIDENCE-INVALID', 'critical', evidenceName, 'invalid-json', {
        title: `${evidenceName.replaceAll('_', ' ')} evidence is not valid JSON`,
        why_it_matters: 'Invalid evidence prevents deterministic recovery checks.',
        evidence_refs: [`path:${evidencePath}`],
        manual_instructions: ['Replace the evidence record using the supplied evidence-writing command after a verified backup or restore test.'],
        validation_steps: ['Validate it with jq and rerun the auditor.'],
        rollback_steps: ['Restore the prior evidence file from auditor state backup if needed.'],
      }));
    }
  }

  const tempCandidates = queueFiles(snapshot, config.queues.temporary)
    .filter((file) => {
      const relative = String(file.relative_path || "");
      if (relative === "Migration_Hold" || relative.startsWith("Migration_Hold/")) return false;
      const age = ageMinutes(file.modified_at, now);
      return age !== null && age >= config.thresholds.temporary_retention_days * 1440;
    })
    .sort((a, b) => a.relative_path.localeCompare(b.relative_path));

  findings.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || a.finding_id.localeCompare(b.finding_id));
  return { findings, tempCandidates };
}

function parseDecisionBlocks(markdown) {
  const decisions = new Map();
  if (!markdown) return decisions;
  const expression = /<!-- auditor-finding:([^>]+) -->\s*```yaml\s*([\s\S]*?)```/g;
  for (const match of markdown.matchAll(expression)) {
    const raw = {};
    for (const line of match[2].split(/\r?\n/)) {
      const field = line.match(/^(status|decision_reason|decision_date|review_after):\s*(.*)$/);
      if (field) raw[field[1]] = field[2];
    }
    decisions.set(match[1].trim(), raw);
  }
  return decisions;
}

function scalarValue(raw, fallback = '') {
  if (raw === undefined || raw === null) return fallback;
  const trimmed = String(raw).trim();
  try { return JSON.parse(trimmed); } catch {}

  // Recover an accidentally shell-escaped outer JSON quote pair, for example
  // \"accepted reason\". Only the outer escapes are changed; valid inner JSON
  // escapes remain untouched.
  const repairedOuterQuotes = trimmed
    .replace(/^\\(["'])/, '$1')
    .replace(/\\(["'])$/, '$1');
  if (repairedOuterQuotes !== trimmed) {
    try { return JSON.parse(repairedOuterQuotes); } catch {}
  }

  if (trimmed.length >= 2 && trimmed.startsWith("'") && trimmed.endsWith("'")) {
    return trimmed.slice(1, -1).replaceAll("''", "'");
  }
  return trimmed;
}

function normalizeDecision(raw = {}, defaults = {}) {
  return {
    status: String(scalarValue(raw.status, defaults.status ?? 'awaiting_decision') || 'awaiting_decision').trim(),
    decision_reason: String(scalarValue(raw.decision_reason, defaults.decision_reason ?? '') ?? ''),
    decision_date: String(scalarValue(raw.decision_date, defaults.decision_date ?? '') ?? ''),
    review_after: String(scalarValue(raw.review_after, defaults.review_after ?? '') ?? ''),
  };
}

function ownerDecisionRegistryPath(config) {
  return path.join(config.paths.state_root, 'owner-decisions.json');
}

function loadOwnerDecisionRegistry(config) {
  const file = ownerDecisionRegistryPath(config);
  const text = readOptional(file);
  if (text === null) return { schema_version: SCHEMA_VERSION, updated_at: null, decisions: {} };
  const value = parseJson(text, 'Owner decision registry');
  if (!value || value.schema_version !== SCHEMA_VERSION || !value.decisions || typeof value.decisions !== 'object' || Array.isArray(value.decisions)) {
    fail('Owner decision registry has the wrong schema.');
  }
  assertNoSecret(value, 'Owner decision registry');
  return value;
}

function persistentDecisionMap(registry) {
  return new Map(Object.entries(registry?.decisions || {}));
}

function isPersistentOwnerDecision(decision) {
  const status = String(decision?.status || '').trim();
  return Boolean(status && status !== 'awaiting_decision');
}

function buildOwnerDecisionRegistry(existingRegistry, existingMarkdown, reconciled, updatedAt) {
  const decisions = { ...(existingRegistry?.decisions || {}) };

  // The current report is the owner's authoritative edit surface. A current
  // non-awaiting decision replaces the stored decision. Changing a current
  // block back to awaiting_decision intentionally clears any stored decision.
  for (const [findingId, raw] of parseDecisionBlocks(existingMarkdown)) {
    const normalized = normalizeDecision(raw);
    if (isPersistentOwnerDecision(normalized)) decisions[findingId] = normalized;
    else delete decisions[findingId];
  }

  // Reconciled findings may have inherited a decision from the persistent
  // registry. Keep those decisions even if the finding later disappears from
  // a generated report, so a future recurrence does not ask the owner again.
  for (const item of reconciled) {
    const normalized = normalizeDecision(item, item);
    if (isPersistentOwnerDecision(normalized)) decisions[item.finding_id] = normalized;
  }

  return {
    schema_version: SCHEMA_VERSION,
    updated_at: updatedAt,
    decisions: canonicalize(decisions),
  };
}

export function reconcileFindings(findings, existingMarkdown, persistentDecisions = new Map()) {
  const reportDecisions = parseDecisionBlocks(existingMarkdown);
  return findings.map((item) => {
    const source = reportDecisions.get(item.finding_id) || persistentDecisions.get(item.finding_id) || null;
    if (!source) return { ...item, decision_raw: null };
    const normalized = normalizeDecision(source, item);
    return {
      ...item,
      ...normalized,
      // Keep a canonical decision snapshot in the audit record instead of
      // perpetuating formatting mistakes from the editable Markdown block.
      decision_raw: { ...normalized },
    };
  });
}

function decisionLine(item, key, fallback) {
  const value = item[key] !== undefined
    ? item[key]
    : scalarValue(item.decision_raw?.[key], fallback);
  if (key === 'status') return `${key}: ${String(value || fallback).trim() || fallback}`;
  return `${key}: ${JSON.stringify(String(value ?? fallback))}`;
}

function preserveWeeklyReview(existingMarkdown) {
  const match = existingMarkdown?.match(/<!-- auditor-weekly-review:start -->[\s\S]*?<!-- auditor-weekly-review:end -->/);
  if (match) return match[0];
  return `<!-- auditor-weekly-review:start -->
## Weekly usefulness review

- Rating (1–5):
- What helped this week?
- What created friction?
- What feature or change would help?
- Which project did the system help advance or complete?
<!-- auditor-weekly-review:end -->`;
}

export function renderReport(audit, existingMarkdown = '') {
  const counts = { critical: 0, warning: 0, advisory: 0 };
  const statuses = {};
  for (const item of audit.findings) {
    counts[item.severity] += 1;
    statuses[item.status] = (statuses[item.status] || 0) + 1;
  }
  const lines = [
    '---',
    'type: second-brain-audit-review',
    `last_audit: ${JSON.stringify(audit.completed_at)}`,
    `snapshot_id: ${JSON.stringify(audit.snapshot_id)}`,
    `overall_status: ${audit.overall_status}`,
    `finding_count: ${audit.findings.length}`,
    '---',
    '',
    '# Second-Brain Audit Review',
    '',
    `Last successful audit: ${audit.completed_at}`,
    '',
    `Overall status: **${audit.overall_status.toUpperCase()}**`,
    '',
    `Evidence window: ${audit.window_start} through ${audit.window_end}`,
    '',
    `Snapshot: \`${audit.snapshot_id}\`  `,
    `Charter hash: \`${audit.charter_hash}\`  `,
    `Configuration hash: \`${audit.config_hash}\``,
    '',
    '## At a glance',
    '',
    `- Critical: ${counts.critical}`,
    `- Warning: ${counts.warning}`,
    `- Advisory: ${counts.advisory}`,
    `- Awaiting decision: ${statuses.awaiting_decision || 0}`,
    `- Local AI review: ${audit.local_ai.status}`,
    `- Deletion requests generated or refreshed: ${audit.deletion_requests.length}`,
    '',
  ];
  if (audit.local_ai.status === 'deferred') lines.push('> [!warning] Local AI review was deferred because ingest or data processing had priority. Deterministic checks still completed.', '');
  if (audit.evidence_gaps.length) {
    lines.push('## Evidence gaps', '');
    for (const gap of audit.evidence_gaps) lines.push(`- ${gap}`);
    lines.push('');
  }
  lines.push('## Findings', '');
  if (audit.findings.length === 0) lines.push('No actionable findings were detected.', '');
  for (const item of audit.findings) {
    lines.push(`<!-- auditor-finding:${item.finding_id} -->`, '```yaml', `finding_id: ${item.finding_id}`,
      decisionLine(item, 'status', 'awaiting_decision'),
      decisionLine(item, 'decision_reason', ''),
      decisionLine(item, 'decision_date', ''),
      decisionLine(item, 'review_after', ''),
      '```', '', `### ${item.title}`, '',
      `**Severity:** ${item.severity}  `,
      `**Source:** ${item.source}  `,
      `**Confidence:** ${item.confidence}  `,
      `**Charter alignment:** ${item.charter_alignment}`, '',
      `**Why this matters:** ${item.why_it_matters}`, '',
      '**Evidence:**', '');
    for (const ref of item.evidence_refs) lines.push(`- \`${ref}\``);
    lines.push('', '**Manual instructions:**', '');
    item.manual_instructions.forEach((step, index) => lines.push(`${index + 1}. ${step}`));
    lines.push('', '**Validation:**', '');
    item.validation_steps.forEach((step, index) => lines.push(`${index + 1}. ${step}`));
    lines.push('', '**Rollback:**', '');
    item.rollback_steps.forEach((step, index) => lines.push(`${index + 1}. ${step}`));
    lines.push('');
  }
  lines.push(preserveWeeklyReview(existingMarkdown), '');
  return `${lines.join('\n').trim()}\n`;
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\\''")}'`;
}

function hostTempPath(relative) {
  return path.posix.join('/AI/Temp', relative === '.' ? '' : relative);
}

export function renderDeletionRequest(request, existing = '') {
  const decisions = parseDecisionBlocks(existing);
  const raw = decisions.get(request.request_id) || null;
  const pseudo = { decision_raw: raw };
  const total = request.candidates.reduce((sum, item) => sum + Number(item.size_bytes || 0), 0);
  const paths = request.candidates.map((item) => hostTempPath(item.relative_path));
  const lines = [
    '---',
    'type: second-brain-deletion-request',
    `request_id: ${request.request_id}`,
    `created_at: ${JSON.stringify(request.created_at)}`,
    `candidate_count: ${request.candidates.length}`,
    `total_bytes: ${total}`,
    '---', '',
    `# Deletion Request ${request.request_id}`, '',
    `<!-- auditor-finding:${request.request_id} -->`,
    '```yaml',
    `finding_id: ${request.request_id}`,
    decisionLine(pseudo, 'status', 'awaiting_decision'),
    decisionLine(pseudo, 'decision_reason', ''),
    decisionLine(pseudo, 'decision_date', ''),
    decisionLine(pseudo, 'review_after', ''),
    '```', '',
    `This request contains ${request.candidates.length} file(s) from the approved temporary queue, totaling ${total} bytes, that were at least 14 days old when audited. Nothing was deleted.`, '',
    '## Candidates', '',
  ];
  for (const candidate of request.candidates) lines.push(`- \`${hostTempPath(candidate.relative_path)}\` — ${candidate.size_bytes} bytes — modified ${candidate.modified_at}`);
  lines.push('', '## Confirm before deletion', '',
    '1. Confirm no voice or document workflow is currently processing these paths.',
    '2. Confirm each candidate is temporary output rather than an original recording, original document, transcript, or extracted-text archive.',
    '3. Confirm the most recent server and workstation backups are verified.',
    '4. Change the decision status above to `approved` and record the reason and date.', '',
    '## Copy/paste validation command', '', '```bash',
    `sudo stat -- ${paths.map(shellQuote).join(' \\\n+  ')}`, '```', '',
    '## Copy/paste deletion command', '',
    '> [!danger] Run this only after approving the request. This deletion is not automatically recoverable except from backup.', '',
    '```bash', `sudo rm -- ${paths.map(shellQuote).join(' \\\n+  ')}`, '```', '',
    '## Post-deletion validation', '', '```bash',
    `sudo test ! -e ${shellQuote(paths[0])} && echo 'First candidate is absent.'`, '```', '',
    'If any candidate was removed incorrectly, restore it from the latest verified backup before rerunning the affected pipeline.', '');
  return `${lines.join('\n').trim()}\n`;
}

export function buildDeletionRequests(candidates, config, now) {
  const maximum = config.limits.maximum_deletion_candidates_per_request;
  const requests = [];
  for (let offset = 0; offset < candidates.length; offset += maximum) {
    const chunk = candidates.slice(offset, offset + maximum);
    const key = chunk.map((item) => `${item.relative_path}\0${item.size_bytes}\0${item.modified_at}`).join('\n');
    requests.push({ request_id: `TEMP-DELETE-${shortHash(key, 12)}`, created_at: now.toISOString(), candidates: chunk });
  }
  return requests;
}

export function homepageBlock() {
  return `<!-- second-brain-auditor-deletion-summary:start -->
## Deletion requests

\`\`\`dataviewjs
const folder = app.vault.getAbstractFileByPath("90 System/Deletion Requests");
const files = folder?.children?.filter(file => file.extension === "md") ?? [];
const records = await Promise.all(files.map(async file => {
  const text = await app.vault.cachedRead(file);
  const status = text.match(/^status:\\s*([^\\n#]+)/m)?.[1]?.trim()?.replace(/^['"]|['"]$/g, "") ?? "awaiting_decision";
  const created = text.match(/^created_at:\\s*["']?([^"'\\n]+)["']?/m)?.[1] ?? null;
  return { status, created };
}));
const pending = records.filter(record => record.status === "awaiting_decision");
const oldest = pending.map(record => Date.parse(record.created)).filter(Number.isFinite).sort((a, b) => a - b)[0];
const age = oldest ? Math.floor((Date.now() - oldest) / 86400000) + " day(s)" : "none";
dv.paragraph("**Awaiting approval:** " + pending.length + "  \\n**Oldest request:** " + age + "  \\n[Open deletion requests](obsidian://search?vault=Second%20Brain&query=path%3A%2290%20System%2FDeletion%20Requests%22)");
\`\`\`
<!-- second-brain-auditor-deletion-summary:end -->`;
}

export function installHomepageBlock(existing) {
  const block = homepageBlock();
  const expression = /<!-- second-brain-auditor-deletion-summary:start -->[\s\S]*?<!-- second-brain-auditor-deletion-summary:end -->/;
  if (expression.test(existing)) return existing.replace(expression, block);
  return `${existing.trimEnd()}\n\n${block}\n`;
}

function findPreviousSnapshot(snapshotFile, snapshot) {
  const directory = path.dirname(snapshotFile);
  let names;
  try { names = fs.readdirSync(directory).filter((name) => name.endsWith('.snapshot.json')).sort(); }
  catch { return null; }
  const current = path.basename(snapshotFile);
  const earlier = names.filter((name) => name < current).pop();
  if (!earlier) return null;
  try {
    const value = validateSnapshot(readJson(path.join(directory, earlier), 'Previous snapshot'));
    return value.run?.run_id === snapshot.run?.run_id ? null : value;
  } catch { return null; }
}

function resourceGuard(snapshot, config) {
  const reasons = [];
  const voiceProcessing = queueFiles(snapshot, config.queues.voice_processing).length;
  const documentProcessing = queueFiles(snapshot, config.queues.document_processing).length;
  const voiceQueue = queueFiles(snapshot, config.queues.voice).length;
  const documentQueue = queueFiles(snapshot, config.queues.documents).length;
  if (config.resource_guard.defer_local_ai_when_voice_processing_has_items && voiceProcessing > 0) reasons.push(`voice processing has ${voiceProcessing} item(s)`);
  if (config.resource_guard.defer_local_ai_when_document_processing_has_items && documentProcessing > 0) reasons.push(`document processing has ${documentProcessing} item(s)`);
  if (config.resource_guard.defer_local_ai_when_queue_over_limit && voiceQueue > config.thresholds.voice_queue_items) reasons.push(`voice queue has ${voiceQueue} item(s)`);
  if (config.resource_guard.defer_local_ai_when_queue_over_limit && documentQueue > config.thresholds.document_queue_items) reasons.push(`document queue has ${documentQueue} item(s)`);
  return { defer: reasons.length > 0, reasons };
}

function charterContent(snapshot) {
  const record = (snapshot.filesystem?.allowlisted_full_content?.files || []).find((file) => /AI Second Brain Charter\.md$/i.test(file.relative_path || ''));
  return record?.content || 'Charter content was not present; use the snapshot charter hash and deterministic rules only.';
}

export function allowedEvidenceRefsFromDomain(evidence) {
  const refs = new Set();
  const visit = (value) => {
    if (Array.isArray(value)) {
      for (const child of value) visit(child);
      return;
    }
    if (!value || typeof value !== 'object') return;
    if (typeof value.evidence_id === 'string' && value.evidence_id.trim()) refs.add(value.evidence_id);
    if (Array.isArray(value.evidence_refs)) {
      for (const ref of value.evidence_refs) if (typeof ref === 'string' && ref.trim()) refs.add(ref);
    }
    for (const child of Object.values(value)) visit(child);
  };
  visit(evidence);
  return refs;
}

function pathEvidenceRef(root, relativePath) {
  const suffix = relativePath === '.' ? '' : `/${relativePath}`;
  return `path:${root}${suffix}`.replace(/\/$/, '');
}

function boundedDomainEvidence(domain, snapshot, deterministic, config) {
  const base = {
    domain,
    snapshot_id: snapshot.run.run_id,
    charter_hash: snapshot.run.charter_hash,
    charter: charterContent(snapshot),
    deterministic_findings: deterministic.map((item) => ({ evidence_id: `finding:${item.finding_id}`, severity: item.severity, title: item.title, subject: item.subject, evidence_refs: item.evidence_refs })),
  };
  if (domain === 'n8n_workflows') {
    base.workflows = snapshot.active_workflows.map((workflow) => ({
      ...workflow,
      evidence_id: `workflow:${workflow.id}`,
      nodes: (workflow.nodes || []).map((node) => ({
        ...node,
        evidence_id: `node:${workflow.id}:${node.id || node.name}`,
      })),
    }));
    base.execution_metadata = {
      ...(snapshot.execution_metadata || {}),
      records: (snapshot.execution_metadata?.records || []).map((execution) => ({
        ...execution,
        evidence_id: `execution:${execution.id}`,
      })),
    };
  } else if (domain === 'obsidian_structure') {
    base.markdown_metadata = (snapshot.filesystem?.markdown_metadata || []).map((group) => ({
      ...group,
      files: (group.files || []).map((file) => ({
        ...file,
        evidence_id: pathEvidenceRef(group.root, file.relative_path),
      })),
    }));
    base.allowlisted_content = (snapshot.filesystem?.allowlisted_full_content?.files || [])
      .filter((file) => /Obsidian|Workstation Obsidian/i.test(`${file.scope_root}/${file.relative_path}`))
      .map((file) => ({
        ...file,
        evidence_id: pathEvidenceRef(file.scope_root, file.relative_path),
      }));
  } else {
    base.capacities = (snapshot.filesystem?.capacities || []).map((capacity) => ({
      ...capacity,
      evidence_id: `capacity:${capacity.path}`,
    }));
    base.operational_trees = (snapshot.filesystem?.trees || [])
      .filter((tree) => tree.category === 'operational')
      .map((tree) => ({
        ...tree,
        entries: (tree.entries || []).map((entry) => ({
          ...entry,
          evidence_id: pathEvidenceRef(tree.root, entry.relative_path),
        })),
      }));
    base.allowlisted_content = (snapshot.filesystem?.allowlisted_full_content?.files || [])
      .filter((file) => !/Obsidian/i.test(file.scope_root || ''))
      .map((file) => ({
        ...file,
        evidence_id: pathEvidenceRef(file.scope_root, file.relative_path),
      }));
    base.retention_policy = config.retention_policy;
  }
  const maximum = config.local_ai.maximum_evidence_bytes_per_domain;
  let serialized = JSON.stringify(base);
  if (Buffer.byteLength(serialized) <= maximum) return base;
  if (base.allowlisted_content) {
    base.allowlisted_content = base.allowlisted_content.map((file) => ({ evidence_id: file.evidence_id, scope_root: file.scope_root, relative_path: file.relative_path, scope_type: file.scope_type, size_bytes: file.size_bytes, source_sha256: file.source_sha256, content_omitted_due_to_domain_limit: true }));
    serialized = JSON.stringify(base);
  }
  if (Buffer.byteLength(serialized) <= maximum) return base;
  if (base.workflows) base.workflows = base.workflows.map((workflow) => ({ evidence_id: workflow.evidence_id, id: workflow.id, name: workflow.name, settings: workflow.settings, nodes: workflow.nodes.map((node) => ({ evidence_id: node.evidence_id, id: node.id, name: node.name, type: node.type, disabled: node.disabled, credential_types: node.credential_types })), connections: workflow.connections }));
  if (base.markdown_metadata) base.markdown_metadata = base.markdown_metadata.map((group) => ({ root: group.root, exists: group.exists, truncated: group.truncated, files: group.files.map((file) => ({ evidence_id: file.evidence_id, relative_path: file.relative_path, size_bytes: file.size_bytes, modified_at: file.modified_at, frontmatter: file.frontmatter, outbound_link_targets: file.outbound_link_targets })) }));
  serialized = JSON.stringify(base);
  if (Buffer.byteLength(serialized) > maximum && base.workflows) {
    base.workflows = base.workflows.map((workflow) => ({
      evidence_id: workflow.evidence_id,
      id: workflow.id,
      name: workflow.name,
      settings: workflow.settings,
      node_count: workflow.nodes.length,
      disabled_nodes: workflow.nodes.filter((node) => node.disabled).map((node) => node.name),
      node_types: [...new Set(workflow.nodes.map((node) => node.type))].sort(),
    }));
  }
  if (Buffer.byteLength(JSON.stringify(base)) > maximum && base.markdown_metadata) {
    base.markdown_metadata = base.markdown_metadata.map((group) => ({
      root: group.root,
      exists: group.exists,
      truncated: group.truncated,
      file_count: group.files.length,
      malformed_frontmatter: group.files.filter((file) => file.frontmatter?.closing_delimiter_missing).map((file) => file.relative_path),
      files_with_links: group.files.filter((file) => file.outbound_link_targets?.length).slice(0, 200).map((file) => ({ evidence_id: file.evidence_id, relative_path: file.relative_path, outbound_link_targets: file.outbound_link_targets })),
    }));
  }
  if (Buffer.byteLength(JSON.stringify(base)) > maximum && base.operational_trees) {
    base.operational_trees = base.operational_trees.map((tree) => {
      const files = (tree.entries || []).filter((entry) => entry.kind === 'file');
      return { root: tree.root, exists: tree.exists, truncated: tree.truncated, entry_count: (tree.entries || []).length, file_count: files.length, oldest_files: files.slice().sort((a, b) => String(a.modified_at).localeCompare(String(b.modified_at))).slice(0, 50) };
    });
  }
  serialized = JSON.stringify(base);
  if (Buffer.byteLength(serialized) > maximum) {
    const minimal = { domain, snapshot_id: snapshot.run.run_id, charter_hash: snapshot.run.charter_hash, charter: charterContent(snapshot), deterministic_findings: base.deterministic_findings, evidence_truncated: true };
    if (Buffer.byteLength(JSON.stringify(minimal)) > maximum) minimal.charter = 'Charter content omitted due to the domain evidence limit; use the charter hash and deterministic finding alignment.';
    return minimal;
  }
  return base;
}

export function modelResponseSchema(maximum) {
  return {
    type: 'object', additionalProperties: false, required: ['findings'], properties: {
      findings: { type: 'array', maxItems: maximum, items: { type: 'object', additionalProperties: false,
        required: ['rule_id', 'severity', 'confidence', 'subject', 'title', 'charter_alignment', 'why_it_matters', 'evidence_refs', 'manual_instructions', 'validation_steps', 'rollback_steps'],
        properties: {
          rule_id: { type: 'string', pattern: '^AI-[A-Z0-9-]{2,40}$', maxLength: 43 }, severity: { enum: ['critical', 'warning', 'advisory'] }, confidence: { type: 'number', minimum: 0.65, maximum: 1 }, subject: { type: 'string', minLength: 1, maxLength: 160 }, title: { type: 'string', minLength: 1, maxLength: 160 }, charter_alignment: { type: 'string', minLength: 1, maxLength: 300 }, why_it_matters: { type: 'string', minLength: 1, maxLength: 300 }, evidence_refs: { type: 'array', items: { type: 'string', minLength: 1, maxLength: 500 }, minItems: 1, maxItems: 3 }, manual_instructions: { type: 'array', items: { type: 'string', minLength: 1, maxLength: 300 }, minItems: 1, maxItems: 2 }, validation_steps: { type: 'array', items: { type: 'string', minLength: 1, maxLength: 300 }, minItems: 1, maxItems: 2 }, rollback_steps: { type: 'array', items: { type: 'string', minLength: 1, maxLength: 300 }, minItems: 1, maxItems: 2 },
        } } },
    },
  };
}

export function validateAiCandidate(candidate, allowedRefs, domain) {
  const reasons = [];
  const textIsClean = (value, maximum) => {
    if (typeof value !== 'string') return false;
    const text = value.trim();
    return Boolean(text)
      && text.length <= maximum
      && !/[\r\n]/.test(text)
      && !/```|[}\]]{4,}|(?:^|\s)(?:here is|below is) the (?:full|corrected) (?:json|response)/i.test(text);
  };
  if (!/^AI-[A-Z0-9-]{2,40}$/.test(candidate?.rule_id || '')) reasons.push('invalid_rule_id');
  if (!['critical', 'warning', 'advisory'].includes(candidate?.severity)) reasons.push('invalid_severity');
  const confidence = Number(candidate?.confidence);
  if (!Number.isFinite(confidence) || confidence < 0.65 || confidence > 1) reasons.push('confidence_out_of_range');
  if (!Array.isArray(candidate?.evidence_refs) || candidate.evidence_refs.length === 0) reasons.push('missing_evidence_refs');
  else if (!candidate.evidence_refs.every((ref) => allowedRefs.has(ref))) reasons.push('unsupported_evidence_ref');
  if (domain === 'n8n_workflows' && candidate?.severity === 'critical') {
    const refs = Array.isArray(candidate?.evidence_refs) ? candidate.evidence_refs : [];
    const hasConcreteWorkflowEvidence = refs.some((ref) => /^(node|execution):/.test(ref));
    if (!hasConcreteWorkflowEvidence) reasons.push('critical_workflow_requires_node_or_execution_evidence');
  }
  for (const key of ['manual_instructions', 'validation_steps', 'rollback_steps']) {
    if (!Array.isArray(candidate?.[key]) || candidate[key].length === 0 || candidate[key].length > 2 || !candidate[key].every((entry) => textIsClean(entry, 300))) reasons.push(`invalid_${key}`);
  }
  for (const [key, maximum] of [['subject', 160], ['title', 160], ['charter_alignment', 300], ['why_it_matters', 300]]) {
    if (!textIsClean(candidate?.[key], maximum)) reasons.push(`invalid_${key}`);
  }
  return [...new Set(reasons)];
}

async function callLocalModel(domain, evidence, config, allowedRefs) {
  const configuredMaximum = Math.min(config.limits.maximum_ai_findings_per_domain, 3);
  const attempt = async (maximumFindings) => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), config.local_ai.request_timeout_seconds * 1000);
    const schema = modelResponseSchema(maximumFindings);
    const prompt = `You are the local advisory reviewer for the owner's AI second brain. Review only the supplied sanitized ${domain} evidence. Return at most ${maximumFindings} concise, high-value finding${maximumFindings === 1 ? '' : 's'}, and return an empty findings array when no materially new improvement is supported. Every recommendation must support the charter goal of turning ongoing projects into completed projects with clear, concise information. Do not claim an edit was applied. Do not invent paths, workflow IDs, node IDs, evidence, or facts. Return only schema-valid JSON. Every rule_id must match AI-[A-Z0-9-] and begin with AI-. Confidence must be at least 0.65. Each evidence_refs entry must be copied exactly from an evidence_id or evidence_refs value present in the supplied evidence. Use no more than two short strings in each instruction, validation, or rollback array. Keep every explanatory or step string below 300 characters. Do not repeat deterministic findings unless you add a materially distinct improvement. Critical severity is reserved for an active or imminent, directly evidenced risk of data loss, corruption, privacy/security breach, or system halt. For n8n_workflows, a critical finding must cite at least one node: or execution: evidence reference that directly supports the claim; a workflow: reference alone is insufficient. Do not describe deterministic extractors such as pdftotext, pandoc, or tesseract as AI. Do not infer data leakage merely from use of a local model or private-LAN endpoint. If the evidence does not directly establish the causal claim, use warning or advisory, or return no finding.`;
    try {
      const response = await fetch(`${config.local_ai.base_url.replace(/\/$/, '')}/api/chat`, {
        method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: config.local_ai.model, stream: false, think: false, format: schema, options: { temperature: config.local_ai.temperature, num_predict: 4096 }, messages: [{ role: 'system', content: prompt }, { role: 'user', content: JSON.stringify(evidence) }] }),
      });
      if (!response.ok) fail(`Local model returned HTTP ${response.status}.`);
      const envelope = await response.json();
      let raw = String(envelope.message?.content || envelope.response || '').trim();
      raw = raw.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
      let parsed;
      try { parsed = JSON.parse(raw); }
      catch (error) {
        return { retryable_failure: envelope.done_reason === 'length' ? 'output_limit' : 'invalid_json', response_characters: raw.length, done_reason: envelope.done_reason || 'unknown', parse_message: String(error.message).slice(0, 200) };
      }
      if (!Array.isArray(parsed.findings)) return { retryable_failure: 'missing_findings_array', response_characters: raw.length, done_reason: envelope.done_reason || 'unknown' };
      const accepted = [];
      const rejected = [];
      for (const candidate of parsed.findings.slice(0, maximumFindings)) {
        const reasonCodes = validateAiCandidate(candidate, allowedRefs, domain);
        if (reasonCodes.length) { rejected.push({ reason: 'candidate_validation_failed', reason_codes: reasonCodes, candidate_hash: sha256(canonicalStringify(candidate)) }); continue; }
        accepted.push(finding(candidate.rule_id, candidate.severity, candidate.subject, candidate.evidence_refs.slice().sort().join('|'), { ...candidate, source: `local_ai:${domain}` }));
      }
      return { accepted, rejected, raw_response_retained: false, compact_retry_used: false };
    } finally { clearTimeout(timeout); }
  };

  const primary = await attempt(configuredMaximum);
  if (!primary.retryable_failure) return primary;
  const compact = await attempt(1);
  if (!compact.retryable_failure) return { ...compact, compact_retry_used: true, primary_failure: primary.retryable_failure };
  fail(`Local model ${domain} could not complete bounded JSON after its compact retry (primary=${primary.retryable_failure}; retry=${compact.retryable_failure}; retry_done_reason=${compact.done_reason}).`);
}

async function runLocalAi(snapshot, deterministic, config, guard) {
  if (!config.local_ai.enabled) return { status: 'disabled', findings: [], rejected: [], domains_completed: [], deferred_reasons: [] };
  if (guard.defer) return { status: 'deferred', findings: [], rejected: [], domains_completed: [], deferred_reasons: guard.reasons };
  const findings = [];
  const rejected = [];
  const completed = [];
  const errors = [];
  for (const domain of config.local_ai.domains) {
    try {
      const evidence = boundedDomainEvidence(domain, snapshot, deterministic, config);
      const allowedRefs = allowedEvidenceRefsFromDomain(evidence);
      const result = await callLocalModel(domain, evidence, config, allowedRefs);
      findings.push(...result.accepted);
      rejected.push(...result.rejected.map((item) => ({ domain, ...item })));
      completed.push(domain);
    } catch (error) { errors.push({ domain, message: String(error.message || error).slice(0, 500) }); }
  }
  return { status: errors.length ? (completed.length ? 'partial' : 'failed') : 'completed', findings, rejected, domains_completed: completed, errors, deferred_reasons: [] };
}

function suppressAiDuplicates(deterministic, localAi) {
  const key = (item) => canonicalStringify({
    subject: String(item.subject || "").trim(),
    title: String(item.title || "").trim().toLowerCase(),
    evidence_refs: [...(item.evidence_refs || [])].map(String).sort(),
  });
  const deterministicKeys = new Set(deterministic.map(key));
  const kept = [];
  const suppressed = [];
  for (const item of localAi.findings || []) {
    if (!deterministicKeys.has(key(item))) {
      kept.push(item);
      continue;
    }
    suppressed.push({
      domain: String(item.source || "").replace(/^local_ai:/, "") || "unknown",
      reason: "duplicate_of_deterministic",
      reason_codes: ["same_subject_title_evidence"],
      candidate_hash: sha256(canonicalStringify(item)),
    });
  }
  localAi.findings = kept;
  localAi.rejected = [...(localAi.rejected || []), ...suppressed];
  return localAi;
}

function mergeFindings(deterministic, ai, maximum) {
  const byId = new Map();
  for (const item of [...deterministic, ...ai]) if (!byId.has(item.finding_id)) byId.set(item.finding_id, item);
  return [...byId.values()].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || a.finding_id.localeCompare(b.finding_id)).slice(0, maximum);
}

function reviewPackage(snapshot, audit) {
  return {
    schema_version: SCHEMA_VERSION,
    package_type: 'portable_external_review',
    generated_at: audit.completed_at,
    exclusions: ['credential values', 'ordinary note bodies', 'execution payloads', 'original recordings and documents', 'transcripts and extracted text', 'automatic fixes'],
    privacy: snapshot.privacy,
    charter_hash: snapshot.run.charter_hash,
    configuration_hash: snapshot.run.config_hash,
    snapshot_id: snapshot.run.run_id,
    active_workflows: snapshot.active_workflows,
    execution_metadata: snapshot.execution_metadata,
    filesystem: snapshot.filesystem,
    collection_errors: snapshot.collection_errors,
    findings: audit.findings.map(({ decision_raw, ...item }) => item),
    evidence_gaps: audit.evidence_gaps,
    questions_for_external_reviewer: [
      'Which findings present the highest risk to reliable voice-first ingest?',
      'Which manual improvement would most directly help complete an ongoing project?',
      'Are any instructions unsupported by the included evidence?',
      'What important risk is visible in the evidence but missing from the findings?',
    ],
  };
}

async function sendNtfy(config, title, message, priority = 'high') {
  const response = await fetch(`${config.notifications.base_url.replace(/\/$/, '')}/${encodeURIComponent(config.notifications.topic)}`, { method: 'POST', headers: { Title: title, Priority: priority, Tags: priority === 'urgent' ? 'rotating_light' : 'mag' }, body: message });
  if (!response.ok) fail(`ntfy returned HTTP ${response.status}.`);
}

function parseArguments(argv) {
  const args = { command: argv[2] || '--help', skipAi: false, skipNotify: false };
  for (let index = 3; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--snapshot-file') args.snapshotFile = argv[++index];
    else if (value === '--config-file') args.configFile = argv[++index];
    else if (value === '--config-base64') args.configBase64 = argv[++index];
    else if (value === '--skip-ai') args.skipAi = true;
    else if (value === '--skip-notify') args.skipNotify = true;
    else fail(`Unknown argument: ${value}`);
  }
  return args;
}

function configFromArguments(args) {
  if (args.configBase64) return validateConfig(parseJson(Buffer.from(args.configBase64, 'base64').toString('utf8'), 'Base64 configuration'));
  if (!args.configFile) fail('Use --config-file or --config-base64.');
  return validateConfig(readJson(args.configFile, 'Production configuration'));
}

async function auditCommand(args) {
  if (!args.snapshotFile) fail('Use --snapshot-file with an accepted collection snapshot.');
  const config = configFromArguments(args);
  const snapshotFile = path.resolve(args.snapshotFile);
  if (!isInside(snapshotFile, config.paths.snapshot_root)) fail('Snapshot escaped the approved snapshot root.');
  const snapshot = validateSnapshot(readJson(snapshotFile, 'Current snapshot'));
  const previous = findPreviousSnapshot(snapshotFile, snapshot);
  const now = new Date();
  const deterministic = collectDeterministicFindings(snapshot, previous, config, now);
  const guard = resourceGuard(snapshot, config);
  const localAi = args.skipAi ? { status: 'disabled_for_acceptance', findings: [], rejected: [], domains_completed: [], deferred_reasons: [] } : await runLocalAi(snapshot, deterministic.findings, config, guard);
  const existingReport = readOptional(config.paths.report) || '';
  const ownerDecisionRegistry = loadOwnerDecisionRegistry(config);
  const persistentDecisions = persistentDecisionMap(ownerDecisionRegistry);
  const existingDecisionIds = new Set([...parseDecisionBlocks(existingReport).keys(), ...persistentDecisions.keys()]);
  suppressAiDuplicates(deterministic.findings, localAi);
  const merged = mergeFindings(deterministic.findings, localAi.findings, config.limits.maximum_findings);
  const reconciled = reconcileFindings(merged, existingReport, persistentDecisions);
  const deletionRequests = buildDeletionRequests(deterministic.tempCandidates, config, now);
  const critical = reconciled.filter((item) => item.severity === 'critical' && item.status === 'awaiting_decision');
  const newActionable = reconciled.filter((item) => item.status === 'awaiting_decision' && !existingDecisionIds.has(item.finding_id));
  const newCritical = newActionable.filter((item) => item.severity === 'critical');
  const audit = {
    schema_version: SCHEMA_VERSION,
    auditor_version: AUDITOR_VERSION,
    mode: 'production_advisory',
    run_id: `AUDIT-${snapshot.run.run_id}`,
    snapshot_id: snapshot.run.run_id,
    snapshot_evidence_hash: snapshot.evidence_hash,
    started_at: now.toISOString(),
    completed_at: new Date().toISOString(),
    window_start: snapshot.run.window_start,
    window_end: snapshot.run.window_end,
    charter_hash: snapshot.run.charter_hash,
    config_hash: sha256(canonicalStringify(config)),
    overall_status: critical.length ? 'critical' : reconciled.some((item) => item.severity === 'warning') ? 'warning' : 'healthy',
    resource_guard: guard,
    local_ai: localAi,
    findings: reconciled,
    deletion_requests: deletionRequests.map((request) => ({ request_id: request.request_id, candidate_count: request.candidates.length })),
    evidence_gaps: [
      ...(snapshot.collection_errors.length ? [`${snapshot.collection_errors.length} collection error(s) were present.`] : []),
      ...(localAi.status === 'failed' || localAi.status === 'partial' ? ['One or more local-AI review domains failed; deterministic findings remain valid.'] : []),
      ...(localAi.status === 'deferred' ? [`Local AI deferred: ${localAi.deferred_reasons.join('; ')}`] : []),
      ...(snapshot.execution_metadata?.count === 0 ? ['No completed active-workflow execution metadata occurred in this incremental window.'] : []),
    ],
    assertions: {
      automatic_source_fixes_applied: false,
      automatic_deletions_performed: false,
      credential_values_collected: false,
      ordinary_note_bodies_collected: false,
      execution_payloads_collected: false,
      local_analysis_only: true,
    },
  };
  assertNoSecret(audit, 'Audit result');
  const report = renderReport(audit, existingReport);
  if (Buffer.byteLength(report) > config.limits.maximum_report_bytes) fail('Rendered report exceeds its safety limit.');
  assertNoSecret(report, 'Rendered report');
  const updatedOwnerDecisionRegistry = buildOwnerDecisionRegistry(ownerDecisionRegistry, existingReport, reconciled, audit.completed_at);
  assertNoSecret(updatedOwnerDecisionRegistry, 'Updated owner decision registry');
  const portable = reviewPackage(snapshot, audit);
  assertNoSecret(portable, 'Portable review package');
  const portableText = `${JSON.stringify(portable, null, 2)}\n`;
  if (Buffer.byteLength(portableText) > config.limits.maximum_review_package_bytes) fail('Portable review package exceeds its safety limit.');

  const deletionPublications = deletionRequests.map((request) => {
    const file = path.join(config.paths.deletion_requests, `${request.request_id}.md`);
    const note = renderDeletionRequest(request, readOptional(file) || '');
    assertNoSecret(note, `Deletion request ${request.request_id}`);
    return { file, note };
  });
  let homepagePlan = null;
  if (config.publication.install_homepage_summary) {
    const current = readOptional(config.paths.homepage);
    if (current === null) fail(`Homepage is missing: ${config.paths.homepage}`);
    const updated = installHomepageBlock(current);
    assertNoSecret(updated, 'Homepage with deletion summary');
    homepagePlan = { current, updated };
  }

  const auditFile = path.join(config.paths.audit_root, `${audit.run_id}.audit.json`);
  const packageFile = path.join(config.paths.export_root, `${audit.run_id}.portable-review.json`);
  if (config.publication.write_audit_record) atomicWriteJson(auditFile, audit);
  if (config.publication.write_report) atomicWriteText(config.paths.report, report);
  atomicWriteJson(ownerDecisionRegistryPath(config), updatedOwnerDecisionRegistry);
  const deletionFiles = [];
  if (config.publication.write_deletion_requests) for (const publication of deletionPublications) {
    atomicWriteText(publication.file, publication.note);
    deletionFiles.push(publication.file);
  }
  let homepageChanged = false;
  if (homepagePlan) {
    if (homepagePlan.updated !== homepagePlan.current) {
      const homepageBackup = path.join(config.paths.state_root, 'Owner File Backups', `${snapshot.run.run_id}.Home.md`);
      atomicWriteText(homepageBackup, homepagePlan.current);
      atomicWriteText(config.paths.homepage, homepagePlan.updated);
      homepageChanged = true;
    }
  }
  if (config.publication.write_portable_review_package) atomicWriteText(packageFile, portableText);
  const successState = { schema_version: SCHEMA_VERSION, auditor_version: AUDITOR_VERSION, last_success: audit.completed_at, run_id: audit.run_id, snapshot_id: audit.snapshot_id, audit_file: auditFile, report_file: config.paths.report, portable_review_file: packageFile, overall_status: audit.overall_status, finding_count: audit.findings.length, critical_count: critical.length, local_ai_status: localAi.status, notification_status: 'not_requested' };
  atomicWriteJson(path.join(config.paths.state_root, 'production-last-success.json'), successState);
  atomicWriteJson(path.join(config.paths.state_root, 'production-heartbeat.json'), { ...successState, current_phase: 'succeeded', report_published: config.publication.write_report, snapshot_published: true, schedule_mode: config.schedule_mode });
  atomicWriteText(path.join(config.paths.log_root, `${audit.run_id}.log.json`), `${JSON.stringify({ run_id: audit.run_id, completed_at: audit.completed_at, overall_status: audit.overall_status, finding_count: audit.findings.length, critical_count: critical.length, local_ai_status: localAi.status }, null, 2)}\n`);

  let notificationStatus = 'not_requested';
  let externalNetwork = false;
  if (!args.skipNotify && config.notifications.enabled && ((config.notifications.notify_on_critical && newCritical.length) || (config.notifications.notify_on_new_actionable_findings && newActionable.length))) {
    try {
      externalNetwork = true;
      await sendNtfy(config, newCritical.length ? 'Second-Brain Auditor: New Critical findings' : 'Second-Brain Auditor: New review items', `${newCritical.length} new Critical; ${newActionable.length} new actionable; ${reconciled.length} total finding(s). Review: 90 System/Second-Brain Audit Review.md`, newCritical.length ? 'urgent' : 'high');
      notificationStatus = 'sent';
    } catch (error) { notificationStatus = `failed:${String(error.message || error).slice(0, 160)}`; }
  }
  successState.notification_status = notificationStatus;
  atomicWriteJson(path.join(config.paths.state_root, 'production-last-success.json'), successState);
  atomicWriteJson(path.join(config.paths.state_root, 'production-heartbeat.json'), { ...successState, current_phase: 'succeeded', report_published: config.publication.write_report, snapshot_published: true, schedule_mode: config.schedule_mode });

  return {
    schema_version: SCHEMA_VERSION,
    auditor_version: AUDITOR_VERSION,
    mode: 'production_advisory',
    status: 'completed',
    run_id: audit.run_id,
    snapshot_id: audit.snapshot_id,
    overall_status: audit.overall_status,
    finding_count: audit.findings.length,
    critical_count: critical.length,
    warning_count: audit.findings.filter((item) => item.severity === 'warning').length,
    advisory_count: audit.findings.filter((item) => item.severity === 'advisory').length,
    awaiting_decision_count: audit.findings.filter((item) => item.status === 'awaiting_decision').length,
    accepted_finding_count: audit.findings.filter((item) => item.status === 'accepted').length,
    manual_follow_up_count: audit.findings.filter((item) => item.status === 'manual_follow_up').length,
    owner_decision_registry_path: ownerDecisionRegistryPath(config),
    local_ai_status: localAi.status,
    local_ai_findings_accepted: localAi.findings.length,
    local_ai_findings_rejected: localAi.rejected.length,
    deletion_request_count: deletionRequests.length,
    report_path: config.paths.report,
    portable_review_path: packageFile,
    audit_record_path: auditFile,
    homepage_block_changed: homepageChanged,
    notification_status: notificationStatus,
    external_network_calls_performed: externalNetwork,
    automatic_source_fixes_applied: false,
    automatic_deletions_performed: false,
  };
}

function help() {
  return `Second-Brain Production Auditor ${AUDITOR_VERSION}\n\nCommands:\n  audit --snapshot-file PATH --config-file PATH [--skip-ai] [--skip-notify]\n  audit --snapshot-file PATH --config-base64 BASE64 [--skip-ai] [--skip-notify]\n  --help\n`;
}

async function main() {
  const args = parseArguments(process.argv);
  if (args.command === '--help' || args.command === 'help') { process.stdout.write(help()); return; }
  if (args.command !== 'audit') fail(`Unknown command: ${args.command}`);
  const result = await auditCommand(args);
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

const invoked = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));
if (invoked) main().catch((error) => { process.stderr.write(`${error.stack || error.message || error}\n`); process.exitCode = 1; });
