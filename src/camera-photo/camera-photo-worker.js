#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const cp = require('child_process');

const WORKER_VERSION = '1.2.0';

class TransientError extends Error {
  constructor(message, code = 'transient_error') {
    super(message);
    this.name = 'TransientError';
    this.code = code;
  }
}

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

function readJson(target, label = target) {
  try {
    return JSON.parse(fs.readFileSync(target, 'utf8'));
  } catch (error) {
    throw new Error(`Could not read ${label}: ${error.message}`);
  }
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

function sha256File(target) {
  const hash = crypto.createHash('sha256');
  const descriptor = fs.openSync(target, 'r');
  const buffer = Buffer.alloc(1024 * 1024);
  try {
    while (true) {
      const count = fs.readSync(
        descriptor,
        buffer,
        0,
        buffer.length,
        null
      );
      if (!count) break;
      hash.update(buffer.subarray(0, count));
    }
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest('hex');
}

function sha256Text(text) {
  return crypto.createHash('sha256').update(text, 'utf8').digest('hex');
}

function safeName(value, maximum = 180) {
  return String(value || 'capture')
    .replace(/[^\w.\- ()[\]]+/g, '_')
    .slice(0, maximum);
}

const SCREENSHOT_PREFIX = '__SB_SCREENSHOT__';

function screenshotIntakeFromName(name) {
  const value = String(name || '');
  if (!value.startsWith(SCREENSHOT_PREFIX)) return null;
  const rest = value.slice(SCREENSHOT_PREFIX.length);
  const splitAt = rest.indexOf('__');
  if (splitAt < 1) return null;
  const requestId = rest.slice(0, splitAt);
  const originalName = rest.slice(splitAt + 2);
  if (!requestId || !originalName) return null;
  return { request_id: requestId, original_name: originalName };
}

function candidateDisplayName(candidate) {
  return String(candidate?.originalName || candidate?.name || 'capture');
}

function slugify(value) {
  return String(value || 'camera capture')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 72) || 'camera-capture';
}

function yamlString(value) {
  return JSON.stringify(String(value ?? ''));
}

function uniquePath(directory, filename, suffix) {
  ensureDir(directory);
  const first = path.join(directory, filename);
  if (!fs.existsSync(first)) return first;
  const extension = path.extname(filename);
  const stem = path.basename(filename, extension);
  return path.join(directory, `${stem}_${suffix}${extension}`);
}

function copyVerified(source, destination) {
  ensureDir(path.dirname(destination));
  if (fs.existsSync(destination)) {
    if (sha256File(source) === sha256File(destination)) return;
    throw new Error(`Destination conflict: ${destination}`);
  }
  const temporary = path.join(
    path.dirname(destination),
    `.${path.basename(destination)}.${process.pid}.${Date.now()}.partial`
  );
  fs.copyFileSync(source, temporary, fs.constants.COPYFILE_EXCL);
  const sourceHash = sha256File(source);
  const destinationHash = sha256File(temporary);
  if (sourceHash !== destinationHash) {
    fs.rmSync(temporary, { force: true });
    throw new Error('Copy checksum validation failed.');
  }
  const descriptor = fs.openSync(temporary, 'r');
  try {
    fs.fsyncSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
  fs.renameSync(temporary, destination);
}

function moveVerified(source, destination) {
  ensureDir(path.dirname(destination));
  try {
    fs.renameSync(source, destination);
    return;
  } catch (error) {
    if (error.code !== 'EXDEV') throw error;
  }
  copyVerified(source, destination);
  fs.unlinkSync(source);
}

function commandExists(program) {
  const result = cp.spawnSync('sh', ['-lc', `command -v ${program}`], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  return result.status === 0;
}

function execute(program, args, timeoutSeconds, options = {}) {
  return cp.execFileSync(program, args, {
    encoding: options.encoding || 'utf8',
    timeout: timeoutSeconds * 1000,
    maxBuffer: options.maxBuffer || 64 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
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
    worker_version: WORKER_VERSION,
  });
  return true;
}

function releaseLock(lockDir) {
  fs.rmSync(lockDir, { recursive: true, force: true });
}

function readState(target) {
  if (!fs.existsSync(target)) {
    return {
      state_version: 1,
      worker_version: WORKER_VERSION,
      created_at: nowIso(),
      updated_at: nowIso(),
      files: {},
    };
  }
  const state = readJson(target, 'camera-photo state');
  state.files = state.files || {};
  return state;
}

function appendEvent(config, event) {
  ensureDir(config.paths.logs);
  fs.appendFileSync(
    path.join(config.paths.logs, 'camera-photo-events.jsonl'),
    JSON.stringify(event) + '\n',
    'utf8'
  );
}

function failureControl(args) {
  const output = cp.execFileSync(
    'python3',
    ['/scripts/Second Brain Failure Control/failure_control.py', ...args],
    {
      encoding: 'utf8',
      timeout: 30000,
      maxBuffer: 4 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  ).trim();
  if (!output) {
    throw new Error('Failure Control returned no output.');
  }
  return JSON.parse(output.split(/\r?\n/).filter(Boolean).at(-1));
}

function retryDelaySeconds(attempt) {
  const schedule = [60, 300, 900];
  const index = Math.max(0, Math.min(schedule.length - 1, Number(attempt || 1) - 1));
  return schedule[index];
}

function validateConfig(config) {
  if (config.schema_version !== '1.0.0') {
    fail(`Unsupported configuration schema: ${config.schema_version}`);
  }
  const requiredPaths = [
    'camera_inbox',
    'processing',
    'archive',
    'failed',
    'duplicates',
    'temp',
    'extracted',
    'pending_notes',
    'approved_documents',
    'preview_attachments',
    'queue',
    'state',
    'logs',
    'ingest_lock',
    'vision_schema',
    'vision_prompt',
  ];
  for (const key of requiredPaths) {
    if (!path.isAbsolute(String(config.paths?.[key] || ''))) {
      fail(`Configuration path ${key} must be absolute.`);
    }
  }
  if (process.env.CAMERA_PHOTO_TEST_MODE !== '1') {
    const captureKeys = [
      'camera_inbox', 'processing', 'archive', 'failed', 'duplicates',
      'temp', 'extracted', 'state', 'logs', 'ingest_lock',
    ];
    for (const key of captureKeys) {
      if (!config.paths[key].startsWith('/Capture/Documents')) {
        fail(`Unsafe capture path ${key}: ${config.paths[key]}`);
      }
    }
    const obsidianKeys = [
      'pending_notes', 'approved_documents', 'preview_attachments', 'queue',
    ];
    for (const key of obsidianKeys) {
      if (!config.paths[key].startsWith('/Obsidian/')) {
        fail(`Unsafe Obsidian path ${key}: ${config.paths[key]}`);
      }
    }
  }
  if (!/^https?:\/\//.test(config.ollama?.chat_url || '')) {
    fail('ollama.chat_url must be an HTTP(S) URL.');
  }
  if (!/^https?:\/\//.test(config.ollama?.show_url || '')) {
    fail('ollama.show_url must be an HTTP(S) URL.');
  }
  const contextTokens = Number(config.ollama?.context_tokens || 16384);
  const maxOutputTokens = Number(config.ollama?.max_output_tokens || 4096);
  if (!Number.isInteger(contextTokens) || contextTokens < 8192 ||
      contextTokens > 131072) {
    fail('ollama.context_tokens must be an integer from 8192 through 131072.');
  }
  if (!Number.isInteger(maxOutputTokens) || maxOutputTokens < 1024 ||
      maxOutputTokens > 8192 || maxOutputTokens >= contextTokens) {
    fail('ollama.max_output_tokens must be an integer from 1024 through 8192 and less than context_tokens.');
  }
  return config;
}

function workflowArguments(argv) {
  const result = {
    command: argv[2] || 'process',
    configPath: '',
    maxBatch: null,
  };
  for (let index = 3; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--config') result.configPath = argv[++index] || '';
    else if (argument === '--max-batch') result.maxBatch = Number(argv[++index]);
    else fail(`Unknown argument: ${argument}`);
  }
  const encoded = process.env.CAMERA_WORKFLOW_PAYLOAD_B64 || '';
  if (encoded) {
    const payload = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
    result.configPath = String(payload.configPath || result.configPath);
    if (payload.maxBatchItems !== undefined) {
      result.maxBatch = Number(payload.maxBatchItems);
    }
    if (payload.operationMode) result.command = String(payload.operationMode);
  }
  if (!result.configPath) {
    fail('Use --config PATH or provide CAMERA_WORKFLOW_PAYLOAD_B64.');
  }
  return result;
}

function alphanumericCount(text) {
  return (String(text || '').match(/[\p{L}\p{N}]/gu) || []).length;
}

function cleanText(value) {
  return String(value || '')
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{4,}/g, '\n\n\n')
    .trim();
}

function boundedEvidence(value, maximumCharacters) {
  const text = cleanText(value);
  const maximum = Math.max(1000, Number(maximumCharacters) || 12000);
  if (text.length <= maximum) return text;
  const headLength = Math.floor(maximum * 0.75);
  const tailLength = maximum - headLength;
  return [
    text.slice(0, headLength),
    '[OCR evidence truncated for vision context safety]',
    text.slice(-tailLength),
  ].join('\n');
}

function cleanStrings(value, maximum = 50) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => cleanText(item))
    .filter(Boolean)
    .slice(0, maximum);
}

function normalizeExtraction(raw) {
  const quality = raw?.quality && typeof raw.quality === 'object'
    ? raw.quality
    : {};
  const clamp = (value) => Math.max(0, Math.min(1, Number(value) || 0));
  return {
    schema_version: '1.0.0',
    capture_type: String(raw?.capture_type || 'unknown'),
    title: cleanText(raw?.title) || 'Camera capture',
    useful_content: raw?.useful_content === true,
    verbatim_transcription: cleanText(raw?.verbatim_transcription),
    cleaned_interpretation: cleanText(raw?.cleaned_interpretation),
    summary: cleanText(raw?.summary) || 'Manual review is required.',
    visual_structure: cleanText(raw?.visual_structure),
    regions: (Array.isArray(raw?.regions) ? raw.regions : []).slice(0, 100).map(
      (item, index) => ({
        id: cleanText(item?.id) || `region-${index + 1}`,
        label: cleanText(item?.label),
        color: cleanText(item?.color),
        position: cleanText(item?.position),
        text: cleanText(item?.text),
        confidence: clamp(item?.confidence),
      })
    ),
    calendar_entries: (
      Array.isArray(raw?.calendar_entries) ? raw.calendar_entries : []
    ).slice(0, 100).map((item) => ({
      visible_date_text: cleanText(item?.visible_date_text),
      date: cleanText(item?.date),
      start_time: cleanText(item?.start_time),
      end_time: cleanText(item?.end_time),
      title: cleanText(item?.title),
      people: cleanStrings(item?.people, 20),
      confidence: clamp(item?.confidence),
      evidence: cleanText(item?.evidence),
    })),
    suggested_tasks: (
      Array.isArray(raw?.suggested_tasks) ? raw.suggested_tasks : []
    ).slice(0, 100).map((item) => ({
      text: cleanText(item?.text),
      due_date: cleanText(item?.due_date),
      assignee: cleanText(item?.assignee),
      confidence: clamp(item?.confidence),
      evidence: cleanText(item?.evidence),
    })),
    people: cleanStrings(raw?.people),
    projects: cleanStrings(raw?.projects),
    topics: cleanStrings(raw?.topics),
    uncertain_items: (
      Array.isArray(raw?.uncertain_items) ? raw.uncertain_items : []
    ).slice(0, 100).map((item) => ({
      text: cleanText(item?.text),
      reason: cleanText(item?.reason),
      alternatives: cleanStrings(item?.alternatives, 10),
    })),
    quality: {
      legibility: String(quality.legibility || 'difficult'),
      blur: String(quality.blur || 'minor'),
      glare: String(quality.glare || 'minor'),
      perspective: String(quality.perspective || 'moderate_angle'),
      cropped_content: quality.cropped_content === true,
      recapture_recommended: quality.recapture_recommended === true,
      recapture_reason: cleanText(quality.recapture_reason),
    },
  };
}

function imageProgram() {
  if (commandExists('magick')) return { program: 'magick', prefix: [] };
  if (commandExists('convert')) return { program: 'convert', prefix: [] };
  return null;
}

function prepareImages(source, workDirectory, config) {
  const image = imageProgram();
  if (!image) {
    return {
      natural: source,
      enhanced: source,
      normalized: false,
      preprocessing: 'original_image_fallback',
    };
  }
  const natural = path.join(workDirectory, 'vision-natural.jpg');
  const enhanced = path.join(workDirectory, 'vision-enhanced.jpg');
  const edge = Number(config.processing.maximum_vision_edge_pixels || 2600);
  const quality = Number(config.processing.vision_jpeg_quality || 88);
  execute(image.program, [
    ...image.prefix,
    source,
    '-auto-orient',
    '-resize', `${edge}x${edge}>`,
    '-strip',
    '-quality', String(quality),
    natural,
  ], 180);
  execute(image.program, [
    ...image.prefix,
    natural,
    '-colorspace', 'Gray',
    '-contrast-stretch', '0.5%x0.5%',
    '-sharpen', '0x1',
    '-quality', String(quality),
    enhanced,
  ], 180);
  return {
    natural,
    enhanced,
    normalized: true,
    preprocessing: 'auto_orient_resize_contrast_enhancement',
  };
}

function tesseractPass(imagePath, pageSegmentation, config) {
  try {
    return cleanText(execute('tesseract', [
      imagePath,
      'stdout',
      '-l', String(config.ocr.tesseract_language || 'eng'),
      '--psm', String(pageSegmentation),
    ], Number(config.ocr.tesseract_timeout_seconds || 180)));
  } catch (_) {
    return '';
  }
}

function runTesseract(images, config) {
  if (!config.ocr.tesseract_enabled) {
    return { status: 'disabled', text: '', strategy: '' };
  }
  if (!commandExists('tesseract')) {
    return { status: 'unavailable', text: '', strategy: '' };
  }
  const candidates = [
    { strategy: 'enhanced_psm6', text: tesseractPass(images.enhanced, 6, config) },
    { strategy: 'enhanced_psm11', text: tesseractPass(images.enhanced, 11, config) },
    { strategy: 'natural_psm11', text: tesseractPass(images.natural, 11, config) },
  ].sort((left, right) =>
    alphanumericCount(right.text) - alphanumericCount(left.text)
  );
  const best = candidates[0];
  return {
    status: best.text ? 'complete' : 'no_text',
    text: best.text,
    strategy: best.strategy,
  };
}

async function fetchWithTimeout(url, options, timeoutSeconds) {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    Math.max(1, timeoutSeconds) * 1000
  );
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    throw new TransientError(
      `Request to ${url} failed: ${error.message}`,
      'network_unavailable'
    );
  } finally {
    clearTimeout(timeout);
  }
}

async function runPaddle(imagePath, config) {
  if (!config.ocr.paddle_enabled) {
    return { status: 'disabled', text: '', lines: [], mean_confidence: 0 };
  }
  try {
    const form = new FormData();
    form.append(
      'file',
      new Blob([fs.readFileSync(imagePath)], { type: 'image/jpeg' }),
      'capture.jpg'
    );
    const response = await fetchWithTimeout(
      config.ocr.paddle_url,
      { method: 'POST', body: form },
      Number(config.ocr.paddle_timeout_seconds || 240)
    );
    if (!response.ok) {
      throw new Error(`PaddleOCR returned HTTP ${response.status}.`);
    }
    const body = await response.json();
    return {
      status: 'complete',
      text: cleanText(body.text),
      lines: Array.isArray(body.lines) ? body.lines : [],
      mean_confidence: Number(body.mean_confidence || 0),
    };
  } catch (error) {
    if (config.ocr.paddle_required) {
      throw new TransientError(
        `Required PaddleOCR service is unavailable: ${error.message}`,
        'paddle_unavailable'
      );
    }
    return {
      status: 'unavailable',
      text: '',
      lines: [],
      mean_confidence: 0,
      error: error.message,
    };
  }
}

async function confirmVisionModel(config) {
  const response = await fetchWithTimeout(
    config.ollama.show_url,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: config.ollama.model,
        verbose: false,
      }),
    },
    30
  );
  if (!response.ok) {
    throw new TransientError(
      `Ollama model check returned HTTP ${response.status}.`,
      'vision_model_unavailable'
    );
  }
  const details = await response.json();
  const capabilities = Array.isArray(details.capabilities)
    ? details.capabilities.map((item) => String(item).toLowerCase())
    : [];
  if (!capabilities.includes('vision')) {
    throw new TransientError(
      `${config.ollama.model} does not report the vision capability.`,
      'vision_capability_missing'
    );
  }
  return capabilities;
}

function boundedVisionSchema(schema) {
  const copy = JSON.parse(JSON.stringify(schema));
  const p = copy.properties || {};

  const maxLength = (property, maximum) => {
    if (p[property] && typeof p[property] === 'object') {
      p[property].maxLength = maximum;
    }
  };

  maxLength('title', 180);
  maxLength('verbatim_transcription', 5000);
  maxLength('cleaned_interpretation', 2500);
  maxLength('summary', 800);
  maxLength('visual_structure', 1500);

  const arrays = {
    regions: 25,
    calendar_entries: 25,
    suggested_tasks: 25,
    people: 25,
    projects: 25,
    topics: 25,
    uncertain_items: 25,
  };

  for (const [name, maximum] of Object.entries(arrays)) {
    if (p[name] && typeof p[name] === 'object') {
      p[name].maxItems = maximum;
    }
  }

  const boundObjectStrings = (arrayName, limits) => {
    const properties = p[arrayName]?.items?.properties;
    if (!properties) return;
    for (const [name, maximum] of Object.entries(limits)) {
      if (properties[name] && properties[name].type === 'string') {
        properties[name].maxLength = maximum;
      }
    }
  };

  boundObjectStrings('regions', {
    id: 80,
    label: 160,
    color: 80,
    position: 200,
    text: 1200,
  });

  boundObjectStrings('calendar_entries', {
    visible_date_text: 120,
    date: 32,
    start_time: 32,
    end_time: 32,
    title: 300,
    evidence: 600,
  });

  boundObjectStrings('suggested_tasks', {
    text: 400,
    due_date: 32,
    assignee: 160,
    evidence: 600,
  });

  boundObjectStrings('uncertain_items', {
    text: 400,
    reason: 500,
  });

  const quality = p.quality?.properties;
  if (quality?.recapture_reason) quality.recapture_reason.maxLength = 600;

  return copy;
}

function parseVisionContent(envelope) {
  const content = cleanText(envelope?.message?.content);
  if (!content) {
    return {
      ok: false,
      content: '',
      error: 'empty_content',
      done: envelope?.done,
      done_reason: envelope?.done_reason || '',
      eval_count: Number(envelope?.eval_count || 0),
    };
  }

  try {
    return {
      ok: true,
      raw: JSON.parse(content),
      content,
      done: envelope?.done,
      done_reason: envelope?.done_reason || '',
      eval_count: Number(envelope?.eval_count || 0),
    };
  } catch (error) {
    return {
      ok: false,
      content,
      error: error.message,
      done: envelope?.done,
      done_reason: envelope?.done_reason || '',
      eval_count: Number(envelope?.eval_count || 0),
    };
  }
}

async function requestVisionAttempt({
  images,
  sourceContext,
  schema,
  prompt,
  config,
  numPredict,
}) {
  const body = {
    model: config.ollama.model,
    stream: false,
    think: false,
    format: schema,
    options: {
      temperature: Number(config.ollama.temperature || 0.1),
      num_ctx: Math.max(8192, Number(config.ollama.context_tokens || 16384)),
      num_predict: numPredict,
    },
    messages: [
      { role: 'system', content: prompt },
      {
        role: 'user',
        content: [
          'Analyze both supplied versions of the same camera photograph.',
          'Image 1 is color-normalized. Image 2 is contrast-enhanced for reading.',
          'Return only JSON matching the schema.',
          '',
          'SOURCE AND OCR EVIDENCE:',
          JSON.stringify(sourceContext, null, 2),
        ].join('\n'),
        images: [
          fs.readFileSync(images.natural).toString('base64'),
          fs.readFileSync(images.enhanced).toString('base64'),
        ],
      },
    ],
  };

  const response = await fetchWithTimeout(
    config.ollama.chat_url,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    Number(config.ollama.timeout_seconds || 600)
  );

  if (!response.ok) {
    const detail = (await response.text()).slice(0, 1000);
    const message = `Ollama returned HTTP ${response.status}: ${detail}`;
    if (response.status >= 500 || response.status === 429) {
      throw new TransientError(message, 'vision_request_failed');
    }
    throw new Error(message);
  }

  return parseVisionContent(await response.json());
}

async function runVision(images, ocr, source, schema, prompt, config) {
  await confirmVisionModel(config);

  const maximumOcrCharacters = Number(
    config.processing.maximum_ocr_evidence_characters || 12000
  );

  const sourceContext = {
    source_filename: source.name,
    captured_or_modified_at: new Date(source.mtimeMs).toISOString(),
    timezone: config.timezone,
    tesseract_status: ocr.tesseract.status,
    tesseract_text: boundedEvidence(
      ocr.tesseract.text,
      maximumOcrCharacters
    ),
    paddle_status: ocr.paddle.status,
    paddle_text: boundedEvidence(
      ocr.paddle.text,
      maximumOcrCharacters
    ),
    paddle_mean_confidence: ocr.paddle.mean_confidence,
  };

  const contextTokens = Math.max(
    8192,
    Number(config.ollama.context_tokens || 16384)
  );
  const configuredOutput = Math.max(
    1024,
    Number(config.ollama.max_output_tokens || 4096)
  );

  const first = await requestVisionAttempt({
    images,
    sourceContext,
    schema,
    prompt,
    config,
    numPredict: configuredOutput,
  });

  if (first.ok) {
    return normalizeExtraction(first.raw);
  }

  const retryOutput = Math.max(
    configuredOutput,
    Math.min(8192, Math.floor(contextTokens * 0.5), 6144)
  );

  const compactContext = {
    ...sourceContext,
    tesseract_text: boundedEvidence(
      sourceContext.tesseract_text,
      Math.min(maximumOcrCharacters, 6000)
    ),
    paddle_text: boundedEvidence(
      sourceContext.paddle_text,
      Math.min(maximumOcrCharacters, 6000)
    ),
  };

  const recoveryPrompt = [
    prompt,
    '',
    'RECOVERY ATTEMPT — BE COMPACT.',
    'A previous structured response could not be parsed.',
    'Do not reproduce OCR noise or repeated garbage.',
    'For verbatim_transcription, include only text actually supported by the image; use [unclear] where necessary.',
    'Keep summary concise. Keep visual_structure concise.',
    'Use no more than 25 regions, 25 calendar entries, 25 tasks, and 25 uncertain items.',
    'Return one complete JSON object and stop only after the closing brace.',
  ].join('\n');

  const second = await requestVisionAttempt({
    images,
    sourceContext: compactContext,
    schema: boundedVisionSchema(schema),
    prompt: recoveryPrompt,
    config,
    numPredict: retryOutput,
  });

  if (second.ok) {
    try {
      appendEvent(config, {
        event: 'vision_structured_output_recovered',
        at: nowIso(),
        source_name: source.name,
        source_sha256: source.sha256 || null,
        first_done_reason: first.done_reason,
        first_eval_count: first.eval_count,
        first_content_length: first.content.length,
        recovery_done_reason: second.done_reason,
        recovery_eval_count: second.eval_count,
        recovery_content_length: second.content.length,
      });
    } catch (_) {}
    return normalizeExtraction(second.raw);
  }

  const diagnostic = {
    first: {
      done: first.done,
      done_reason: first.done_reason,
      eval_count: first.eval_count,
      content_length: first.content.length,
      content_sha256: sha256Text(first.content),
      parse_error: first.error,
    },
    retry: {
      done: second.done,
      done_reason: second.done_reason,
      eval_count: second.eval_count,
      content_length: second.content.length,
      content_sha256: sha256Text(second.content),
      parse_error: second.error,
    },
  };

  throw new TransientError(
    `Ollama returned invalid structured JSON after recovery retry: ${JSON.stringify(diagnostic)}`,
    'vision_invalid_json'
  );
}

function renderList(values, empty = 'None identified.') {
  const items = cleanStrings(values);
  return items.length ? items.map((item) => `- ${item}`).join('\n') : empty;
}

function renderRegions(regions) {
  if (!regions.length) return 'No distinct regions identified.';
  return regions.map((region) => [
    `### ${region.label || region.id}`,
    '',
    `- **Region ID:** \`${region.id}\``,
    `- **Color:** ${region.color || 'Not identified'}`,
    `- **Position:** ${region.position || 'Not identified'}`,
    `- **Confidence:** ${Math.round(region.confidence * 100)}%`,
    '',
    region.text || '[No readable text]',
  ].join('\n')).join('\n\n');
}

function renderCalendar(entries) {
  if (!entries.length) return 'No calendar entries suggested.';
  return entries.map((entry) => [
    `- **${entry.title || 'Untitled entry'}**`,
    `  - Visible date: ${entry.visible_date_text || 'Not visible'}`,
    `  - Normalized date: ${entry.date || 'Unresolved'}`,
    `  - Time: ${entry.start_time || 'Unspecified'}${entry.end_time ? `–${entry.end_time}` : ''}`,
    `  - People: ${entry.people.join(', ') || 'None identified'}`,
    `  - Confidence: ${Math.round(entry.confidence * 100)}%`,
    `  - Evidence: ${entry.evidence || 'Not supplied'}`,
  ].join('\n')).join('\n');
}

function renderTasks(tasks) {
  if (!tasks.length) return 'No tasks suggested.';
  return tasks.map((task) => [
    `- [ ] ${task.text || '[Unclear task]'}`,
    `  - Due date: ${task.due_date || 'Unresolved'}`,
    `  - Assignee: ${task.assignee || 'Unspecified'}`,
    `  - Confidence: ${Math.round(task.confidence * 100)}%`,
    `  - Evidence: ${task.evidence || 'Not supplied'}`,
  ].join('\n')).join('\n');
}

function renderUncertain(items) {
  if (!items.length) return 'No uncertainties reported.';
  return items.map((item) => [
    `- **${item.text || '[unclear]'}** — ${item.reason || 'Uncertain'}`,
    item.alternatives.length
      ? `  - Possible readings: ${item.alternatives.join(' / ')}`
      : '',
  ].filter(Boolean).join('\n')).join('\n');
}

function vaultRelative(target) {
  const normalized = target.replace(/\\/g, '/');
  return normalized.startsWith('/Obsidian/')
    ? normalized.slice('/Obsidian/'.length)
    : normalized.replace(/^\/+/, '');
}

function renderNote(record) {
  const { extraction, source, evidence } = record;
  const quality = extraction.quality;
  const sourceType = record.source_kind === 'screenshot' ? 'screenshot' : 'camera_photo';
  return [
    '---',
    `title: ${yamlString(extraction.title)}`,
    `type: ${yamlString(sourceType === 'screenshot' ? 'screenshot-capture' : 'camera-photo-capture')}`,
    `source_type: ${yamlString(sourceType)}`,
    `screenshot_request_id: ${yamlString(record.screenshot_request_id || '')}`,
    'status: "photo_review"',
    'photo_review_status: "pending"',
    'document_ai_review_status: "pending"',
    `capture_type: ${yamlString(extraction.capture_type)}`,
    `source_filename: ${yamlString(source.name)}`,
    `source_extension: ${yamlString(source.extension)}`,
    `source_sha256: ${yamlString(source.sha256)}`,
    `source_size_bytes: ${source.size}`,
    `source_modified_at: ${yamlString(new Date(source.mtimeMs).toISOString())}`,
    `ingested_at: ${yamlString(evidence.completed_at)}`,
    `ingest_run_id: ${yamlString(evidence.run_id)}`,
    'extractor: "camera_hybrid_ocr_vision"',
    'ocr_applied: true',
    'ocr_language: "eng"',
    `tesseract_status: ${yamlString(evidence.tesseract.status)}`,
    `paddle_ocr_status: ${yamlString(evidence.paddle.status)}`,
    `vision_model: ${yamlString(evidence.vision_model)}`,
    `photo_useful_content: ${extraction.useful_content}`,
    `photo_quality_legibility: ${yamlString(quality.legibility)}`,
    `photo_recapture_recommended: ${quality.recapture_recommended}`,
    `photo_recapture_reason: ${yamlString(quality.recapture_reason)}`,
    `source_preview_vault_path: ${yamlString(record.preview_path)}`,
    `original_archive_path: ${yamlString(record.archive_path)}`,
    `extracted_text_path: ${yamlString(record.extracted_text_path)}`,
    `photo_extraction_json_path: ${yamlString(record.extraction_json_path)}`,
    'entity_action_mode: "advisory_only"',
    'automatic_entity_creation: false',
    'automatic_task_creation: false',
    'automatic_calendar_creation: false',
    '---',
    '',
    `# ${extraction.title}`,
    '',
    '> [!warning] Owner review required',
    '> The transcription, dates, tasks, names, and interpretations below are suggestions. Nothing has been filed, scheduled, or created.',
    '',
    `![[${vaultRelative(record.preview_path)}]]`,
    '',
    '## Capture Summary',
    '',
    `- **Source type:** ${sourceType}`,
    `- **Capture type:** ${extraction.capture_type}`,
    `- **Useful content detected:** ${extraction.useful_content}`,
    `- **Legibility:** ${quality.legibility}`,
    `- **Blur:** ${quality.blur}`,
    `- **Glare:** ${quality.glare}`,
    `- **Perspective:** ${quality.perspective}`,
    `- **Cropped content:** ${quality.cropped_content}`,
    `- **Recapture recommended:** ${quality.recapture_recommended}`,
    `- **Recapture reason:** ${quality.recapture_reason || 'None.'}`,
    '',
    extraction.summary,
    '',
    '## Verbatim Transcription',
    '',
    extraction.verbatim_transcription || '[No dependable transcription.]',
    '',
    '## Cleaned Interpretation',
    '',
    extraction.cleaned_interpretation || '[No cleaned interpretation.]',
    '',
    '## Visual Structure',
    '',
    extraction.visual_structure || 'No special visual structure identified.',
    '',
    '## Detected Regions',
    '',
    renderRegions(extraction.regions),
    '',
    '## Suggested Calendar Entries',
    '',
    '> [!important] Advisory only',
    '> These entries have not been added to a calendar.',
    '',
    renderCalendar(extraction.calendar_entries),
    '',
    '## Suggested Tasks',
    '',
    '> [!important] Advisory only',
    '> These tasks have not been added to the Task Review queue.',
    '',
    renderTasks(extraction.suggested_tasks),
    '',
    '## People',
    '',
    renderList(extraction.people),
    '',
    '## Projects',
    '',
    renderList(extraction.projects),
    '',
    '## Topics',
    '',
    renderList(extraction.topics),
    '',
    '## Uncertainties',
    '',
    renderUncertain(extraction.uncertain_items),
    '',
    '## Processing Evidence',
    '',
    `- **Source SHA-256:** \`${source.sha256}\``,
    `- **Original archive:** \`${record.archive_path}\``,
    `- **Image preprocessing:** ${evidence.preprocessing}`,
    `- **Tesseract:** ${evidence.tesseract.status} (${evidence.tesseract.strategy || 'no strategy'})`,
    `- **PaddleOCR:** ${evidence.paddle.status}`,
    `- **Vision model:** \`${evidence.vision_model}\``,
    `- **Extraction JSON:** \`${record.extraction_json_path}\``,
    `- **Run ID:** \`${evidence.run_id}\``,
    '',
    '## Photo Review',
    '',
    '- Open [[90 System/Queues/Photo Capture Review Queue|Photo Capture Review Queue]].',
    '- Check exactly one decision for this capture.',
    '- Edit this note first if the transcription needs correction.',
    '',
  ].join('\n');
}

function extractionPlainText(extraction) {
  return [
    extraction.title,
    extraction.summary,
    extraction.verbatim_transcription,
    extraction.cleaned_interpretation,
    extraction.visual_structure,
    ...extraction.regions.map((item) => item.text),
    ...extraction.calendar_entries.map((item) =>
      [item.visible_date_text, item.date, item.start_time, item.title, item.evidence]
        .filter(Boolean).join(' | ')
    ),
    ...extraction.suggested_tasks.map((item) =>
      [item.text, item.due_date, item.assignee, item.evidence]
        .filter(Boolean).join(' | ')
    ),
  ].filter(Boolean).join('\n\n');
}

function listCandidates(config, state) {
  ensureDir(config.paths.camera_inbox);
  const supported = new Set(
    config.processing.supported_extensions.map((item) => item.toLowerCase())
  );
  const stableBefore = Date.now() -
    Number(config.processing.minimum_stable_age_seconds || 30) * 1000;
  return fs.readdirSync(config.paths.camera_inbox, { withFileTypes: true })
    .filter((entry) => entry.isFile() && !entry.name.startsWith('.'))
    .map((entry) => {
      const fullPath = path.join(config.paths.camera_inbox, entry.name);
      const stat = fs.statSync(fullPath);
      const screenshotIntake = screenshotIntakeFromName(entry.name);
      const originalName = screenshotIntake?.original_name || entry.name;
      return {
        name: entry.name,
        originalName,
        screenshotRequestId: screenshotIntake?.request_id || null,
        sourceType: screenshotIntake ? 'screenshot' : 'camera_photo',
        fullPath,
        extension: path.extname(originalName).toLowerCase(),
        size: stat.size,
        mtimeMs: stat.mtimeMs,
      };
    })
    .filter((item) => supported.has(item.extension) && item.mtimeMs <= stableBefore)
    .filter((item) => {
      try {
        const hash = sha256File(item.fullPath);
        const retryAfter = state.files[hash]?.retry_after;
        return !retryAfter || Date.parse(retryAfter) <= Date.now();
      } catch (_) {
        return true;
      }
    })
    .sort((left, right) => left.mtimeMs - right.mtimeMs ||
      left.name.localeCompare(right.name));
}

async function processCandidate(candidate, config, state, schema, prompt) {
  const currentRunId = runId();
  const maximumBytes = Number(config.processing.max_source_bytes || 52428800);
  let processingPath = null;
  let workDirectory = null;
  let sourceHash = '';
  try {
    if (candidate.size < 1 || candidate.size > maximumBytes) {
      throw new Error(
        `Source size ${candidate.size} is outside the allowed range 1-${maximumBytes}.`
      );
    }
    sourceHash = sha256File(candidate.fullPath);
    const hash8 = sourceHash.slice(0, 8);
    const existing = state.files[sourceHash];
    if (existing?.completed === true && existing.archive_path &&
        fs.existsSync(existing.archive_path)) {
      const duplicatePath = uniquePath(
        config.paths.duplicates,
        safeName(candidateDisplayName(candidate)),
        hash8
      );
      moveVerified(candidate.fullPath, duplicatePath);
      const result = {
        result: 'duplicate',
        source_name: candidateDisplayName(candidate),
        source_type: candidate.sourceType,
        source_sha256: sourceHash,
        duplicate_path: duplicatePath,
      };
      appendEvent(config, { event: 'duplicate', at: nowIso(), ...result });
      return result;
    }

    ensureDir(config.paths.processing);
    processingPath = uniquePath(
      config.paths.processing,
      `${hash8}_${safeName(candidate.name)}`,
      hash8
    );
    moveVerified(candidate.fullPath, processingPath);
    workDirectory = path.join(config.paths.temp, sourceHash);
    fs.rmSync(workDirectory, { recursive: true, force: true });
    ensureDir(workDirectory);

    const attemptNumber = Number(existing?.attempts || 0) + 1;
    state.files[sourceHash] = {
      ...(state.files[sourceHash] || {}),
      source_name: candidateDisplayName(candidate),
      queue_name: candidate.name,
      source_type: candidate.sourceType,
      screenshot_request_id: candidate.screenshotRequestId,
      source_sha256: sourceHash,
      source_size_bytes: candidate.size,
      extension: candidate.extension,
      status: 'processing',
      attempts: attemptNumber,
      max_retries: 3,
      total_attempts: 4,
      run_id: currentRunId,
      started_at: nowIso(),
      processing_path: processingPath,
      retry_after: null,
      last_error: null,
    };
    state.updated_at = nowIso();
    atomicWriteJson(config.paths.state, state);

    const images = prepareImages(processingPath, workDirectory, config);
    const tesseract = runTesseract(images, config);
    const paddle = await runPaddle(images.natural, config);
    const extraction = await runVision(
      images,
      { tesseract, paddle },
      { ...candidate, sha256: sourceHash },
      schema,
      prompt,
      config
    );
    if (candidate.sourceType === 'screenshot') {
      extraction.capture_type = 'screenshot';
    }

    const minimumUseful = Number(
      config.processing.minimum_meaningful_alphanumeric_characters || 2
    );
    const usableCharacters = alphanumericCount(
      extraction.verbatim_transcription + extraction.cleaned_interpretation
    );
    if (usableCharacters < minimumUseful) extraction.useful_content = false;

    const sourceDate = new Date(candidate.mtimeMs);
    const year = String(sourceDate.getUTCFullYear());
    const month = String(sourceDate.getUTCMonth() + 1).padStart(2, '0');
    const day = String(sourceDate.getUTCDate()).padStart(2, '0');
    const archiveDirectory = path.join(
      config.paths.archive,
      year,
      `${year}-${month}`
    );
    const archivePath = uniquePath(
      archiveDirectory,
      safeName(candidateDisplayName(candidate)),
      hash8
    );
    const previewDirectory = path.join(
      config.paths.preview_attachments,
      year,
      `${year}-${month}`
    );
    const previewExtension = images.normalized ? '.jpg' : candidate.extension;
    const previewPath = path.join(
      previewDirectory,
      `${sourceHash}${previewExtension}`
    );
    const extractionJsonPath = path.join(
      config.paths.extracted,
      `${sourceHash}.photo-extraction.json`
    );
    const extractedTextPath = path.join(
      config.paths.extracted,
      `${sourceHash}.txt`
    );
    const noteFilename = [
      `${year}-${month}-${day}`,
      hash8,
      slugify(extraction.title),
    ].join('_') + '.md';
    const pendingNotePath = path.join(config.paths.pending_notes, noteFilename);
    const completedAt = nowIso();
    const evidence = {
      worker_version: WORKER_VERSION,
      run_id: currentRunId,
      completed_at: completedAt,
      vision_model: config.ollama.model,
      preprocessing: images.preprocessing,
      tesseract,
      paddle,
    };
    const extractionRecord = {
      schema_version: '1.0.0',
      source: {
        name: candidateDisplayName(candidate),
        queue_name: candidate.name,
        source_type: candidate.sourceType,
        screenshot_request_id: candidate.screenshotRequestId,
        extension: candidate.extension,
        sha256: sourceHash,
        size_bytes: candidate.size,
        modified_at: new Date(candidate.mtimeMs).toISOString(),
      },
      extraction,
      evidence,
      safety: config.safety,
    };

    ensureDir(config.paths.extracted);
    atomicWriteJson(extractionJsonPath, extractionRecord);
    atomicWriteText(extractedTextPath, extractionPlainText(extraction) + '\n');
    copyVerified(images.natural, previewPath);

    const note = renderNote({
      extraction,
      source: {
        ...candidate,
        name: candidateDisplayName(candidate),
        sha256: sourceHash,
      },
      source_kind: candidate.sourceType,
      screenshot_request_id: candidate.screenshotRequestId,
      evidence,
      archive_path: archivePath,
      preview_path: previewPath,
      extraction_json_path: extractionJsonPath,
      extracted_text_path: extractedTextPath,
    });
    if (fs.existsSync(pendingNotePath)) {
      const existingText = fs.readFileSync(pendingNotePath, 'utf8');
      if (!existingText.includes(`source_sha256: "${sourceHash}"`)) {
        throw new Error(`Pending-note conflict: ${pendingNotePath}`);
      }
    } else {
      atomicWriteText(pendingNotePath, note);
    }

    moveVerified(processingPath, archivePath);
    processingPath = null;

    state.files[sourceHash] = {
      ...state.files[sourceHash],
      status: 'pending_owner_review',
      completed: true,
      completed_at: completedAt,
      processing_path: null,
      archive_path: archivePath,
      preview_path: previewPath,
      extraction_json_path: extractionJsonPath,
      extracted_text_path: extractedTextPath,
      pending_note_path: pendingNotePath,
      source_type: candidate.sourceType,
      screenshot_request_id: candidate.screenshotRequestId,
      original_source_name: candidateDisplayName(candidate),
      capture_type: extraction.capture_type,
      useful_content: extraction.useful_content,
      recapture_recommended: extraction.quality.recapture_recommended,
      retry_after: null,
      last_error: null,
      completion: {
        marker_version: 1,
        run_id: currentRunId,
        final_note_sha256: sha256File(pendingNotePath),
        archived_source_sha256: sha256File(archivePath),
        write_result: 'atomic_publish_complete',
      },
    };
    state.updated_at = nowIso();
    atomicWriteJson(config.paths.state, state);
    try {
      failureControl([
        'resolve',
        '--pipeline', 'camera_photo',
        '--source-id', sourceHash,
        '--message', candidate.sourceType === 'screenshot' ? 'Screenshot processing completed and is awaiting owner review.' : 'Camera photo processing completed and is awaiting owner review.',
      ]);
    } catch (failureControlError) {
      appendEvent(config, {
        event: 'failure_control_resolve_warning',
        at: nowIso(),
        source_sha256: sourceHash,
        error: failureControlError.message,
      });
    }
    fs.rmSync(workDirectory, { recursive: true, force: true });

    const result = {
      result: 'pending_owner_review',
      run_id: currentRunId,
      source_name: candidateDisplayName(candidate),
      source_type: candidate.sourceType,
      screenshot_request_id: candidate.screenshotRequestId,
      source_sha256: sourceHash,
      capture_type: extraction.capture_type,
      useful_content: extraction.useful_content,
      recapture_recommended: extraction.quality.recapture_recommended,
      pending_note_path: pendingNotePath,
      archive_path: archivePath,
      preview_path: previewPath,
      tesseract_status: tesseract.status,
      paddle_status: paddle.status,
      state_file: config.paths.state,
    };
    appendEvent(config, { event: 'pending_owner_review', at: nowIso(), ...result });
    return result;
  } catch (error) {
    const failureAt = nowIso();
    if (error instanceof TransientError) {
      const attemptNumber = Number(state.files[sourceHash]?.attempts || 1);
      const hasRetriesRemaining = attemptNumber < 4;
      if (hasRetriesRemaining) {
        const retryAfter = new Date(
          Date.now() + retryDelaySeconds(attemptNumber) * 1000
        ).toISOString();
        let returnedPath = candidate.fullPath;
        if (processingPath && fs.existsSync(processingPath)) {
          returnedPath = uniquePath(
            config.paths.camera_inbox,
            safeName(candidate.name),
            sourceHash.slice(0, 8) || 'retry'
          );
          moveVerified(processingPath, returnedPath);
          processingPath = null;
        }
        if (sourceHash) {
          state.files[sourceHash] = {
            ...(state.files[sourceHash] || {}),
            status: 'failed_retryable',
            completed: false,
            processing_path: null,
            retry_after: retryAfter,
            retry_path: returnedPath,
            last_error: error.message,
            last_error_code: error.code,
            last_attempt_at: failureAt,
          };
          state.updated_at = failureAt;
          atomicWriteJson(config.paths.state, state);
          failureControl([
            'register',
            '--pipeline', 'camera_photo',
            '--source-id', sourceHash,
            '--source-name', candidateDisplayName(candidate),
            '--source-sha256', sourceHash,
            '--source-path', returnedPath,
            '--disposition', 'failed_retryable',
            '--attempt', String(attemptNumber),
            '--retry-at', retryAfter,
            '--error', error.message,
          ]);
        }
        if (workDirectory && !config.processing.keep_temp_on_failure) {
          fs.rmSync(workDirectory, { recursive: true, force: true });
        }
        const result = {
          result: 'failed_retryable',
          source_name: candidateDisplayName(candidate),
          source_type: candidate.sourceType,
          screenshot_request_id: candidate.screenshotRequestId,
          source_sha256: sourceHash || null,
          attempt: attemptNumber,
          max_retries: 3,
          retry_after: retryAfter,
          error_code: error.code,
          error: error.message,
        };
        appendEvent(config, { event: result.result, at: failureAt, ...result });
        return result;
      }

      let deadLetterPath = null;
      let isolationFailed = false;
      try {
        const deadLetterDir = path.join(config.paths.failed, 'Dead Letter');
        if (processingPath && fs.existsSync(processingPath)) {
          deadLetterPath = uniquePath(
            deadLetterDir,
            safeName(candidate.name),
            sourceHash.slice(0, 8) || 'failed'
          );
          moveVerified(processingPath, deadLetterPath);
          processingPath = null;
        } else if (fs.existsSync(candidate.fullPath)) {
          deadLetterPath = uniquePath(
            deadLetterDir,
            safeName(candidate.name),
            sourceHash.slice(0, 8) || 'failed'
          );
          moveVerified(candidate.fullPath, deadLetterPath);
        }
      } catch (moveError) {
        isolationFailed = true;
        deadLetterPath = `MOVE_FAILED: ${moveError.message}`;
      }
      state.files[sourceHash] = {
        ...(state.files[sourceHash] || {}),
        status: 'failed_permanent',
        completed: false,
        failed_at: failureAt,
        dead_letter_path: deadLetterPath,
        processing_path: processingPath,
        retry_after: null,
        last_error: error.message,
        last_error_code: error.code,
        last_attempt_at: failureAt,
      };
      state.updated_at = failureAt;
      atomicWriteJson(config.paths.state, state);
      failureControl([
        'register',
        '--pipeline', 'camera_photo',
        '--source-id', sourceHash,
        '--source-name', candidateDisplayName(candidate),
        '--source-sha256', sourceHash,
        '--source-path', processingPath || deadLetterPath || '',
        '--disposition', 'failed_permanent',
        '--attempt', String(attemptNumber),
        '--dead-letter-path', deadLetterPath || '',
        '--error', error.message,
        ...(isolationFailed ? ['--halting'] : []),
      ]);
      if (workDirectory && !config.processing.keep_temp_on_failure) {
        fs.rmSync(workDirectory, { recursive: true, force: true });
      }
      const result = {
        result: 'failed_permanent',
        source_name: candidateDisplayName(candidate),
        source_type: candidate.sourceType,
        screenshot_request_id: candidate.screenshotRequestId,
        source_sha256: sourceHash,
        attempt: attemptNumber,
        dead_letter_path: deadLetterPath,
        isolation_failed: isolationFailed,
        error_code: error.code,
        error: error.message,
      };
      appendEvent(config, { event: result.result, at: failureAt, ...result });
      return result;
    }

    let failedPath = null;
    let isolationFailed = false;
    try {
      const deadLetterDir = path.join(config.paths.failed, 'Dead Letter');
      if (processingPath && fs.existsSync(processingPath)) {
        failedPath = uniquePath(
          deadLetterDir,
          safeName(candidate.name),
          sourceHash.slice(0, 8) || 'failed'
        );
        moveVerified(processingPath, failedPath);
        processingPath = null;
      } else if (fs.existsSync(candidate.fullPath)) {
        failedPath = uniquePath(
          deadLetterDir,
          safeName(candidate.name),
          sourceHash.slice(0, 8) || 'failed'
        );
        moveVerified(candidate.fullPath, failedPath);
      }
    } catch (moveError) {
      isolationFailed = true;
      failedPath = `MOVE_FAILED: ${moveError.message}`;
    }
    const attemptNumber = Number(state.files[sourceHash]?.attempts || 1);
    if (sourceHash) {
      state.files[sourceHash] = {
        ...(state.files[sourceHash] || {}),
        status: 'failed_permanent',
        completed: false,
        failed_at: failureAt,
        dead_letter_path: failedPath,
        processing_path: processingPath,
        retry_after: null,
        last_error: error.message,
      };
      state.updated_at = failureAt;
      atomicWriteJson(config.paths.state, state);
      failureControl([
        'register',
        '--pipeline', 'camera_photo',
        '--source-id', sourceHash,
        '--source-name', candidateDisplayName(candidate),
        '--source-sha256', sourceHash,
        '--source-path', processingPath || failedPath || '',
        '--disposition', 'failed_permanent',
        '--attempt', String(attemptNumber),
        '--dead-letter-path', failedPath || '',
        '--error', error.message,
        ...(isolationFailed ? ['--halting'] : []),
      ]);
    }
    if (workDirectory && !config.processing.keep_temp_on_failure) {
      fs.rmSync(workDirectory, { recursive: true, force: true });
    }
    const result = {
      result: 'failed_permanent',
      source_name: candidateDisplayName(candidate),
      source_type: candidate.sourceType,
      screenshot_request_id: candidate.screenshotRequestId,
      source_sha256: sourceHash || null,
      attempt: attemptNumber,
      dead_letter_path: failedPath,
      isolation_failed: isolationFailed,
      error: error.message,
    };
    appendEvent(config, { event: 'failed_permanent', at: failureAt, ...result });
    return result;
  }
}

async function preflight(config) {
  const directoryChecks = {};
  for (const key of [
    'camera_inbox', 'processing', 'archive', 'failed', 'duplicates',
    'temp', 'extracted', 'pending_notes', 'preview_attachments', 'logs',
  ]) {
    try {
      ensureDir(config.paths[key]);
      fs.accessSync(config.paths[key], fs.constants.R_OK | fs.constants.W_OK);
      directoryChecks[key] = 'pass';
    } catch (error) {
      directoryChecks[key] = `fail:${error.message}`;
    }
  }
  let vision = 'pass';
  try {
    await confirmVisionModel(config);
  } catch (error) {
    vision = `fail:${error.message}`;
  }
  let paddle = 'disabled';
  if (config.ocr.paddle_enabled) {
    try {
      const response = await fetchWithTimeout(
        config.ocr.paddle_health_url,
        { method: 'GET' },
        10
      );
      paddle = response.ok ? 'pass' : `unavailable:http_${response.status}`;
    } catch (error) {
      paddle = `optional_unavailable:${error.message}`;
    }
  }
  const checks = {
    node: process.version,
    imagemagick: commandExists('magick') || commandExists('convert')
      ? 'pass' : 'optional_unavailable_original_image_fallback',
    tesseract: commandExists('tesseract') ? 'pass' : 'unavailable',
    vision,
    paddle,
    vision_schema: fs.existsSync(config.paths.vision_schema) ? 'pass' : 'fail',
    vision_prompt: fs.existsSync(config.paths.vision_prompt) ? 'pass' : 'fail',
    directories: directoryChecks,
  };
  const blocking = [
    checks.vision,
    checks.vision_schema,
    checks.vision_prompt,
    ...Object.values(directoryChecks),
  ].some((value) => String(value).startsWith('fail'));
  return {
    result: blocking ? 'preflight_failed' : 'preflight_passed',
    worker_version: WORKER_VERSION,
    model: config.ollama.model,
    checks,
  };
}

async function processBatch(config, maximum) {
  for (const directory of [
    config.paths.camera_inbox,
    config.paths.processing,
    config.paths.archive,
    config.paths.failed,
    config.paths.duplicates,
    config.paths.temp,
    config.paths.extracted,
    config.paths.pending_notes,
    config.paths.preview_attachments,
    config.paths.logs,
  ]) ensureDir(directory);

  if (!acquireLock(
    config.paths.ingest_lock,
    Number(config.processing.stale_lock_minutes || 90)
  )) {
    return {
      result: 'ingest_locked',
      lock_path: config.paths.ingest_lock,
      items: [],
    };
  }
  try {
    const state = readState(config.paths.state);
    const schema = readJson(config.paths.vision_schema, 'photo-extraction schema');
    const prompt = fs.readFileSync(config.paths.vision_prompt, 'utf8').trim();
    const candidates = listCandidates(config, state).slice(0, maximum);
    const items = [];
    for (const candidate of candidates) {
      const result = await processCandidate(
        candidate,
        config,
        state,
        schema,
        prompt
      );
      items.push(result);
      if (result.result === 'failed_permanent' && result.isolation_failed === true) break;
      if (String(result.failed_path || result.dead_letter_path || '').startsWith('MOVE_FAILED:')) break;
    }
    const counts = {};
    for (const item of items) {
      counts[item.result] = (counts[item.result] || 0) + 1;
    }
    return {
      result: candidates.length ? 'batch_complete' : 'nothing_to_process',
      processed_count: items.length,
      candidate_count: candidates.length,
      max_batch_items: maximum,
      result_counts: counts,
      items,
      state_file: config.paths.state,
    };
  } finally {
    releaseLock(config.paths.ingest_lock);
  }
}

async function main() {
  const args = workflowArguments(process.argv);
  const config = validateConfig(readJson(args.configPath, 'camera-photo config'));
  if (args.command === 'preflight') {
    process.stdout.write(JSON.stringify(await preflight(config)) + '\n');
    return;
  }
  if (args.command !== 'process') fail(`Unknown command: ${args.command}`);
  const maximum = Math.max(
    1,
    Math.min(
      100,
      Number(args.maxBatch || config.processing.max_batch_items || 10)
    )
  );
  process.stdout.write(JSON.stringify(await processBatch(config, maximum)) + '\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message || error}\n`);
  process.exitCode = 1;
});
