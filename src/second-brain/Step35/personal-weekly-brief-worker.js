'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

function requiredString(value, name) {
  const result = String(value || '').trim();
  if (!result) throw new Error(`Missing required configuration: ${name}`);
  return result;
}

function numberOption(value, fallback, minimum) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(minimum, parsed) : fallback;
}

function booleanOption(value, fallback) {
  if (typeof value === 'boolean') return value;
  if (String(value).toLowerCase() === 'true') return true;
  if (String(value).toLowerCase() === 'false') return false;
  return fallback;
}

function parseConfiguration() {
  const encoded = requiredString(
    process.env.STEP35_PERSONAL_WEEKLY_CONFIG_B64,
    'STEP35_PERSONAL_WEEKLY_CONFIG_B64',
  );
  const parsed = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
  const roots = Array.isArray(parsed.sourceRoots)
    ? parsed.sourceRoots
    : JSON.parse(requiredString(parsed.sourceRootsJson, 'sourceRootsJson'));

  return {
    environment: requiredString(parsed.environment || 'staging', 'environment'),
    sourceRoots: roots.map((item) => path.resolve(requiredString(item, 'sourceRoot'))),
    outputRoot: path.resolve(requiredString(parsed.outputRoot, 'outputRoot')),
    stateRoot: path.resolve(requiredString(parsed.stateRoot, 'stateRoot')),
    timezone: requiredString(parsed.timezone || 'America/Kentucky/Louisville', 'timezone'),
    preserveExisting: booleanOption(parsed.preserveExisting, true),
    maximumSourceFiles: numberOption(parsed.maximumSourceFiles, 700, 25),
    maximumRecentNotes: numberOption(parsed.maximumRecentNotes, 6, 1),
    maximumProjects: numberOption(parsed.maximumProjects, 6, 1),
    maximumTasks: numberOption(parsed.maximumTasks, 8, 1),
    maximumUpcomingItems: numberOption(parsed.maximumUpcomingItems, 12, 1),
    nowOverride: String(parsed.nowOverride || '').trim(),
  };
}

function ensureInside(candidate, parent, name) {
  const resolvedCandidate = path.resolve(candidate);
  const resolvedParent = path.resolve(parent);
  if (
    resolvedCandidate !== resolvedParent &&
    !resolvedCandidate.startsWith(`${resolvedParent}${path.sep}`)
  ) {
    throw new Error(`Unsafe ${name}: ${resolvedCandidate}`);
  }
  return resolvedCandidate;
}

function sha256Text(value) {
  return crypto.createHash('sha256').update(String(value), 'utf8').digest('hex');
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function safeRead(filePath) {
  try {
    return fs.readFileSync(filePath, 'utf8');
  } catch (_) {
    return '';
  }
}

function frontmatterBlock(text) {
  const match = String(text).match(/^---\s*\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  return match ? match[1] : '';
}

function contentWithoutFrontmatter(text) {
  return String(text || '').replace(/^---\s*\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '');
}

function frontmatterValue(text, key) {
  const block = frontmatterBlock(text);
  if (!block) return '';
  const match = block.match(new RegExp(`^${key}:\\s*(.*?)\\s*$`, 'mi'));
  if (!match) return '';
  return match[1].trim().replace(/^['"]|['"]$/g, '');
}

function sourceDate(text, filePath, stat) {
  for (const key of [
    'created', 'date', 'recorded_at', 'generated_at', 'processed_at',
    'modified', 'updated', 'event_date', 'start',
  ]) {
    const value = frontmatterValue(text, key);
    if (!value) continue;
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }

  const filenameMatch = path.basename(filePath).match(/(20\d{2})-(\d{2})-(\d{2})/);
  if (filenameMatch) {
    const parsed = new Date(`${filenameMatch[1]}-${filenameMatch[2]}-${filenameMatch[3]}T12:00:00`);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  return stat.mtime;
}

function projectActivityDate(text) {
  for (const key of ['updated', 'modified', 'last_updated', 'status_updated']) {
    const value = frontmatterValue(text, key);
    if (!value) continue;
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  return null;
}

function cleanLine(value, maximum = 260) {
  return String(value || '')
    .replace(/<!--.*?-->/g, '')
    .replace(/^\s*[-*+]\s*/, '')
    .replace(/^\s*#+\s*/, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, maximum);
}

function titleFromNote(text, filePath) {
  const heading = String(text).match(/^#\s+(.+?)\s*$/m);
  const frontmatterTitle = frontmatterValue(text, 'title');
  return cleanLine(frontmatterTitle || (heading ? heading[1] : path.basename(filePath, '.md')), 120);
}

function obsidianLink(filePath) {
  const normalized = path.resolve(filePath).split(path.sep).join('/');
  const prefix = '/Obsidian/';
  const relative = normalized.startsWith(prefix)
    ? normalized.slice(prefix.length)
    : path.basename(normalized);
  const target = relative.replace(/\.md$/i, '');
  return `[[${target}|${path.basename(target)}]]`;
}

function localDateKey(date, timezone) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((item) => [item.type, item.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function localDateTime(date, timezone) {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(date);
}

function localDateLong(date, timezone) {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  }).format(date);
}

function within(date, start, end) {
  return date.getTime() >= start.getTime() && date.getTime() < end.getTime();
}

function normalize(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function uniqueBy(items, keyFunction) {
  const seen = new Set();
  const output = [];
  for (const item of items) {
    const key = keyFunction(item);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    output.push(item);
  }
  return output;
}

const WORK_PATH_PATTERNS = [
  /(^|\/)10 Areas\/W2 Work(\/|$)/i,
  /(^|\/)00 Inbox\/Work Shift Email Review(\/|$)/i,
  /(^|\/)00 Inbox\/Weekly Supervisor/i,
  /(^|\/)00 Inbox\/Weekly Machinist/i,
  /(^|\/)00 Inbox\/Personal Weekly Brief/i,
  /(^|\/)80 Tasks\/Legacy Voice Tasks\/Work(\/|$)/i,
];

const GENERATED_PATH_PATTERNS = [
  /nightly-curator-review/i,
  /(^|\/)90 System\/Reports(\/|$)/i,
  /weekly-(?:supervisor|machinist)-report/i,
  /personal-weekly-brief/i,
  /(^|\/)State(\/|$)/i,
];

const WORK_TEXT_PATTERN = /\b(?:4th shift|cnc|machine shop|machinist|supervisor report|qps|production shift|workplace|machine\s+(?:l-?\d+|\d+)|l-?\d{1,2}\b|cell\s+(?:\d+|one|two|three|four)|spindle|face mill|tool life|production traveler|baffle|internal threads?|thermal overload|employee handbook|criv master|uniform|shift report|shop floor|qa(?:'d)?|quality check|parl(?:eck|ax)|paycheck issue|hr about|truck parts|vending machine|(?:add\s+)?steven\s+and\s+john morgan|john morgan.*system|dry coolant|refuel station|performance review|adp workforce|work vs personal|work emails?|employee access|silencer|suppress(?:or)?|lathe|production quality|quality rejected|machine insert|akuma|weekend work|bar feeder|final inspection|body dimensions|five[- ]axis|axis mill)\b/i;
const LOW_VALUE_PATTERN = /\b(?:sale|save\s+\d+%|\d+%\s+off|bogo|shop now|new arrivals|best-?sellers|under\s+\$?\d+|klarna|pay later|limited time|expires? in|collab|budget boutique|publisher sale|election night|fosters? are needed|area manager at|machinist at|job alert|sponsored|unsubscribe)\b/i;
const IMPORTANT_PERSONAL_PATTERN = /\b(?:appointment|upcoming visit|itinerary|reservation|flight|hotel|rental|pickup|drop-?off|depart|return|autopay|automatic payment|payment scheduled|bill due|doctor|dentist|cedar point|sandusky|chiang mai|travel|reminder|insurance|renewal|delivery|low food|dog food)\b/i;
const NON_EVENT_PATTERN = /\b(?:roadmap|phase\s+\d+|canonical handoff|working specification|publisher sale|valid from|apply by|bank statements? for|receipt|build plan|project status|section|document as)\b/i;
const VAGUE_TASK_PATTERN = /^(?:take care of (?:it|the work)|start today|put that back|figure (?:it|that) out|get that figured out|test(?:ing)?\b|i don'?t know\b)/i;
const LOW_INFORMATION_TITLE_PATTERN = /^(?:unknown note(?: title)?|analy[sz]ing\b|self court overflow\b)/i;
const GENERATED_TASK_PATTERN = /\b(?:analy[sz]e (?:the )?(?:email|message)|phishing|malicious links?|data exfiltration|tracking parameters?|extract (?:all )?(?:links?|urls?)|email footer|marketing or transactional|contact support|provided text|security risks?|copyright year|physical address)\b/i;
const EXTRACTION_META_PATTERN = /\b(?:email date|indicates a future event|verify if the user|relation:\s*future|basis:\s*recorded_at_inference|confidence:\s*[\d.]+)\b/i;
const TEST_ARTIFACT_PATTERN = /\b(?:test fixture|provenance test|testing test|_test\b|screenshot[_-]?\d+|(?:^|[-_ ])test(?:[-_. ]|$))\b/i;

function isGeneratedSource(filePath) {
  const normalizedPath = filePath.split(path.sep).join('/');
  return GENERATED_PATH_PATTERNS.some((pattern) => pattern.test(normalizedPath));
}

function isTestArtifact(title, filePath) {
  const normalizedPath = filePath.split(path.sep).join('/');
  return TEST_ARTIFACT_PATTERN.test(`${title}\n${path.basename(normalizedPath)}`) ||
    /\/00 Inbox\/Documents\/Needs Filing Review\/Tests\//i.test(normalizedPath);
}

function isLowValueSource(title, text, filePath) {
  if (!filePath.includes('/50 Records/Email/Personal/')) return false;
  const sample = `${title}\n${String(text).slice(0, 1500)}`;
  return LOW_VALUE_PATTERN.test(sample) && !IMPORTANT_PERSONAL_PATTERN.test(sample);
}

function isWorkItem(value) {
  return WORK_TEXT_PATTERN.test(String(value || ''));
}

function cleanOwnerText(value, maximum = 220) {
  return cleanLine(value, maximum)
    .replace(/\s*\([^)]*\b(?:owner:\s*unknown|confidence:\s*[\d.]+|review required)\b[^)]*\)/gi, '')
    .replace(/\s*\((?:confidence|spoken\/written due text|category):[^)]*\)/gi, '')
    .replace(/\s*\((?:,?\s*)?(?:T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2})?,?\s*)?(?:category:\s*[^,)]*,?\s*)?(?:confidence:\s*[\d.]+)(?:,\s*review required)?\)\s*$/i, '')
    .replace(/\s*\((?:category:\s*[^,)]*)(?:,\s*confidence:\s*[\d.]+)?(?:,\s*review required)?\)\s*$/i, '')
    .replace(/^title:\s*["']?|["']$/g, '')
    .trim();
}

function explicitWorkClassification(text, filePath) {
  const normalizedPath = filePath.split(path.sep).join('/');
  if (WORK_PATH_PATTERNS.some((pattern) => pattern.test(normalizedPath))) return true;
  const block = frontmatterBlock(text);
  if (!block) return false;
  for (const key of ['area', 'category', 'context', 'domain', 'tags', 'topics', 'project', 'note_kind']) {
    const scalar = block.match(new RegExp(`^${key}:\\s*(.*?)\\s*$`, 'mi'));
    if (scalar && WORK_TEXT_PATTERN.test(scalar[1])) return true;
    const list = block.match(new RegExp(`^${key}:\\s*\\r?\\n((?:\\s+-\\s+.*(?:\\r?\\n|$))*)`, 'mi'));
    if (list && WORK_TEXT_PATTERN.test(list[1])) return true;
  }
  return false;
}

const PERSONAL_PATH_PATTERNS = [
  /(^|\/)10 Areas\/(?:Personal|Home|Health|Finance|Family|Relationships|Travel)(\/|$)/i,
  /(^|\/)00 Inbox\/Personal(\/|$)/i,
];

function explicitPersonalClassification(text, filePath) {
  const normalizedPath = filePath.split(path.sep).join('/');
  if (PERSONAL_PATH_PATTERNS.some((pattern) => pattern.test(normalizedPath))) return true;
  const block = frontmatterBlock(text);
  if (!block) return false;
  for (const key of ['area', 'category', 'context', 'domain', 'tags', 'topics', 'project', 'note_kind']) {
    const scalar = block.match(new RegExp(`^${key}:\\s*(.*?)\\s*$`, 'mi'));
    if (scalar && /\b(?:personal|home|health|finance|family|relationship|travel)\b/i.test(scalar[1])) return true;
    const list = block.match(new RegExp(`^${key}:\\s*\\r?\\n((?:\\s+-\\s+.*(?:\\r?\\n|$))*)`, 'mi'));
    if (list && /\b(?:personal|home|health|finance|family|relationship|travel)\b/i.test(list[1])) return true;
  }
  return false;
}

function canonicalTaskKey(text) {
  const noise = new Set([
    'a', 'an', 'and', 'i', 'it', 'need', 'the', 'to', 'out', 'functionality',
  ]);
  return normalize(text)
    .split(/\s+/)
    .filter((token) => token && !noise.has(token))
    .join(' ');
}

function walkMarkdown(root, output, excludedRoots) {
  if (!fs.existsSync(root)) return;
  const resolvedRoot = path.resolve(root);
  if (excludedRoots.some((item) => {
    return resolvedRoot === item || resolvedRoot.startsWith(`${item}${path.sep}`);
  })) {
    return;
  }
  const stack = [resolvedRoot];
  while (stack.length) {
    const directory = stack.pop();
    if (excludedRoots.some((item) => directory === item || directory.startsWith(`${item}${path.sep}`))) {
      continue;
    }
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.')) continue;
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === '99 Archive') continue;
        if (entry.name === '90 System' && !entryPath.includes('Personal Morning Briefs')) continue;
        stack.push(entryPath);
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) {
        output.push(entryPath);
      }
    }
  }
}

function extractTaskLines(text, source, previousStart, previousEnd, upcomingStart, nextEnd) {
  const output = [];
  const normalizedPath = source.path.split(path.sep).join('/');
  if (
    source.generated || source.lowValue || source.testArtifact ||
    normalizedPath.includes('/50 Records/Email/') ||
    normalizedPath.includes('/00 Inbox/Webpages/') ||
    normalizedPath.includes('/00 Inbox/Documents/')
  ) return output;
  for (const rawLine of contentWithoutFrontmatter(text).split(/\r?\n/)) {
    const match = rawLine.match(/^\s*[-*]\s*\[([ xX])\]\s+(.+?)\s*$/);
    if (!match) continue;
    const body = cleanLine(match[2], 260);
    const dueMatch = body.match(/(?:📅\s*|\bdue(?:::|:)?\s*)(20\d{2}-\d{2}-\d{2})/i);
    const due = dueMatch ? new Date(`${dueMatch[1]}T12:00:00`) : null;
    const completionMatch = body.match(/(?:✅\s*|\b(?:completed|completion)(?:::|:)?\s*)(20\d{2}-\d{2}-\d{2})/i);
    const completedAt = completionMatch ? new Date(`${completionMatch[1]}T12:00:00`) : null;
    const cleanedText = cleanOwnerText(body
      .replace(/(?:📅\s*|\bdue(?:::|:)?\s*)20\d{2}-\d{2}-\d{2}/i, '')
      .replace(/(?:✅\s*|\b(?:completed|completion)(?:::|:)?\s*)20\d{2}-\d{2}-\d{2}/i, '')
      .trim(), 220);
    const taskContext = `${source.title}\n${cleanedText}`;
    if (!cleanedText || isWorkItem(taskContext) || VAGUE_TASK_PATTERN.test(cleanedText) || GENERATED_TASK_PATTERN.test(taskContext)) continue;
    output.push({
      completed: match[1].toLowerCase() === 'x',
      completedAt,
      completedInWindow: Boolean(completedAt && within(completedAt, previousStart, previousEnd)),
      text: cleanedText,
      due,
      overdue: Boolean(due && !Number.isNaN(due.getTime()) && due < upcomingStart && match[1] === ' '),
      upcoming: Boolean(due && !Number.isNaN(due.getTime()) && within(due, upcomingStart, nextEnd)),
      sourceLink: source.link,
      sourcePath: source.path,
      sourceDate: source.date,
    });
  }
  return output;
}

function extractDateCandidates(line, now) {
  const dates = [];
  for (const match of line.matchAll(/\b(20\d{2})-(\d{2})-(\d{2})\b/g)) {
    dates.push(new Date(`${match[1]}-${match[2]}-${match[3]}T12:00:00`));
  }
  const monthPattern = /\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?!\d)(?:st|nd|rd|th)?(?:,\s*(20\d{2}))?/gi;
  for (const match of line.matchAll(monthPattern)) {
    const year = match[3] || String(now.getFullYear());
    const parsed = new Date(`${match[1]} ${match[2]}, ${year} 12:00:00`);
    dates.push(parsed);
  }
  return dates.filter((item) => !Number.isNaN(item.getTime()));
}

function ownerEventLabel(line, source, dateOnly) {
  const context = `${source.title}\n${line}`;
  if (/\b(?:autopay|automatic payment)\b/i.test(context)) {
    return 'Automatic payment scheduled';
  }
  if (/\bpriceline\b/i.test(context) && /\b(?:itinerary|rental|trip)\b/i.test(context)) {
    return 'Priceline itinerary';
  }
  return dateOnly || line.length > 180 || EXTRACTION_META_PATTERN.test(line)
    ? source.title
    : line;
}

function extractUpcomingLines(text, source, now, upcomingStart, nextEnd) {
  const output = [];
  if (source.generated || source.lowValue) return output;
  for (const rawLine of contentWithoutFrontmatter(text).split(/\r?\n/)) {
    if (!rawLine.trim() || /^\s*(?:---|```|#)/.test(rawLine)) continue;
    const line = cleanOwnerText(rawLine, 240);
    const context = `${source.title} ${line}`;
    if (!line || isWorkItem(context) || NON_EVENT_PATTERN.test(context)) continue;
    if (!IMPORTANT_PERSONAL_PATTERN.test(context)) continue;
    if (/\b(?:email date|indicates a future event|verify if the user)\b/i.test(line)) continue;
    if (/\b(?:autopay|automatic payment)\b/i.test(context) && /\$0(?:\.00)?\b/.test(context)) continue;
    for (const date of extractDateCandidates(line, now)) {
      if (!within(date, upcomingStart, nextEnd)) continue;
      const dateOnly = /^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?,?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s*20\d{2})?$/i.test(line);
      let displayText = ownerEventLabel(line, source, dateOnly);
      if (/\blow food supply\b/i.test(source.title)) displayText = 'Check remaining food supplies';
      output.push({ date, text: displayText, sourceLink: source.link, sourcePath: source.path, sourceTitle: source.title });
    }
  }
  return output;
}

function projectIdentity(source) {
  const marker = '/20 Projects/';
  const normalizedPath = source.path.split(path.sep).join('/');
  const index = normalizedPath.indexOf(marker);
  if (index < 0) return null;
  const relative = normalizedPath.slice(index + marker.length).replace(/\.md$/i, '');
  const segments = relative.split('/').filter(Boolean);
  if (!segments.length) return null;
  if (/^(?:index|\d{4}-\d{2}-\d{2}_[a-f0-9]+_index)$/i.test(segments[0])) return null;
  const combined = `${source.title} ${relative}`;
  if (/ai[-_ ]second[-_ ]brain|second brain|home server.*ai/i.test(combined)) {
    return { key: 'ai-second-brain', title: 'AI Second Brain' };
  }
  if (/^jayaroh$/i.test(segments[0]) && /^projects$/i.test(segments[1] || '') && segments[2]) {
    const title = cleanOwnerText(segments[2].replace(/\s+(?:example|draft)$/i, ''), 100);
    return { key: `jayaroh-${normalize(title)}`, title };
  }
  return { key: normalize(segments[0]), title: cleanOwnerText(segments[0], 100) };
}

function projectRepresentativeScore(source, identity) {
  const normalizedTitle = normalize(source.title);
  const baseName = normalize(path.basename(source.path, path.extname(source.path)));
  const depthPenalty = source.path.split('/').length;
  let score = -depthPenalty;
  if (normalizedTitle === normalize(identity.title)) score += 100;
  if (baseName === normalize(identity.title)) score += 100;
  if (/\b(?:dashboard mockup|rough draft|example|examples?\s*&\s*inspiration)\b/i.test(source.title)) score -= 20;
  return score;
}

function consolidateProjects(sources, previousStart, previousEnd, maximum) {
  const groups = new Map();
  for (const source of sources.filter((item) => (
    item.path.includes('/20 Projects/') &&
    item.projectActivityDate &&
    within(item.projectActivityDate, previousStart, previousEnd)
  ))) {
    const identity = projectIdentity(source);
    if (!identity || !identity.key) continue;
    const current = groups.get(identity.key);
    const score = projectRepresentativeScore(source, identity);
    if (!current || score > current.projectScore || (score === current.projectScore && source.modified > current.modified)) {
      groups.set(identity.key, { ...source, projectTitle: identity.title, projectScore: score });
    }
  }
  return Array.from(groups.values())
    .sort((a, b) => b.projectActivityDate - a.projectActivityDate || a.projectTitle.localeCompare(b.projectTitle))
    .slice(0, maximum);
}

function atomicWrite(targetPath, content) {
  const temporary = `${targetPath}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(temporary, content, { encoding: 'utf8', mode: 0o664, flag: 'wx' });
  try {
    fs.renameSync(temporary, targetPath);
  } catch (error) {
    try { fs.unlinkSync(temporary); } catch (_) {}
    throw error;
  }
}

function renderList(lines, items, renderer, emptyText = '- None found') {
  if (!items.length) {
    lines.push(emptyText);
  } else {
    for (const item of items) lines.push(renderer(item));
  }
}

function validateExistingSet(paths, dateKey, environment) {
  const dated = [paths.brief, paths.review, paths.evidence];
  const present = dated.filter((item) => fs.existsSync(item));
  if (present.length && present.length !== dated.length) {
    const missing = dated.filter((item) => !fs.existsSync(item));
    throw new Error(`Incomplete Step 35 output set. Present: ${present.join(', ')}. Missing: ${missing.join(', ')}`);
  }
  if (present.length !== dated.length) return null;
  if (!fs.existsSync(paths.latest)) throw new Error(`Existing dated output set has no latest pointer: ${paths.latest}`);
  const evidence = JSON.parse(fs.readFileSync(paths.evidence, 'utf8'));
  const outputs = evidence.outputs || {};
  const checks = {
    schema: evidence.schema_version === '1.5.0',
    environment: evidence.environment === environment,
    report_date: evidence.report_date === dateKey,
    brief_path: outputs.brief_file === paths.brief,
    review_path: outputs.review_file === paths.review,
    latest_path: outputs.latest_file === paths.latest,
    brief_hash: outputs.brief_sha256 === sha256File(paths.brief),
    review_hash: outputs.review_sha256 === sha256File(paths.review),
    latest_hash: outputs.latest_sha256 === sha256File(paths.latest),
    outbound: evidence.outbound_delivery_performed === false,
  };
  const failed = Object.keys(checks).filter((key) => !checks[key]);
  if (failed.length) throw new Error(`Existing Step 35 output set failed: ${failed.join(', ')}`);
  return evidence;
}

function main(config) {
  if (config.environment !== 'staging') throw new Error('This worker package is staging-only.');
  process.env.TZ = config.timezone;
  const now = config.nowOverride ? new Date(config.nowOverride) : new Date();
  if (Number.isNaN(now.getTime())) throw new Error(`Invalid nowOverride: ${config.nowOverride}`);
  const dateKey = localDateKey(now, config.timezone);
  const upcomingStart = new Date(`${dateKey}T00:00:00`);
  const previousEnd = new Date(upcomingStart);
  const previousStart = new Date(upcomingStart.getTime() - 7 * 24 * 60 * 60 * 1000);
  const nextEnd = new Date(upcomingStart.getTime() + 7 * 24 * 60 * 60 * 1000);

  fs.mkdirSync(config.outputRoot, { recursive: true });
  fs.mkdirSync(config.stateRoot, { recursive: true });
  const paths = {
    brief: path.join(config.outputRoot, `${dateKey}_personal-weekly-brief.md`),
    review: path.join(config.outputRoot, `${dateKey}_personal-weekly-brief-review.md`),
    latest: path.join(config.outputRoot, 'Latest Personal Weekly Brief Staging.md'),
    evidence: path.join(config.stateRoot, `${dateKey}_personal-weekly-brief-evidence.json`),
  };
  for (const [name, target] of Object.entries(paths)) {
    const parent = name === 'evidence' ? config.stateRoot : config.outputRoot;
    ensureInside(target, parent, name);
  }

  if (config.preserveExisting) {
    const existing = validateExistingSet(paths, dateKey, config.environment);
    if (existing) {
      process.stdout.write(JSON.stringify({
        status: 'already_exists_verified_and_preserved',
        environment: config.environment,
        brief_file: paths.brief,
        review_file: paths.review,
        latest_file: paths.latest,
        evidence_file: paths.evidence,
        output_set_complete: true,
        output_hashes_verified: true,
        outbound_delivery_performed: false,
      }));
      return;
    }
  }

  const discovered = [];
  const excludedRoots = [path.resolve(config.outputRoot)];
  for (const root of config.sourceRoots) walkMarkdown(root, discovered, excludedRoots);
  const uniquePaths = Array.from(new Set(discovered.map((item) => path.resolve(item))));
  if (uniquePaths.length > config.maximumSourceFiles) {
    throw new Error(`Discovered ${uniquePaths.length} files; safety limit is ${config.maximumSourceFiles}.`);
  }

  const sources = [];
  const excluded = [];
  for (const filePath of uniquePaths) {
    let stat;
    try { stat = fs.statSync(filePath); } catch (_) { continue; }
    if (!stat.isFile() || stat.size > 1024 * 1024) continue;
    const text = safeRead(filePath);
    if (!text) continue;
    const link = obsidianLink(filePath);
    if (explicitWorkClassification(text, filePath)) {
      excluded.push({ path: filePath, link, reason: 'Explicit work classification or work-report path' });
      continue;
    }
    const date = sourceDate(text, filePath, stat);
    const title = titleFromNote(text, filePath);
    sources.push({
      path: filePath,
      link,
      title,
      text,
      date,
      modified: stat.mtime,
      sha256: sha256Text(text),
      generated: isGeneratedSource(filePath),
      lowValue: isLowValueSource(title, text, filePath),
      testArtifact: isTestArtifact(title, filePath),
      personalClassified: explicitPersonalClassification(text, filePath),
      projectActivityDate: projectActivityDate(text),
    });
  }

  const recentSources = sources
    .filter((item) => within(item.date, previousStart, previousEnd) || within(item.modified, previousStart, previousEnd))
    .sort((a, b) => b.date - a.date || a.path.localeCompare(b.path));
  const recentNotes = recentSources
    .filter((item) => !item.path.includes('/80 Tasks/') && !item.path.includes('/20 Projects/'))
    .filter((item) => !item.path.includes('/50 Records/Email/'))
    .filter((item) => !item.path.includes('/00 Inbox/Documents/') && !item.path.includes('/00 Inbox/Webpages/'))
    .filter((item) => !item.generated && !item.lowValue && !item.testArtifact)
    .filter((item) => item.personalClassified)
    .filter((item) => !isWorkItem(`${item.title}\n${item.text.slice(0, 1200)}`))
    .filter((item) => !LOW_INFORMATION_TITLE_PATTERN.test(item.title))
    .slice(0, config.maximumRecentNotes);
  const updatedProjects = consolidateProjects(
    sources, previousStart, previousEnd, config.maximumProjects,
  );

  let tasks = [];
  let upcoming = [];
  for (const source of sources) {
    tasks.push(...extractTaskLines(
      source.text, source, previousStart, previousEnd, upcomingStart, nextEnd,
    ));
    upcoming.push(...extractUpcomingLines(source.text, source, now, upcomingStart, nextEnd));
  }
  tasks = uniqueBy(tasks, (item) => `${canonicalTaskKey(item.text)}:${item.completed}:${item.due ? localDateKey(item.due, config.timezone) : ''}`);
  upcoming = uniqueBy(upcoming, (item) => `${item.sourcePath}:${localDateKey(item.date, config.timezone)}`)
    .sort((a, b) => a.date - b.date);
  const itineraryGroups = new Map();
  for (const item of upcoming) {
    if (!/\b(?:itinerary|reservation|rental)\b/i.test(item.sourceTitle || '')) continue;
    const group = itineraryGroups.get(item.sourcePath) || [];
    group.push(item);
    itineraryGroups.set(item.sourcePath, group);
  }
  for (const group of itineraryGroups.values()) {
    if (group.length < 2) continue;
    group.sort((a, b) => a.date - b.date);
    const label = /\bpriceline\b/i.test(group[0].sourceTitle || '')
      ? 'Priceline itinerary'
      : 'Travel itinerary';
    group[0].text = `${label} — begins`;
    group[group.length - 1].text = `${label} — ends`;
  }
  upcoming = upcoming.slice(0, config.maximumUpcomingItems);
  const byRecency = (a, b) => b.sourceDate - a.sourceDate || a.text.localeCompare(b.text);
  const overdueTasks = tasks.filter((item) => item.overdue).sort((a, b) => a.due - b.due).slice(0, config.maximumTasks);
  const upcomingTasks = tasks.filter((item) => item.upcoming && !item.completed).sort((a, b) => a.due - b.due).slice(0, config.maximumTasks);
  const openTasks = tasks
    .filter((item) => !item.completed && !item.overdue && !item.upcoming)
    .filter((item) => item.sourceDate >= new Date(upcomingStart.getTime() - 90 * 24 * 60 * 60 * 1000))
    .sort(byRecency)
    .slice(0, config.maximumTasks);
  const completedTasks = tasks
    .filter((item) => item.completed && item.completedInWindow)
    .sort((a, b) => b.completedAt - a.completedAt)
    .slice(0, config.maximumTasks);

  const briefLines = [
    '---',
    'type: personal-weekly-brief',
    'status: pending-review',
    `environment: ${config.environment}`,
    `report_date: "${dateKey}"`,
    `previous_window_start: "${previousStart.toISOString()}"`,
    `previous_window_end: "${previousEnd.toISOString()}"`,
    `upcoming_window_start: "${upcomingStart.toISOString()}"`,
    `upcoming_window_end: "${nextEnd.toISOString()}"`,
    `generated_at: "${now.toISOString()}"`,
    '---', '',
    `# Personal Weekly Brief — ${localDateLong(now, config.timezone)}`, '',
    '> [!warning] Staging review',
    '> This is a deterministic personal-only draft. Nothing was sent and no source note was changed.', '',
    '## Weekly snapshot', '',
    `- Personal sources considered: **${sources.length}**`,
    `- Recent notes retained: **${recentNotes.length}**`,
    `- Active projects with recent activity: **${updatedProjects.length}**`,
    `- Open tasks shown: **${openTasks.length}**`,
    `- Overdue tasks shown: **${overdueTasks.length}**`,
    `- Upcoming dated items: **${upcoming.length}**`,
    `- Explicit work sources excluded: **${excluded.length}**`,
    `- Generated, test, or low-value sources suppressed: **${sources.filter((item) => item.generated || item.testArtifact || item.lowValue).length}**`, '',
    '## Previous seven days — recent notes', '',
  ];
  renderList(briefLines, recentNotes, (item) => `- ${item.title} — ${item.link}`);
  briefLines.push('', '## Previous seven days — project activity', '');
  renderList(briefLines, updatedProjects, (item) => `- ${item.projectTitle} — ${item.link}`);
  briefLines.push('', '## Completed during the previous seven days', '');
  renderList(briefLines, completedTasks, (item) => `- [x] ${item.text} — completed ${localDateKey(item.completedAt, config.timezone)} — ${item.sourceLink}`,
    '- None found with an explicit completion date in the reporting window.');
  briefLines.push('', '## Overdue tasks', '');
  renderList(briefLines, overdueTasks, (item) => `- [ ] ${item.text} — due ${localDateKey(item.due, config.timezone)} — ${item.sourceLink}`);
  briefLines.push('', '## Next seven days — dated items', '');
  renderList(briefLines, upcoming, (item) => `- **${localDateKey(item.date, config.timezone)}** — ${item.text} — ${item.sourceLink}`);
  briefLines.push('', '## Next seven days — tasks with due dates', '');
  renderList(briefLines, upcomingTasks, (item) => `- [ ] ${item.text} — due ${localDateKey(item.due, config.timezone)} — ${item.sourceLink}`);
  briefLines.push('', '## Other open tasks', '');
  renderList(briefLines, openTasks, (item) => `- [ ] ${item.text} — ${item.sourceLink}`);
  briefLines.push('', '## Weekly review prompts', '',
    '- [ ] Are the upcoming dates correct?',
    '- [ ] Which open tasks matter most this week?',
    '- [ ] Did any important personal event or decision go uncaptured?',
    '- [ ] Are any work items incorrectly included?',
    '- [ ] Is this brief concise enough for weekly use?', '');
  const brief = briefLines.join('\n');

  const reviewLines = [
    '---', 'type: personal-weekly-brief-review', 'status: pending-review',
    `environment: ${config.environment}`, `report_date: "${dateKey}"`,
    `generated_at: "${now.toISOString()}"`, '---', '',
    `# Personal Weekly Brief Review — ${localDateLong(now, config.timezone)}`, '',
    '> [!warning] Approval required',
    '> STAGING ONLY. No source was modified and no notification or message was sent.', '',
    '## Open the brief', '', obsidianLink(paths.brief), '',
    '## Acceptance checklist', '',
    '- [ ] Previous-seven-day notes are relevant and personal',
    '- [ ] Project activity is useful',
    '- [ ] Completed, overdue, and open tasks are correct',
    '- [ ] Upcoming dates are correct',
    '- [ ] No work-report material leaked into the personal brief',
    '- [ ] Section order and mobile readability are acceptable',
    '- [ ] APPROVED FOR STEP 35 PRODUCTION CANDIDATE', '',
    '## Counts', '',
    `- Discovered Markdown files: ${uniquePaths.length}`,
    `- Personal sources retained: ${sources.length}`,
    `- Explicit work sources excluded: ${excluded.length}`,
    `- Recent notes shown: ${recentNotes.length}`,
    `- Project notes shown: ${updatedProjects.length}`,
    `- Completed tasks shown: ${completedTasks.length}`,
    `- Overdue tasks shown: ${overdueTasks.length}`,
    `- Upcoming dated items shown: ${upcoming.length}`,
    `- Upcoming due tasks shown: ${upcomingTasks.length}`,
    `- Other open tasks shown: ${openTasks.length}`, '',
    '## Conservative policy notes', '',
    '- Work is excluded at both source and individual-item level.',
    '- Generated reports, test artifacts, filing-review checklists, and low-value promotional email are suppressed.',
    '- Checked tasks require an explicit completion date inside the reporting window.',
    '- Upcoming items require an explicit date plus personal event context.',
    '- Upcoming items are deduplicated by source and local date.',
      '- Project activity requires an explicit update/status date; filesystem copy time is not treated as activity.',
    '- Every displayed item links to its source note.',
    '- Source modification time is used only when a trustworthy note date is unavailable.', '',
    '## Excluded work sources', '',
  ];
  renderList(reviewLines, excluded.slice(0, 100), (item) => `- ${item.reason} — ${item.link}`,
    '- None');
  reviewLines.push('');
  const review = reviewLines.join('\n');

  const latest = [
    '# Latest Personal Weekly Brief — Staging', '',
    '## Read the brief', '', obsidianLink(paths.brief), '',
    '## Review and approve', '', obsidianLink(paths.review), '',
    'STAGING ONLY — nothing was sent.', '',
    `Generated: ${localDateTime(now, config.timezone)}`, '',
  ].join('\n');

  const evidence = {
    schema_version: '1.6.0',
    environment: config.environment,
    generated_at: now.toISOString(),
    report_date: dateKey,
    windows: {
      previous_start: previousStart.toISOString(), previous_end: previousEnd.toISOString(),
      upcoming_start: upcomingStart.toISOString(), upcoming_end: nextEnd.toISOString(),
    },
    counts: {
      discovered_files: uniquePaths.length,
      personal_sources: sources.length,
      excluded_work_sources: excluded.length,
      recent_notes_shown: recentNotes.length,
      projects_shown: updatedProjects.length,
      completed_tasks_shown: completedTasks.length,
      overdue_tasks_shown: overdueTasks.length,
      upcoming_items_shown: upcoming.length,
      upcoming_tasks_shown: upcomingTasks.length,
      open_tasks_shown: openTasks.length,
    },
    sources: sources.map((item) => ({
      path: item.path, link: item.link, source_date: item.date.toISOString(), sha256: item.sha256,
    })),
    excluded_sources: excluded,
    retained: {
      recent_notes: recentNotes.map((item) => ({ title: item.title, source_link: item.link })),
      projects: updatedProjects.map((item) => ({ title: item.projectTitle, source_link: item.link })),
      upcoming: upcoming.map((item) => ({
        date: item.date.toISOString(), text: item.text, source_link: item.sourceLink,
      })),
    },
    outbound_delivery_performed: false,
    source_files_modified: false,
    outputs: {
      brief_file: paths.brief, brief_sha256: sha256Text(brief),
      review_file: paths.review, review_sha256: sha256Text(review),
      latest_file: paths.latest, latest_sha256: sha256Text(latest),
    },
  };

  const previousLatest = fs.existsSync(paths.latest) ? fs.readFileSync(paths.latest, 'utf8') : null;
  const created = [];
  try {
    atomicWrite(paths.brief, brief); created.push(paths.brief);
    atomicWrite(paths.review, review); created.push(paths.review);
    atomicWrite(paths.evidence, `${JSON.stringify(evidence, null, 2)}\n`); created.push(paths.evidence);
    atomicWrite(paths.latest, latest); created.push(paths.latest);
  } catch (error) {
    for (const target of created.filter((item) => item !== paths.latest)) {
      try { fs.unlinkSync(target); } catch (_) {}
    }
    try {
      if (previousLatest === null) {
        if (fs.existsSync(paths.latest)) fs.unlinkSync(paths.latest);
      } else {
        atomicWrite(paths.latest, previousLatest);
      }
    } catch (_) {}
    throw error;
  }

  const failedHashes = [];
  if (sha256File(paths.brief) !== evidence.outputs.brief_sha256) failedHashes.push('brief');
  if (sha256File(paths.review) !== evidence.outputs.review_sha256) failedHashes.push('review');
  if (sha256File(paths.latest) !== evidence.outputs.latest_sha256) failedHashes.push('latest');
  if (failedHashes.length) throw new Error(`Post-write hash validation failed: ${failedHashes.join(', ')}`);

  process.stdout.write(JSON.stringify({
    status: excluded.length ? 'staging_created_with_review_items' : 'staging_created',
    environment: config.environment,
    brief_file: paths.brief,
    review_file: paths.review,
    latest_file: paths.latest,
    evidence_file: paths.evidence,
    previous_window_start: previousStart.toISOString(),
    previous_window_end: previousEnd.toISOString(),
    upcoming_window_start: upcomingStart.toISOString(),
    upcoming_window_end: nextEnd.toISOString(),
    sources_scanned: uniquePaths.length,
    personal_sources: sources.length,
    excluded_work_sources: excluded.length,
    recent_notes: recentNotes.length,
    projects: updatedProjects.length,
    completed_tasks: completedTasks.length,
    overdue_tasks: overdueTasks.length,
    upcoming_items: upcoming.length,
    upcoming_tasks: upcomingTasks.length,
    open_tasks: openTasks.length,
    output_set_complete: true,
    output_hashes_verified: true,
    outbound_delivery_performed: false,
  }));
}

function runSelfTest() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'step35-worker-test-'));
  try {
    const vault = path.join(root, 'Obsidian');
    const projects = path.join(vault, '20 Projects', 'Personal Test');
    const tasks = path.join(vault, '80 Tasks');
    const work = path.join(vault, '10 Areas', 'W2 Work');
    const inbox = path.join(vault, '00 Inbox');
    const email = path.join(vault, '50 Records', 'Email', 'Personal', '2026', '2026-08');
    const output = path.join(vault, '00 Inbox', 'Personal Weekly Brief Staging');
    const state = path.join(root, 'State', 'Step 35', 'Staging');
    fs.mkdirSync(projects, { recursive: true });
    fs.mkdirSync(tasks, { recursive: true });
    fs.mkdirSync(work, { recursive: true });
    fs.mkdirSync(inbox, { recursive: true });
    fs.mkdirSync(email, { recursive: true });
    fs.mkdirSync(path.join(vault, '30 Reference'), { recursive: true });
    fs.mkdirSync(path.join(inbox, 'Documents', 'Needs Filing Review'), { recursive: true });
    fs.mkdirSync(path.join(inbox, 'Webpages'), { recursive: true });
    fs.writeFileSync(path.join(projects, 'Trip.md'), '---\ncreated: 2026-08-01\nupdated: 2026-08-14\n---\n# Trip\nCedar Point on August 18, 2026.\n');
    fs.writeFileSync(path.join(projects, 'Stale Project.md'), '---\ncreated: 2026-08-14\n---\n# Stale Project\nThis file has no explicit activity date.\n');
    fs.writeFileSync(path.join(vault, '20 Projects', '2026-08-04_febb28f6_index.md'), '# Index\n');
    fs.writeFileSync(path.join(tasks, 'Tasks.md'), '# Tasks\n- [ ] Pack bag 📅 2026-08-17\n- [x] Buy tickets ✅ 2026-08-12\n- [x] Old checked item\n- [ ] Call the doctor (T15:00:00-04:00, confidence: 1) 📅 2026-08-14\n- [ ] Monitor L-24 performance\n');
    fs.writeFileSync(path.join(work, 'Shift.md'), '---\ndomain: work\n---\n# Shift\nMachine L24 needs attention.\n');
    fs.writeFileSync(path.join(inbox, '2026-08-14_personal-note.md'), '---\ncreated: 2026-08-14\n---\n# Pack for Cedar Point\nRental pickup is August 17, 2026.\n');
    fs.writeFileSync(path.join(inbox, '2026-08-14_explicit-personal.md'), '---\ncreated: 2026-08-14\ncategory: personal\n---\n# Call Mom\n');
    fs.writeFileSync(path.join(inbox, '2026-08-14_nightly-curator-review.md'), '# Review\n- [ ] Generated feedback task\n');
    fs.writeFileSync(path.join(email, '2026-08-14_sale.md'), '---\ncreated: 2026-08-14\n---\n# BOGO FREE sale\nValid August 20, 2026.\n');
    fs.writeFileSync(path.join(email, '2026-08-15_autopay.md'), '---\ncreated: 2026-08-15\n---\n# Your AutoPay payment is scheduled for August 19, 2026\nAutomatic payment is scheduled for August 19, 2026.\nReminder: automatic payment on August 19, 2026.\n');
    fs.writeFileSync(path.join(email, '2026-08-15_priceline.md'), '---\ncreated: 2026-08-15\n---\n# Your Priceline itinerary for Louisville, KY - Mon, Aug 17 (Trip #: 137-620-876-26)\nRental pickup begins August 17, 2026.\nRental return ends August 20, 2026.\n');
    fs.writeFileSync(path.join(inbox, '2026-08-14_old-receipt.md'), '# Old receipt\nVerify bank statements for August 2021.\n');
    fs.writeFileSync(path.join(inbox, '2026-08-14_work-leak.md'), '# Return vending machine key to locked box\n');
    fs.writeFileSync(path.join(inbox, '2026-08-14_unknown.md'), '# Unknown Note Title\n');
    fs.writeFileSync(path.join(inbox, '2026-08-15_low-food.md'), '---\ncreated: 2026-08-15\n---\n# Low Food Supply\n**Monday** → `2026-08-17` (relation: future, basis: recorded_at_inference, confidence: 0.95)\n');
    fs.writeFileSync(path.join(email, '2026-08-15_belize.md'), '---\ncreated: 2026-08-15\n---\n# Belize travel offer\nThe email date (2026-08-15) indicates a future event in January 2027. Verify if the user is still interested.\n- [ ] Analyze the email for phishing and malicious links\n');
    fs.writeFileSync(path.join(email, '2026-08-15_zero-autopay.md'), '---\ncreated: 2026-08-15\n---\n# AutoPay scheduled\nAutomatic payment of $0.00 is scheduled for August 19, 2026.\n');
    fs.writeFileSync(path.join(inbox, '2026-08-14_parleck.md'), '# Inquire About Parleck Status\n- [ ] Follow up with Stewart in person\n');
    fs.writeFileSync(path.join(inbox, '2026-08-14_baffles.md'), '# Deburr and clean baffles\n- [ ] Clean the internal threads\n');
    fs.writeFileSync(path.join(inbox, '2026-08-14_cell-one.md'), '# Secure boards in cell one\n- [ ] Figure out a way to secure the boards that are down in cell one\n');
    fs.writeFileSync(path.join(inbox, 'Documents', '2026-08-14_fixture.md'), '# HOME IMPROVEMENT RECEIPT TEST FIXTURE\n');
    fs.writeFileSync(path.join(inbox, 'Documents', 'Needs Filing Review', '2026-08-14_review.md'), '# Review\n- [ ] Confirm the extraction is complete and readable.\n- [ ] Decide the permanent vault location.\n');
    fs.writeFileSync(path.join(inbox, 'Webpages', '2026-08-14_capture.md'), '# Captured webpage\n- [ ] Confirm the capture is the intended page.\n');
    fs.writeFileSync(path.join(inbox, '2026-08-15_silencer-quality.md'), '# Silencer Lathe Production Quality Inspection\n');
    fs.writeFileSync(path.join(inbox, '2026-08-15_akuma-legs.md'), '# Akuma Legs\n');
    fs.writeFileSync(path.join(inbox, '2026-08-15_voice-note-testing-test.md'), '# Voice Note Testing Test\n');
    fs.writeFileSync(path.join(vault, '30 Reference', 'ssh-notes-test.md'), '# SSH Notes Test\n- [ ] Remove any obsolete test host entries after validation.\n');
    fs.writeFileSync(path.join(inbox, '2026-08-15_weekend-work.md'), '# Compile weekend work summary\n- [ ] Get weekend work compiled into a daily summary\n');
    fs.writeFileSync(path.join(tasks, 'MetadataTasks.md'), '# Metadata\n- [ ] Add auto task generation (owner: unknown, confidence: 0.8, review required)\n- [ ] Add auto task generation\n');
    fs.writeFileSync(path.join(tasks, 'DuplicateTasks.md'), '# Duplicate wording\n- [ ] Formalize failure recovery document and print it out\n- [ ] Formalize the failure recovery document, and I need to print it out\n');
    main({
      environment: 'staging', sourceRoots: [vault], outputRoot: output,
      stateRoot: state, timezone: 'America/Kentucky/Louisville',
      preserveExisting: true, maximumSourceFiles: 50, maximumRecentNotes: 6,
      maximumProjects: 6, maximumTasks: 8, maximumUpcomingItems: 12,
      nowOverride: '2026-08-15T16:00:00-04:00',
    });
    const brief = safeRead(path.join(output, '2026-08-15_personal-weekly-brief.md'));
    if (!brief.includes('Cedar Point on August 18, 2026')) throw new Error('Self-test missed upcoming event.');
    if (!brief.includes('Pack bag')) throw new Error('Self-test missed upcoming task.');
    if (!brief.includes('Buy tickets')) throw new Error('Self-test missed dated completion.');
    if (brief.includes('Old checked item')) throw new Error('Self-test invented an undated completion.');
    if (brief.includes('Monitor L-24')) throw new Error('Self-test leaked an item-level work task.');
    if (brief.includes('Generated feedback task')) throw new Error('Self-test leaked a generated-review task.');
    if (brief.includes('BOGO FREE')) throw new Error('Self-test leaked promotional email.');
    if (brief.includes('August 2021')) throw new Error('Self-test misparsed a year as a month day.');
    if ((brief.match(/automatic payment/gi) || []).length > 1) throw new Error('Self-test duplicated a same-source dated event.');
    if (!brief.includes('Automatic payment scheduled')) throw new Error('Self-test did not shorten an automatic-payment email label.');
    if (!brief.includes('Priceline itinerary — begins') || !brief.includes('Priceline itinerary — ends')) throw new Error('Self-test did not shorten itinerary labels.');
    if (brief.includes('Your Priceline itinerary for Louisville')) throw new Error('Self-test leaked a raw itinerary email subject.');
    if (brief.includes('**2026-08-15** — Your AutoPay')) throw new Error('Self-test treated front matter as an event.');
    if (brief.includes('confidence:')) throw new Error('Self-test leaked extraction metadata.');
    if (brief.includes('Machine L24')) throw new Error('Self-test leaked work content.');
    if (brief.includes('vending machine key')) throw new Error('Self-test leaked a known mixed-Inbox work note.');
    if (brief.includes('Unknown Note Title')) throw new Error('Self-test retained a low-information note title.');
    if (brief.includes('2026-08-04_febb28f6_index')) throw new Error('Self-test treated an index artifact as a project.');
    if (brief.includes('Analyze the email')) throw new Error('Self-test promoted an email-analysis artifact to a task.');
    if (brief.includes('indicates a future event')) throw new Error('Self-test promoted extraction commentary to an event.');
    if (brief.includes('recorded_at_inference')) throw new Error('Self-test leaked inferred-date metadata.');
    if (brief.includes('Parleck') || brief.includes('Follow up with Stewart')) throw new Error('Self-test leaked the known Parleck work item.');
    if (brief.includes('internal threads') || brief.includes('cell one')) throw new Error('Self-test leaked a work task through a mixed Inbox note.');
    if (brief.includes('TEST FIXTURE')) throw new Error('Self-test retained a document test fixture as a recent note.');
    if (brief.includes('Confirm the extraction') || brief.includes('permanent vault location') || brief.includes('Confirm the capture')) throw new Error('Self-test promoted ingestion-review checkboxes to personal tasks.');
    if (brief.includes('Automatic payment of $0')) throw new Error('Self-test retained a zero-dollar autopay event.');
    if (!brief.includes('Check remaining food supplies')) throw new Error('Self-test did not render the low-food reminder as an action.');
    if (brief.includes('Silencer Lathe') || brief.includes('Akuma Legs')) throw new Error('Self-test retained a manufacturing note in personal recent notes.');
    if (brief.includes('Voice Note Testing Test') || brief.includes('obsolete test host entries')) throw new Error('Self-test retained a test artifact.');
    if (brief.includes('weekend work compiled')) throw new Error('Self-test retained a work task through a mixed Inbox note.');
    if ((brief.match(/Add auto task generation/g) || []).length !== 1) throw new Error('Self-test did not remove unreviewed extraction metadata before task deduplication.');
    if ((brief.match(/Formalize (?:the )?failure recovery document/gi) || []).length !== 1) throw new Error('Self-test did not deduplicate a wording variant.');
    if (!brief.includes('- Personal Test —')) throw new Error('Self-test missed explicitly dated project activity.');
    if (brief.includes('Stale Project')) throw new Error('Self-test treated project creation/copy time as activity.');
    if (!brief.includes('Call Mom')) throw new Error('Self-test excluded an explicitly classified personal note.');
    if (brief.includes('Pack for Cedar Point')) throw new Error('Self-test treated an unclassified raw Inbox note as a personal recent note.');
    process.stdout.write('\n' + JSON.stringify({ status: 'STEP_35_WORKER_SELF_TEST_PASS' }));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

if (process.argv.includes('--self-test')) {
  runSelfTest();
} else {
  main(parseConfiguration());
}
