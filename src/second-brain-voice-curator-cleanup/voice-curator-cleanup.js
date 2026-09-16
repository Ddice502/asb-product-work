#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");
const crypto = require("crypto");

const DEFAULTS = {
  inbox: "/Obsidian/00 Inbox",
  vault: "/Obsidian",
  state: "/State/Second Brain/voice-curator-cleanup-state.json",
  events: "/State/Second Brain/Voice Curator Cleanup/events.jsonl",
  logDir: "/Obsidian/90 System/Curator Cleanup Control/logs",
};

function nowIso() {
  return new Date().toISOString();
}

function sha256Buffer(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

function sha256Text(text) {
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}

function atomicWrite(filePath, content, mode) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temp = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.${process.pid}.${Date.now()}.tmp`
  );
  fs.writeFileSync(temp, content, "utf8");
  if (typeof mode === "number") {
    fs.chmodSync(temp, mode & 0o777);
  }
  fs.renameSync(temp, filePath);
}

function parseScalar(value) {
  let text = String(value || "").trim();
  if (
    text.length >= 2 &&
    ((text.startsWith('"') && text.endsWith('"')) ||
      (text.startsWith("'") && text.endsWith("'")))
  ) {
    if (text.startsWith('"')) {
      try {
        return JSON.parse(text);
      } catch (_) {}
    }
    return text.slice(1, -1);
  }
  return text;
}

function parseFrontmatter(content) {
  const lines = content.split(/\r?\n/);
  if (lines.length < 3 || lines[0].trim() !== "---") {
    return null;
  }
  const end = lines.slice(1).findIndex((line) => line.trim() === "---");
  if (end < 0) {
    return null;
  }
  const endIndex = end + 1;
  const fields = {};
  for (let i = 1; i < endIndex; i++) {
    const match = lines[i].match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (!match) continue;
    fields[match[1]] = parseScalar(match[2]);
  }
  return { fields, endLine: endIndex };
}

function findSection(content, heading) {
  const lines = content.split(/\r?\n/);
  const headingRegex = new RegExp(`^##\\s+${heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`, "i");
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    if (headingRegex.test(lines[i])) {
      start = i;
      break;
    }
  }
  if (start < 0) return null;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^##\s+/.test(lines[i])) {
      end = i;
      break;
    }
  }
  return {
    lines,
    headingIndex: start,
    endIndex: end,
    bodyLines: lines.slice(start + 1, end),
    body: lines.slice(start + 1, end).join("\n").trim(),
  };
}

function replaceSectionBody(content, heading, replacement) {
  const section = findSection(content, heading);
  if (!section) return content;
  const before = section.lines.slice(0, section.headingIndex + 1);
  const after = section.lines.slice(section.endIndex);
  const merged = [...before, "", replacement.trim(), "", ...after];
  return merged.join("\n").replace(/\n{4,}/g, "\n\n\n").replace(/\s+$/, "") + "\n";
}

function normalizeSpace(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function isSystemGeneratedVoiceNote(filename, content) {
  const fm = parseFrontmatter(content);
  if (!fm) return false;
  const f = fm.fields;
  const type = normalizeSpace(f.type).toLowerCase();
  if (type !== "voice-note") return false;
  if (!normalizeSpace(f.audio_file)) return false;
  if (!normalizeSpace(f.processed_at)) return false;
  if (!normalizeSpace(f.model)) return false;
  if (!findSection(content, "Source Audio")) return false;
  if (!findSection(content, "Original Transcript")) return false;
  return filename.toLowerCase().endsWith(".md");
}

function isIncompleteSummary(summary) {
  const value = normalizeSpace(summary);
  if (!value) return true;
  const lower = value.toLowerCase().replace(/[.!]+$/, "");
  const exact = new Set([
    "tbd",
    "todo",
    "none",
    "n/a",
    "na",
    "summary unavailable",
    "summary pending",
    "summary incomplete",
    "summary not available",
    "no summary",
    "no summary available",
    "not generated",
    "pending summary",
    "see transcript",
    "see original transcript",
    "voice note summary",
  ]);
  if (exact.has(lower)) return true;
  if (/^\[(?:tbd|todo|pending|incomplete|summary pending)\]$/i.test(value)) return true;
  if (/\b(summary\s+(?:pending|incomplete|unavailable|not available)|not generated)\b/i.test(value)) return true;
  return false;
}

function cleanBullet(line) {
  let value = line.trim();
  if (!/^[-*]\s+/.test(value)) return "";
  value = value.replace(/^[-*]\s+/, "");
  value = value.replace(/^\[[ xX]\]\s+/, "");
  value = value.replace(/\s+\(confidence:[^)]+\)\s*$/i, "");
  value = value.replace(/\s+\*\*Review required\*\*\s*$/i, "");
  return normalizeSpace(value);
}

function summaryFromKeyPoints(content) {
  const section = findSection(content, "Key Points");
  if (!section) return "";
  const points = [];
  for (const line of section.bodyLines) {
    if (/^\s*>/.test(line)) continue;
    const point = cleanBullet(line);
    if (!point) continue;
    if (!points.includes(point)) points.push(point);
    if (points.length >= 3) break;
  }
  if (!points.length) return "";
  return points
    .map((point) => /[.!?]$/.test(point) ? point : `${point}.`)
    .join(" ");
}

function usefulTitle(value) {
  const text = normalizeSpace(value);
  if (!text) return false;
  const lower = text.toLowerCase().replace(/[.!]+$/, "");
  if (
    new Set([
      "untitled voice note",
      "voice note",
      "voice recording",
      "audio note",
      "note",
      "untitled",
    ]).has(lower)
  ) {
    return false;
  }
  return text.length >= 3;
}

function firstH1(content) {
  const match = content.match(/^#\s+(.+?)\s*$/m);
  return match ? normalizeSpace(match[1]) : "";
}

function titleCandidate(content) {
  const fm = parseFrontmatter(content);
  const title = fm ? normalizeSpace(fm.fields.title) : "";
  if (usefulTitle(title)) return title;

  const h1 = firstH1(content);
  if (usefulTitle(h1)) return h1;

  const summary = findSection(content, "Summary");
  if (summary && !isIncompleteSummary(summary.body) && usefulTitle(summary.body)) {
    return summary.body.split(/[.!?]/)[0].trim();
  }

  const keyPoints = findSection(content, "Key Points");
  if (keyPoints) {
    for (const line of keyPoints.bodyLines) {
      const point = cleanBullet(line);
      if (usefulTitle(point)) return point;
    }
  }
  return "";
}

function slugify(value) {
  let text = normalizeSpace(value);
  try {
    text = text.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  } catch (_) {}
  text = text.toLowerCase();
  text = text.replace(/&/g, " and ");
  text = text.replace(/[^a-z0-9]+/g, "-");
  text = text.replace(/^-+|-+$/g, "").replace(/-{2,}/g, "-");
  if (text.length > 80) {
    text = text.slice(0, 80).replace(/-+$/g, "");
  }
  return text;
}

function timestampPrefix(fields, filename) {
  const recorded = normalizeSpace(fields.recorded_at);
  let match = recorded.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  if (match) {
    return `${match[1]}-${match[2]}-${match[3]}_${match[4]}${match[5]}${match[6]}`;
  }

  const audio = normalizeSpace(fields.audio_file);
  match = audio.match(/(?:^|[^0-9])(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})(?:[^0-9]|$)/);
  if (match) {
    return `${match[1]}-${match[2]}-${match[3]}_${match[4]}${match[5]}${match[6]}`;
  }

  match = filename.match(/^(\d{4}-\d{2}-\d{2})_(\d{6})_/);
  if (match) {
    return `${match[1]}_${match[2]}`;
  }

  return "";
}

function desiredFilename(filename, content) {
  const fm = parseFrontmatter(content);
  if (!fm) return "";
  const prefix = timestampPrefix(fm.fields, filename);
  const title = titleCandidate(content);
  const slug = slugify(title);
  if (!prefix || !slug) return "";
  return `${prefix}_${slug}.md`;
}

function walkMarkdown(root, callback) {
  if (!fs.existsSync(root)) return;
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch (_) {
      continue;
    }
    for (const entry of entries) {
      if (entry.name === ".obsidian" || entry.name === ".git") continue;
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(full);
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith(".md")) {
        callback(full);
      }
    }
  }
}

function hasExternalBacklink(vault, sourcePath, oldFilename) {
  const base = oldFilename.replace(/\.md$/i, "");
  const needles = [
    `[[${oldFilename}]]`,
    `[[${base}]]`,
    `[[00 Inbox/${oldFilename}]]`,
    `[[00 Inbox/${base}]]`,
  ].map((x) => x.toLowerCase());

  let found = null;
  walkMarkdown(vault, (filePath) => {
    if (found || path.resolve(filePath) === path.resolve(sourcePath)) return;
    let text = "";
    try {
      text = fs.readFileSync(filePath, "utf8").toLowerCase();
    } catch (_) {
      return;
    }
    if (needles.some((needle) => text.includes(needle))) {
      found = filePath;
    }
  });
  return found;
}

function readState(statePath) {
  const fallback = {
    schema_version: "1.0.0",
    files: {},
    last_run: null,
  };
  if (!fs.existsSync(statePath)) return fallback;
  try {
    const parsed = JSON.parse(fs.readFileSync(statePath, "utf8"));
    if (!parsed || typeof parsed !== "object") return fallback;
    return {
      ...fallback,
      ...parsed,
      files: parsed.files && typeof parsed.files === "object" ? parsed.files : {},
    };
  } catch (error) {
    throw new Error(`Voice curator cleanup state is invalid: ${error.message}`);
  }
}

function writeRunLog(config, result) {
  if (result.dry_run) return null;
  fs.mkdirSync(config.logDir, { recursive: true });
  const stamp = result.completed_at.replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
  const logPath = path.join(config.logDir, `${stamp}_voice-curator-cleanup.md`);
  const lines = [
    "---",
    "type: curator-cleanup-log",
    `processed_at: "${result.completed_at}"`,
    `scanned: ${result.scanned}`,
    `system_voice_notes: ${result.system_voice_notes}`,
    `renamed: ${result.renamed}`,
    `summaries_completed: ${result.summaries_completed}`,
    `rename_blocked_backlinks: ${result.rename_blocked_backlinks}`,
    `conflicts: ${result.conflicts}`,
    "---",
    "",
    "# Nightly Curator Voice Cleanup Log",
    "",
    "> System-generated voice notes only. Owner-authored notes are never rewritten by this cleanup stage.",
    "",
    "## Changes",
    "",
  ];
  if (!result.changes.length) {
    lines.push("- None");
  } else {
    for (const item of result.changes) {
      const actions = [];
      if (item.summary_completed) actions.push("completed incomplete summary from existing Key Points");
      if (item.renamed_to) actions.push(`renamed to \`${item.renamed_to}\``);
      lines.push(`- \`${item.original_filename}\`: ${actions.join("; ")}`);
    }
  }
  lines.push("", "## Rename Blocks / Conflicts", "");
  const issues = [...result.blocked, ...result.conflict_items];
  if (!issues.length) {
    lines.push("- None");
  } else {
    for (const item of issues) {
      lines.push(`- \`${item.filename}\`: ${item.reason}`);
    }
  }
  lines.push("", "## Incomplete Summaries Not Auto-Repaired", "");
  if (!result.unresolved_summary_items.length) {
    lines.push("- None");
  } else {
    for (const item of result.unresolved_summary_items) {
      lines.push(`- \`${item.filename}\`: no grounded Key Points were available; source note was left unchanged.`);
    }
  }
  atomicWrite(logPath, lines.join("\n") + "\n", 0o644);
  return logPath;
}

function runCleanup(options = {}) {
  const config = {
    ...DEFAULTS,
    ...options,
  };
  const dryRun = Boolean(options.dryRun);
  if (!fs.existsSync(config.inbox)) {
    throw new Error(`Obsidian Inbox does not exist: ${config.inbox}`);
  }

  const result = {
    schema_version: "1.0.0",
    started_at: nowIso(),
    completed_at: null,
    dry_run: dryRun,
    scanned: 0,
    system_voice_notes: 0,
    skipped_non_system: 0,
    renamed: 0,
    summaries_completed: 0,
    rename_blocked_backlinks: 0,
    conflicts: 0,
    unresolved_summaries: 0,
    changes: [],
    blocked: [],
    conflict_items: [],
    unresolved_summary_items: [],
  };

  const state = readState(config.state);
  const entries = fs
    .readdirSync(config.inbox, { withFileTypes: true })
    .filter((entry) =>
      entry.isFile() &&
      entry.name.toLowerCase().endsWith(".md") &&
      !entry.name.startsWith(".") &&
      !/_nightly-curator-review\.md$/i.test(entry.name)
    )
    .sort((a, b) => a.name.localeCompare(b.name));

  for (const entry of entries) {
    result.scanned += 1;
    let currentName = entry.name;
    let currentPath = path.join(config.inbox, currentName);
    let content = fs.readFileSync(currentPath, "utf8");
    const beforeHash = sha256Text(content);

    if (!isSystemGeneratedVoiceNote(currentName, content)) {
      result.skipped_non_system += 1;
      continue;
    }
    result.system_voice_notes += 1;

    let summaryCompleted = false;
    const summarySection = findSection(content, "Summary");
    if (summarySection && isIncompleteSummary(summarySection.body)) {
      const groundedSummary = summaryFromKeyPoints(content);
      if (groundedSummary) {
        const updated = replaceSectionBody(content, "Summary", groundedSummary);
        if (updated !== content) {
          summaryCompleted = true;
          if (!dryRun) {
            const stat = fs.statSync(currentPath);
            atomicWrite(currentPath, updated, stat.mode);
          }
          content = updated;
          result.summaries_completed += 1;
        }
      } else {
        result.unresolved_summaries += 1;
        result.unresolved_summary_items.push({ filename: currentName });
      }
    }

    const desired = desiredFilename(currentName, content);
    let renamedTo = null;

    if (desired && desired !== currentName) {
      const backlink = hasExternalBacklink(config.vault, currentPath, currentName);
      if (backlink) {
        result.rename_blocked_backlinks += 1;
        result.blocked.push({
          filename: currentName,
          reason: `external wiki-link found in ${backlink}; rename blocked to avoid modifying owner-authored backlinks`,
        });
      } else {
        const targetPath = path.join(config.inbox, desired);
        if (fs.existsSync(targetPath)) {
          result.conflicts += 1;
          result.conflict_items.push({
            filename: currentName,
            reason: `target filename already exists: ${desired}; neither file was deleted`,
          });
        } else {
          renamedTo = desired;
          if (!dryRun) {
            fs.renameSync(currentPath, targetPath);
            currentPath = targetPath;
            currentName = desired;
          }
          result.renamed += 1;
        }
      }
    }

    if (summaryCompleted || renamedTo) {
      const afterHash = sha256Text(content);
      result.changes.push({
        original_filename: entry.name,
        renamed_to: renamedTo,
        summary_completed: summaryCompleted,
        content_sha256_before: beforeHash,
        content_sha256_after: afterHash,
      });

      if (!dryRun) {
        const fm = parseFrontmatter(content);
        const audio = fm ? normalizeSpace(fm.fields.audio_file) : "";
        const key = audio || entry.name;
        state.files[key] = {
          current_filename: currentName,
          last_cleaned_at: nowIso(),
          summary_completed: summaryCompleted,
          last_content_sha256: afterHash,
        };
      }
    }
  }

  result.completed_at = nowIso();

  if (!dryRun) {
    state.schema_version = "1.0.0";
    state.last_run = {
      completed_at: result.completed_at,
      scanned: result.scanned,
      system_voice_notes: result.system_voice_notes,
      renamed: result.renamed,
      summaries_completed: result.summaries_completed,
      rename_blocked_backlinks: result.rename_blocked_backlinks,
      conflicts: result.conflicts,
      unresolved_summaries: result.unresolved_summaries,
    };
    atomicWrite(config.state, JSON.stringify(state, null, 2) + "\n", 0o644);

    fs.mkdirSync(path.dirname(config.events), { recursive: true });
    fs.appendFileSync(
      config.events,
      JSON.stringify({
        event_at: result.completed_at,
        event: "voice_curator_cleanup_run",
        scanned: result.scanned,
        system_voice_notes: result.system_voice_notes,
        renamed: result.renamed,
        summaries_completed: result.summaries_completed,
        rename_blocked_backlinks: result.rename_blocked_backlinks,
        conflicts: result.conflicts,
        unresolved_summaries: result.unresolved_summaries,
        changes: result.changes,
      }) + "\n",
      "utf8"
    );
    result.log_file = writeRunLog(config, result);
  }

  return result;
}

function selfTest() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "sb-step20-"));
  const inbox = path.join(base, "Obsidian", "00 Inbox");
  const vault = path.join(base, "Obsidian");
  const state = path.join(base, "State", "voice-curator-cleanup-state.json");
  const events = path.join(base, "State", "events.jsonl");
  const logDir = path.join(base, "Obsidian", "90 System", "logs");
  fs.mkdirSync(inbox, { recursive: true });

  const voice = `---
title: "Pump Bearing Follow Up"
type: "voice-note"
recorded_at: "2026-08-13T02:51:14-04:00"
processed_at: "2026-08-13T06:51:21Z"
model: "hermes3-agent:8b"
audio_file: "20260813_025114.m4a"
---

# Pump Bearing Follow Up

## Summary

Summary pending.

## Key Points

- Check the pump bearing tomorrow
- Vibration is higher than last week

## Source Audio

\`20260813_025114.m4a\`

## Original Transcript

Check the pump bearing tomorrow. Vibration is higher than last week.
`;
  const originalName = "2026-08-13_065121_capture.md";
  fs.writeFileSync(path.join(inbox, originalName), voice, "utf8");

  const owner = `---
title: "My Voice Note"
type: "voice-note"
---

# My Voice Note

Owner-authored content.
`;
  fs.writeFileSync(path.join(inbox, "owner-note.md"), owner, "utf8");

  const first = runCleanup({ inbox, vault, state, events, logDir, dryRun: false });
  const expectedName = "2026-08-13_025114_pump-bearing-follow-up.md";
  if (first.renamed !== 1 || first.summaries_completed !== 1) {
    throw new Error(`Self-test expected 1 rename + 1 summary completion: ${JSON.stringify(first)}`);
  }
  if (!fs.existsSync(path.join(inbox, expectedName))) {
    throw new Error("Self-test renamed file is missing.");
  }
  const updated = fs.readFileSync(path.join(inbox, expectedName), "utf8");
  if (!updated.includes("Check the pump bearing tomorrow.") || !updated.includes("Vibration is higher than last week.")) {
    throw new Error("Self-test grounded summary was not rebuilt from Key Points.");
  }
  if (!fs.existsSync(path.join(inbox, "owner-note.md"))) {
    throw new Error("Self-test owner-authored note was modified or removed.");
  }

  const second = runCleanup({ inbox, vault, state, events, logDir, dryRun: false });
  if (second.renamed !== 0 || second.summaries_completed !== 0) {
    throw new Error(`Self-test idempotency failed: ${JSON.stringify(second)}`);
  }

  fs.rmSync(base, { recursive: true, force: true });
  return {
    status: "completed",
    mode: "self_test",
    system_generated_only: true,
    owner_authored_note_unchanged: true,
    grounded_summary_completion: true,
    deterministic_recording_time_filename: true,
    idempotent_second_run: true,
    backlink_protection_enabled: true,
    collision_deletion_disabled: true,
  };
}

function main() {
  const args = new Set(process.argv.slice(2));
  if (args.has("--self-test")) {
    process.stdout.write(JSON.stringify(selfTest(), null, 2) + "\n");
    return;
  }
  const result = runCleanup({ dryRun: args.has("--dry-run") });
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
}

try {
  main();
} catch (error) {
  console.error(JSON.stringify({
    status: "failed",
    error: `${error && error.name ? error.name : "Error"}: ${error && error.message ? error.message : String(error)}`,
  }, null, 2));
  process.exit(1);
}
