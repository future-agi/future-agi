// The trace POST-PROCESSOR: rewrites Playwright trace archives
// (trace.zip and the per-context chunks merged into it) so that test.trace,
// the context *.trace/*.network files, the stacks file and the network
// resources follow the scrub rules (scrub.mjs). Two passes over a SET of
// archives and runner events: the first learns the password values and the
// sensitive-endpoint resources, the second rewrites. Resources of sensitive
// endpoints are removed from the archive, not only unlinked. Every rewritten
// archive carries trace-hygiene.json (counts only).
import {
  lstatSync,
  readdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { collect, newContext, scrubEvent, scrubText } from "./scrub.mjs";
import { readZip, writeZip } from "./zip.mjs";

export const MARKER = "trace-hygiene.json";
const EVENT_ENTRY =
  /(?:^|[/-])(?:test|trace)\.(?:trace|network)$|\.(?:trace|network)$/;
const STACKS_ENTRY = /\.stacks$/;

export const isTraceArchive = (entries) =>
  entries.some((e) => EVENT_ENTRY.test(e.name));
export const isScrubbed = (entries) => entries.some((e) => e.name === MARKER);

function parseLines(data, ctx) {
  const events = [];
  for (const line of data.toString("utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      events.push(JSON.parse(line));
    } catch {
      // Fail closed: a line the scrubber cannot read is not kept.
      ctx.stats.droppedLines = (ctx.stats.droppedLines ?? 0) + 1;
    }
  }
  return events;
}

const resourceName = (name) =>
  name.startsWith("resources/") ? name.slice("resources/".length) : null;

/**
 * Scrub trace archives (buffers) and, optionally, in-memory runner events
 * (the worker's test.trace events, mutated in place) as one set.
 * Returns the rewritten buffers and the counts.
 */
export function scrubTraceSet(buffers, runnerEvents = []) {
  const ctx = newContext();
  const archives = buffers.map((buf) =>
    readZip(buf).map((e) => ({ ...e, events: null })),
  );
  for (const entries of archives)
    for (const e of entries)
      if (EVENT_ENTRY.test(e.name)) e.events = parseLines(e.data, ctx);
  for (const entries of archives)
    for (const e of entries) for (const ev of e.events ?? []) collect(ev, ctx);
  for (const ev of runnerEvents) collect(ev, ctx);

  for (const ev of runnerEvents) scrubEvent(ev, ctx);
  const out = archives.map((entries) => {
    const kept = [];
    for (const e of entries) {
      if (e.name === MARKER) continue;
      const res = resourceName(e.name);
      if (res !== null && ctx.dropped.has(res)) {
        ctx.stats.droppedResources = (ctx.stats.droppedResources ?? 0) + 1;
        continue;
      }
      let data = e.data;
      if (e.events)
        data = Buffer.from(
          e.events.map((ev) => JSON.stringify(scrubEvent(ev, ctx))).join("\n") +
            "\n",
        );
      else if (
        STACKS_ENTRY.test(e.name) ||
        (res !== null && ctx.textResources.has(res))
      )
        data = Buffer.from(scrubText(e.data.toString("utf8"), ctx), "utf8");
      kept.push({ name: e.name, data });
    }
    kept.push({
      name: MARKER,
      data: Buffer.from(
        JSON.stringify({ scrubber: "e2e/lib/trace-hygiene", stats: ctx.stats }),
      ),
    });
    return writeZip(kept);
  });
  return {
    buffers: out,
    stats: { ...ctx.stats, passwordValues: ctx.passwords.size },
  };
}

/** Scrub one trace archive file in place (atomic). False when it is not a trace or already scrubbed. */
export function scrubTraceFile(file, { force = false } = {}) {
  const buf = readFileSync(file);
  const entries = readZip(buf);
  if (!isTraceArchive(entries) || (!force && isScrubbed(entries))) return false;
  const { buffers } = scrubTraceSet([buf]);
  const tmp = `${file}.hygiene-${process.pid}`;
  writeFileSync(tmp, buffers[0]);
  renameSync(tmp, file);
  return true;
}

/**
 * Every *.zip trace archive under `dirs` (a file is its own list) not yet
 * scrubbed (or every one, with force), scrubbed in place.
 * Returns { scrubbed: [...], failed: [...] } (paths); a failure is left for
 * the auditor to refuse.
 */
export function sweepTraceArchives(dirs, { force = false } = {}) {
  const scrubbed = [];
  const failed = [];
  const walk = (p) => {
    let st;
    try {
      st = lstatSync(p);
    } catch {
      return;
    }
    if (st.isSymbolicLink()) return;
    if (st.isDirectory()) {
      for (const child of readdirSync(p)) walk(path.join(p, child));
      return;
    }
    if (!st.isFile() || (!p.endsWith(".zip") && !dirs.includes(p))) return;
    try {
      if (scrubTraceFile(p, { force })) scrubbed.push(p);
    } catch {
      failed.push(p);
    }
  };
  for (const dir of dirs) walk(dir);
  return { scrubbed, failed };
}
