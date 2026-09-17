#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const REVIEW_VERSION = '1.0.0';

function nowIso() {
  return new Date().toISOString();
}

function runId() {
  return nowIso().replace(/[-:.]/g, '').replace('Z', 'Z-') +
    crypto.randomBytes(4).toString('hex');
}

function fail(message) {
  throw new Error(message);
}

function ensureDir(directory) {
  fs.mkdirSync(directory, { recursive: true });
}

function atomicWriteText(target, text) {
  ensureDir(path.dirname(target));
  const temporary = path.join(
    path.dirname(target),
    `.${path.basename(target)}.${process.pid}.${Date.now()}.tmp`
  );
  const descriptor = fs.openSync(temporary, 'wx', 0o664);
  try {
    fs.writeFileSync(descriptor, text, 'utf8');
    fs.fsyncSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
  fs.renameSync(temporary, target);
}

function atomicWriteJson(target, value) {
  atomicWriteText(target, JSON.stringify(value, null, 2) + '\n');
}

function readJson(target, fallback = null) {
  if (!fs.existsSync(target)) return fallback;
  return JSON.parse(fs.readFileSync(target, 'utf8'));
}

function sha256Text(text) {
  return crypto.createHash('sha256').update(text, 'utf8').digest('hex');
}

function acquireLock(lockDir, staleMinutes) {
  ensureDir(path.dirname(lockDir));
  try {
    fs.mkdirSync(lockDir);
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const ageMinutes = (Date.now() - fs.statSync(lockDir).mtimeMs) / 60000;
    if (ageMinutes < staleMinutes) return false;
    fs.rmSync(lockDir, { recursive: true, force: true });
    fs.mkdirSync(lockDir);
  }
  atomicWriteJson(path.join(lockDir, 'owner.json'), {
    pid: process.pid,
    acquired_at: nowIso(),
    review_version: REVIEW_VERSION,
  });
  return true;
}

function releaseLock(lockDir) {
  fs.rmSync(lockDir, { recursive: true, force: true });
}

function appendEvent(config, event) {
  ensureDir(config.paths.logs);
  fs.appendFileSync(
    path.join(config.paths.logs, 'camera-photo-review-events.jsonl'),
    JSON.stringify(event) + '\n',
    'utf8'
  );
}

function workflowArguments(argv) {
  const output = {
    command: argv[2] || 'build',
    configPath: '',
    maxBatch: null,
  };
  for (let index = 3; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--config') output.configPath = argv[++index] || '';
    else if (argument === '--max-batch') output.maxBatch = Number(argv[++index]);
    else fail(`Unknown argument: ${argument}`);
  }
  const encoded = process.env.CAMERA_REVIEW_PAYLOAD_B64 || '';
  if (encoded) {
    const payload = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
    output.configPath = String(payload.configPath || output.configPath);
    if (payload.maxBatchItems !== undefined) {
      output.maxBatch = Number(payload.maxBatchItems);
    }
    if (payload.operationMode) output.command = String(payload.operationMode);
  }
  if (!output.configPath) {
    fail('Use --config PATH or provide CAMERA_REVIEW_PAYLOAD_B64.');
  }
  return output;
}

function validateConfig(config) {
  if (config.schema_version !== '1.0.0') {
    fail(`Unsupported configuration schema: ${config.schema_version}`);
  }
  for (const key of [
    'pending_notes', 'approved_documents', 'transcription_only_documents',
    'review_archive', 'queue',
    'state', 'logs', 'review_lock',
  ]) {
    if (!path.isAbsolute(String(config.paths?.[key] || ''))) {
      fail(`Configuration path ${key} must be absolute.`);
    }
  }
  if (process.env.CAMERA_PHOTO_TEST_MODE !== '1') {
    for (const key of [
      'pending_notes', 'approved_documents', 'transcription_only_documents',
      'review_archive', 'queue',
    ]) {
      if (!config.paths[key].startsWith('/Obsidian/')) {
        fail(`Unsafe Obsidian path ${key}: ${config.paths[key]}`);
      }
    }
    for (const key of ['state', 'logs', 'review_lock']) {
      if (!config.paths[key].startsWith('/Capture/Documents')) {
        fail(`Unsafe capture path ${key}: ${config.paths[key]}`);
      }
    }
  }
  return config;
}

function listMarkdown(root) {
  if (!fs.existsSync(root)) return [];
  const output = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      if (entry.name.startsWith('.')) continue;
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(fullPath);
      else if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) {
        const stat = fs.statSync(fullPath);
        output.push({ path: fullPath, name: entry.name, mtimeMs: stat.mtimeMs });
      }
    }
  }
  return output.sort((left, right) =>
    left.mtimeMs - right.mtimeMs || left.path.localeCompare(right.path)
  );
}

function frontmatterValue(text, key) {
  if (!text.startsWith('---')) return '';
  const end = text.indexOf('\n---', 3);
  if (end < 0) return '';
  const block = text.slice(3, end);
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = block.match(new RegExp(`^${escaped}:\\s*(.*?)\\s*$`, 'mi'));
  if (!match) return '';
  const raw = match[1].trim();
  try {
    return JSON.parse(raw);
  } catch (_) {
    return raw.replace(/^["']|["']$/g, '');
  }
}

function titleFrom(text, filename) {
  return String(
    frontmatterValue(text, 'title') ||
    text.match(/^#\s+(.+)$/m)?.[1] ||
    filename.replace(/\.md$/i, '')
  ).trim();
}

function section(text, heading) {
  const escaped = heading.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = text.match(
    new RegExp(`^## ${escaped}\\s*$([\\s\\S]*?)(?=^## |\\z)`, 'm')
  );
  if (match) return match[1].trim();
  const start = text.search(new RegExp(`^## ${escaped}\\s*$`, 'm'));
  if (start < 0) return '';
  const after = text.slice(start).replace(new RegExp(`^## ${escaped}\\s*$`, 'm'), '');
  const next = after.search(/^## /m);
  return (next < 0 ? after : after.slice(0, next)).trim();
}

function vaultLink(target) {
  const normalized = String(target || '').replace(/\\/g, '/');
  return normalized.startsWith('/Obsidian/')
    ? normalized.slice('/Obsidian/'.length).replace(/\.md$/i, '')
    : normalized.replace(/^\/+/, '').replace(/\.md$/i, '');
}

function escapeHeading(value) {
  return String(value || '').replace(/[\r\n]+/g, ' ').trim();
}

function parseExistingDecisions(queueText) {
  const decisions = new Map();
  const pattern = /<!-- photo-capture-review-item:start sha=([a-f0-9]{64}) -->\n([\s\S]*?)<!-- photo-capture-review-item:end sha=\1 -->/gi;
  let match;
  while ((match = pattern.exec(queueText)) !== null) {
    const checked = [];
    for (const line of match[2].split('\n')) {
      const choice = line.match(/^- \[[xX]\]\s+(.+?)\s*$/);
      if (choice) checked.push(choice[1].trim());
    }
    decisions.set(match[1].toLowerCase(), checked);
  }
  return decisions;
}

function checked(existing, label) {
  return existing.includes(label) ? 'x' : ' ';
}

function buildQueue(config) {
  ensureDir(config.paths.pending_notes);
  ensureDir(path.dirname(config.paths.queue));
  ensureDir(config.paths.logs);

  function boolish(value) {
    if (value === true || value === false) return value;
    const normalized = String(value ?? '').trim().toLowerCase();
    if (normalized === 'true') return true;
    if (normalized === 'false') return false;
    return null;
  }

  function callout(type, title, body, collapsed = false) {
    const cleaned = String(body || '').trim();
    if (!cleaned) return [];
    const marker = collapsed ? '-' : '';
    const lines = [
      `> [!${type}]${marker} ${title}`,
      ...cleaned.split('\n').map((line) => `> ${line}`),
      '',
    ];
    return lines;
  }

  function reviewSuggestion(note) {
    const useful = boolish(note.useful);
    const recapture = boolish(note.recapture);
    const legibility = String(note.legibility || '').toLowerCase();

    if (recapture === true || ['poor', 'unreadable'].includes(legibility)) {
      return {
        key: 'retake',
        label: 'Retake likely',
        detail: 'The capture quality suggests checking whether a clearer photo would be better before approving.',
      };
    }
    if (useful === false) {
      return {
        key: 'ignore',
        label: 'Ignore/archive likely',
        detail: 'The extraction marked this capture as not containing useful content. Verify the preview before ignoring it.',
      };
    }
    return {
      key: 'approve',
      label: 'Ready to review',
      detail: 'Check the preview and transcription. If they are accurate, approve the extraction.',
    };
  }

  if (!acquireLock(
    config.paths.review_lock,
    Number(config.review.stale_lock_minutes || 30)
  )) {
    return { result: 'queue_locked', queue_path: config.paths.queue };
  }

  try {
    const currentRunId = runId();
    const generatedAt = nowIso();
    const existingText = fs.existsSync(config.paths.queue)
      ? fs.readFileSync(config.paths.queue, 'utf8')
      : '';
    const existingDecisions = parseExistingDecisions(existingText);

    const notes = listMarkdown(config.paths.pending_notes).map((item) => {
      const text = fs.readFileSync(item.path, 'utf8');
      const hash = String(
        frontmatterValue(text, 'source_sha256') || sha256Text(text)
      ).toLowerCase();

      const note = {
        ...item,
        text,
        hash,
        title: titleFrom(text, item.name),
        captureType: frontmatterValue(text, 'capture_type') || 'unknown',
        useful: frontmatterValue(text, 'photo_useful_content'),
        recapture: frontmatterValue(text, 'photo_recapture_recommended'),
        recaptureReason: frontmatterValue(text, 'photo_recapture_reason') || '',
        legibility: frontmatterValue(text, 'photo_quality_legibility') || 'unknown',
        preview: frontmatterValue(text, 'source_preview_vault_path'),
        summary: section(text, 'Capture Summary'),
        transcription: section(text, 'Verbatim Transcription'),
        cleaned: section(text, 'Cleaned Interpretation'),
        calendar: section(text, 'Suggested Calendar Entries'),
        tasks: section(text, 'Suggested Tasks'),
        people: section(text, 'People'),
        projects: section(text, 'Projects'),
        uncertainties: section(text, 'Uncertainties'),
      };

      note.suggestion = reviewSuggestion(note);
      return note;
    });

    const suggestionCounts = {
      approve: notes.filter((note) => note.suggestion.key === 'approve').length,
      retake: notes.filter((note) => note.suggestion.key === 'retake').length,
      ignore: notes.filter((note) => note.suggestion.key === 'ignore').length,
    };

    const lines = [
      '---',
      'title: "Photo Capture Review"',
      'type: "system-queue"',
      `updated_at: "${generatedAt}"`,
      `pending_count: ${notes.length}`,
      `queue_run_id: "${currentRunId}"`,
      'layout: "mobile-review"',
      '---',
      '',
      '# Photo Capture Review',
      '',
      '> [!summary] Review dashboard',
      `> **${notes.length} photo${notes.length === 1 ? '' : 's'} awaiting your decision.**`,
      '>',
      '> For each item: look at the image, read the summary, edit the linked note if needed, then check exactly one decision.',
      '> Your checked decision is applied automatically by the existing Photo Capture Review workflow. Tasks, calendar entries, people, projects, and filing suggestions remain advisory until you approve the appropriate path.',
      '',
      '## At a glance',
      '',
      `- **Ready to review:** ${suggestionCounts.approve}`,
      `- **Retake likely:** ${suggestionCounts.retake}`,
      `- **Ignore/archive likely:** ${suggestionCounts.ignore}`,
      `- **Last refreshed:** ${generatedAt}`,
      '',
      '> [!tip] Fastest phone workflow',
      '> 1. Check the image.',
      '> 2. Read **Quick summary**.',
      '> 3. Tap **Edit this capture** only if something needs correction.',
      '> 4. Check exactly one decision.',
      '> 5. Move to the next photo. The apply workflow runs automatically every 15 minutes.',
      '',
    ];

    if (!notes.length) {
      lines.push(
        '> [!success] All caught up',
        '> No camera photos are waiting for owner review.',
        ''
      );
    }

    notes.forEach((note, index) => {
      const existing = existingDecisions.get(note.hash) || [];
      const preview = note.preview
        ? `![[${vaultLink(note.preview)}]]`
        : '> [!warning] Preview unavailable\n> Open the editable note to inspect the capture details.';

      const summaryText = note.summary || 'No dependable summary was produced.';
      const qualityLines = [
        `- **Capture type:** ${note.captureType}`,
        `- **Legibility:** ${note.legibility}`,
        `- **Useful content:** ${note.useful}`,
        `- **Recapture recommended:** ${note.recapture}`,
      ];
      if (note.recaptureReason) {
        qualityLines.push(`- **Recapture reason:** ${note.recaptureReason}`);
      }

      lines.push(
        `<!-- photo-capture-review-item:start sha=${note.hash} -->`,
        `## ${index + 1}. ${escapeHeading(note.title)}`,
        '',
        `> [!info] Suggested review path — ${note.suggestion.label}`,
        `> ${note.suggestion.detail}`,
        '',
        '### Preview',
        '',
        preview,
        '',
        '### Quick summary',
        '',
        summaryText,
        '',
        '### Quality',
        '',
        ...qualityLines,
        '',
        `**✏️ Edit this capture:** [[${vaultLink(note.path)}|Open editable note]]`,
        '',
        ...callout(
          'quote',
          'Verbatim transcription',
          note.transcription || '[No dependable transcription.]',
          true
        ),
        ...callout(
          'note',
          'Cleaned interpretation',
          note.cleaned || '[No cleaned interpretation.]',
          true
        ),
        ...callout(
          'todo',
          'Suggested tasks',
          note.tasks || 'None suggested.',
          true
        ),
        ...callout(
          'calendar',
          'Suggested calendar entries',
          note.calendar || 'None suggested.',
          true
        ),
        ...callout(
          'users',
          'People / projects',
          [
            note.people ? `**People**\n${note.people}` : '',
            note.projects ? `**Projects**\n${note.projects}` : '',
          ].filter(Boolean).join('\n\n') || 'None identified.',
          true
        ),
        ...callout(
          'warning',
          'Uncertainties',
          note.uncertainties || 'None reported.',
          true
        ),
        '### Decision',
        '',
        '> [!important] Choose exactly one',
        '> If the transcription or interpretation needs correction, use **Edit this capture** above first. Leave **Keep pending** checked only when you want to defer the item.',
        '',
        `- [${checked(existing, 'Approve extraction')}] Approve extraction`,
        `- [${checked(existing, 'Approve transcription only')}] Approve transcription only`,
        `- [${checked(existing, 'Needs retake')}] Needs retake`,
        `- [${checked(existing, 'Ignore and archive')}] Ignore and archive`,
        `- [${checked(existing, 'Keep pending')}] Keep pending`,
        '',
        `- **Source:** \`${note.hash.slice(0, 12)}…\``,
        '',
        `<!-- photo-capture-review-item:end sha=${note.hash} -->`,
        '',
        '---',
        '',
      );
    });

    atomicWriteText(config.paths.queue, lines.join('\n'));

    appendEvent(config, {
      event: 'queue_refreshed',
      at: generatedAt,
      run_id: currentRunId,
      queue_path: config.paths.queue,
      pending_count: notes.length,
      preserved_decision_count: [...existingDecisions.values()]
        .filter((choices) => choices.length).length,
      queue_layout: 'mobile-review-v2',
      suggestion_counts: suggestionCounts,
    });

    return {
      result: 'queue_refreshed',
      run_id: currentRunId,
      queue_path: config.paths.queue,
      pending_count: notes.length,
      queue_layout: 'mobile-review-v2',
      suggestion_counts: suggestionCounts,
    };
  } finally {
    releaseLock(config.paths.review_lock);
  }
}


function parseQueueItems(text) {
  const output = [];
  const pattern = /<!-- photo-capture-review-item:start sha=([a-f0-9]{64}) -->\n([\s\S]*?)<!-- photo-capture-review-item:end sha=\1 -->/gi;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    const checkedLabels = [];
    for (const line of match[2].split('\n')) {
      const choice = line.match(/^- \[[xX]\]\s+(.+?)\s*$/);
      if (choice) checkedLabels.push(choice[1].trim());
    }
    output.push({
      full: match[0],
      hash: match[1].toLowerCase(),
      body: match[2],
      title: match[2].match(/^##\s+(.+)$/m)?.[1]?.trim() || match[1].slice(0, 12),
      checkedLabels,
    });
  }
  return output;
}

function decision(item) {
  if (!item.checkedLabels.length || item.checkedLabels[0] === 'Keep pending') {
    return { status: 'pending' };
  }
  if (item.checkedLabels.length !== 1) {
    return { status: 'invalid', error: 'Select exactly one photo-review choice.' };
  }
  const map = {
    'Approve extraction': 'approve_full',
    'Approve transcription only': 'approve_transcription',
    'Needs retake': 'needs_retake',
    'Ignore and archive': 'ignore',
  };
  const action = map[item.checkedLabels[0]];
  return action
    ? { status: 'approved', action }
    : { status: 'invalid', error: `Unknown choice: ${item.checkedLabels[0]}` };
}

function queueStatus(block, message) {
  const cleaned = block.replace(
    /\n> \[!warning\] Photo review status\n> .*?\n(?=\n|### Review decision)/s,
    '\n'
  );
  if (!message) return cleaned;
  return cleaned.replace(
    '\n### Review decision',
    `\n> [!warning] Photo review status\n> ${message}\n\n### Review decision`
  );
}

function findPending(config, hash) {
  for (const item of listMarkdown(config.paths.pending_notes)) {
    const text = fs.readFileSync(item.path, 'utf8');
    const sourceHash = String(
      frontmatterValue(text, 'source_sha256') || sha256Text(text)
    ).toLowerCase();
    if (sourceHash === hash) return { ...item, text };
  }
  return null;
}

function yamlValue(value) {
  if (typeof value === 'boolean' || typeof value === 'number') return String(value);
  return JSON.stringify(String(value));
}

function upsertFrontmatter(text, updates) {
  if (!text.startsWith('---')) fail('Photo note is missing YAML frontmatter.');
  const end = text.indexOf('\n---', 3);
  if (end < 0) fail('Photo note has unterminated YAML frontmatter.');
  let header = text.slice(0, end);
  const body = text.slice(end);
  for (const [key, value] of Object.entries(updates)) {
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const line = `${key}: ${yamlValue(value)}`;
    const pattern = new RegExp(`^${escaped}:.*$`, 'm');
    header = pattern.test(header)
      ? header.replace(pattern, line)
      : `${header}\n${line}`;
  }
  return header + body;
}

function replaceSection(text, heading, replacement) {
  const escaped = heading.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const headingPattern = new RegExp(`^## ${escaped}\\s*$`, 'm');
  const match = headingPattern.exec(text);
  if (!match) return text;
  const contentStart = match.index + match[0].length;
  const remainder = text.slice(contentStart);
  const nextHeadingOffset = remainder.search(/^## /m);
  const contentEnd = nextHeadingOffset < 0
    ? text.length
    : contentStart + nextHeadingOffset;
  return [
    text.slice(0, contentStart),
    '',
    replacement,
    '',
    text.slice(contentEnd).replace(/^\n+/, ''),
  ].join('\n');
}

function transcriptionOnly(text) {
  let output = text;
  output = replaceSection(
    output,
    'Suggested Calendar Entries',
    '> Owner approved transcription only. Calendar suggestions were not forwarded.'
  );
  output = replaceSection(
    output,
    'Suggested Tasks',
    '> Owner approved transcription only. Task suggestions were not forwarded.'
  );
  output = replaceSection(
    output,
    'People',
    '> Owner approved transcription only. People suggestions were not forwarded.'
  );
  output = replaceSection(
    output,
    'Projects',
    '> Owner approved transcription only. Project suggestions were not forwarded.'
  );
  return output;
}

function publishNote(source, destination, updated, sourceHash) {
  ensureDir(path.dirname(destination));
  if (fs.existsSync(destination)) {
    const existing = fs.readFileSync(destination, 'utf8');
    if (String(frontmatterValue(existing, 'source_sha256')).toLowerCase() !== sourceHash) {
      throw new Error(`Destination conflict: ${destination}`);
    }
  } else {
    atomicWriteText(destination, updated);
  }
  if (fs.existsSync(source)) fs.unlinkSync(source);
}

function applyDecisions(config, maximum) {
  ensureDir(config.paths.pending_notes);
  ensureDir(config.paths.approved_documents);
  ensureDir(config.paths.transcription_only_documents);
  ensureDir(config.paths.review_archive);
  ensureDir(config.paths.logs);
  if (!acquireLock(
    config.paths.review_lock,
    Number(config.review.stale_lock_minutes || 30)
  )) {
    return { result: 'queue_locked', queue_path: config.paths.queue };
  }
  try {
    if (!fs.existsSync(config.paths.queue)) {
      return { result: 'queue_missing', queue_path: config.paths.queue };
    }
    const currentRunId = runId();
    const startedAt = nowIso();
    let queueText = fs.readFileSync(config.paths.queue, 'utf8');
    const items = parseQueueItems(queueText);
    const state = readJson(config.paths.state, {
      state_version: 1,
      created_at: startedAt,
      updated_at: startedAt,
      files: {},
    });
    state.files = state.files || {};
    const processed = [];
    const invalid = [];
    let approvedSeen = 0;

    for (const item of items) {
      const selected = decision(item);
      if (selected.status === 'pending') {
        queueText = queueText.replace(item.full, queueStatus(item.full, ''));
        continue;
      }
      if (selected.status === 'invalid') {
        invalid.push({ source_sha256: item.hash, title: item.title, error: selected.error });
        queueText = queueText.replace(
          item.full,
          queueStatus(item.full, selected.error)
        );
        continue;
      }
      approvedSeen += 1;
      if (processed.length >= maximum) continue;
      const source = findPending(config, item.hash);
      if (!source) {
        const record = state.files[item.hash];
        if (record?.review?.completed === true && record.review.destination_path &&
            fs.existsSync(record.review.destination_path)) {
          processed.push({
            source_sha256: item.hash,
            result: 'already_complete',
            action: record.review.action,
            destination_path: record.review.destination_path,
          });
          queueText = queueText.replace(item.full, '');
          continue;
        }
        const error = 'The pending photo note could not be found.';
        invalid.push({ source_sha256: item.hash, title: item.title, error });
        queueText = queueText.replace(item.full, queueStatus(item.full, error));
        continue;
      }

      const completedAt = nowIso();
      let updated = source.text;
      let destination;
      if (selected.action === 'approve_full' ||
          selected.action === 'approve_transcription') {
        if (selected.action === 'approve_transcription') {
          updated = transcriptionOnly(updated);
        }
        updated = upsertFrontmatter(updated, {
          status: selected.action === 'approve_full'
            ? 'review' : 'needs_filing_review',
          photo_review_status: 'approved',
          photo_review_scope: selected.action === 'approve_full'
            ? 'full_extraction' : 'transcription_only',
          photo_review_approved_at: completedAt,
          photo_review_run_id: currentRunId,
          document_ai_review_status: selected.action === 'approve_full'
            ? 'pending' : 'not_requested',
          document_filing_status: selected.action === 'approve_full'
            ? 'pending' : 'needs_review',
          entity_action_mode: selected.action === 'approve_full'
            ? 'advisory_only' : 'transcription_only',
          suppress_action_extraction: selected.action === 'approve_transcription',
          automatic_task_creation: false,
          automatic_calendar_creation: false,
          automatic_entity_creation: false,
        });
        destination = path.join(
          selected.action === 'approve_full'
            ? config.paths.approved_documents
            : config.paths.transcription_only_documents,
          source.name
        );
      } else {
        const archiveFolder = selected.action === 'needs_retake'
          ? 'Needs Retake' : 'Ignored';
        updated = upsertFrontmatter(updated, {
          status: 'archived',
          photo_review_status: selected.action,
          photo_review_scope: 'not_forwarded',
          photo_review_approved_at: completedAt,
          photo_review_run_id: currentRunId,
          document_ai_review_status: 'not_requested',
          automatic_task_creation: false,
          automatic_calendar_creation: false,
          automatic_entity_creation: false,
        });
        destination = path.join(config.paths.review_archive, archiveFolder, source.name);
      }

      updated = updated.replace(/\s*$/, '\n') + [
        '',
        '<!-- photo-review-decision:start -->',
        '## Photo Review Decision Record',
        '',
        `- **Decision:** \`${selected.action}\``,
        `- **Completed at:** ${completedAt}`,
        `- **Run ID:** \`${currentRunId}\``,
        '- **Automatic task creation:** false',
        '- **Automatic calendar creation:** false',
        '- **Automatic entity creation:** false',
        '<!-- photo-review-decision:end -->',
        '',
      ].join('\n');

      publishNote(source.path, destination, updated, item.hash);
      state.files[item.hash] = {
        ...(state.files[item.hash] || {}),
        status: selected.action.startsWith('approve')
          ? 'approved_for_document_pipeline'
          : selected.action,
        review: {
          completed: true,
          completed_at: completedAt,
          run_id: currentRunId,
          action: selected.action,
          source_path: source.path,
          destination_path: destination,
          final_note_sha256: sha256Text(fs.readFileSync(destination, 'utf8')),
          automatic_changes_performed: false,
        },
      };
      state.updated_at = completedAt;
      atomicWriteJson(config.paths.state, state);
      appendEvent(config, {
        event: 'review_decision_applied',
        at: completedAt,
        run_id: currentRunId,
        source_sha256: item.hash,
        action: selected.action,
        source_path: source.path,
        destination_path: destination,
      });
      processed.push({
        source_sha256: item.hash,
        title: item.title,
        action: selected.action,
        destination_path: destination,
        result: 'complete',
      });
      queueText = queueText.replace(item.full, '');
    }

    const remaining = Math.max(0, items.length - processed.length);
    queueText = queueText
      .replace(/\n{4,}/g, '\n\n\n')
      .replace(/pending_count:\s*\d+/m, `pending_count: ${remaining}`)
      .replace(/updated_at:\s*"[^"]*"/m, `updated_at: "${nowIso()}"`)
      .replace(/\*\*Photos awaiting review:\*\*\s*\d+/m,
        `**Photos awaiting review:** ${remaining}`);
    if (!/photo-capture-review-item:start/.test(queueText)) {
      queueText = queueText.replace(
        /\*\*Photos awaiting review:\*\*\s*0\s*/m,
        '**Photos awaiting review:** 0\n\nNo camera photos are waiting for review.\n'
      );
    }
    atomicWriteText(config.paths.queue, queueText);
    const result = processed.length
      ? (approvedSeen > processed.length ? 'batch_limit_reached' : 'decisions_applied')
      : invalid.length
        ? 'decisions_need_correction'
        : 'nothing_approved';
    return {
      result,
      run_id: currentRunId,
      queue_path: config.paths.queue,
      processed_count: processed.length,
      invalid_count: invalid.length,
      approved_seen: approvedSeen,
      max_batch_items: maximum,
      processed,
      invalid,
      state_file: config.paths.state,
    };
  } finally {
    releaseLock(config.paths.review_lock);
  }
}

function main() {
  const args = workflowArguments(process.argv);
  const config = validateConfig(readJson(args.configPath));
  if (!config) fail(`Configuration was not found: ${args.configPath}`);
  if (args.command === 'build') {
    process.stdout.write(JSON.stringify(buildQueue(config)) + '\n');
    return;
  }
  if (args.command === 'apply') {
    const maximum = Math.max(
      1,
      Math.min(100, Number(args.maxBatch || config.review.max_batch_items || 10))
    );
    process.stdout.write(JSON.stringify(applyDecisions(config, maximum)) + '\n');
    return;
  }
  fail(`Unknown command: ${args.command}`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`${error.stack || error.message || error}\n`);
  process.exitCode = 1;
}
