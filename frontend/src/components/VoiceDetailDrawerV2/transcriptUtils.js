// Shared transcript + metrics helpers used by TranscriptView and
// CallAnalyticsView. Keep these pure (no React) so they're trivial to
// unit-test and reuse.

export const formatClock = (seconds) => {
  if (seconds == null || !Number.isFinite(seconds)) return "0:00";
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return `${m}:${String(rest).padStart(2, "0")}`;
};

export const normalizeRole = (role) => {
  const r = String(role || "").toLowerCase();
  if (r === "user" || r === "customer" || r === "human") return "user";
  if (r === "assistant" || r === "agent" || r === "bot") return "assistant";
  if (r === "system") return "system";
  if (r === "tool" || r === "function") return "tool";
  return r || "unknown";
};

export const getContent = (item) => {
  const raw = item?.content ?? item?.message ?? item?.text ?? "";
  if (typeof raw === "string") return raw;
  if (Array.isArray(raw)) {
    return raw
      .map((c) => (typeof c === "string" ? c : c?.text || ""))
      .filter(Boolean)
      .join(" ");
  }
  try {
    return JSON.stringify(raw);
  } catch {
    return "";
  }
};

// Read a numeric field that may live under several names.
export const readNum = (obj, keys) => {
  for (const k of keys) {
    const v = obj?.[k];
    if (v != null && Number.isFinite(Number(v))) return Number(v);
  }
  return null;
};

// Read a time-like field — number (seconds / epoch-ms) or ISO string.
// Strings parse via Date and return epoch-ms; the epoch-norm pass in
// enrichTurns later rebases everything to offsets.
export const readTime = (obj, keys) => {
  for (const k of keys) {
    const v = obj?.[k];
    if (v == null) continue;
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string") {
      const asNum = Number(v);
      if (Number.isFinite(asNum) && /^\s*-?\d+(\.\d+)?\s*$/.test(v)) {
        return asNum;
      }
      const parsed = Date.parse(v);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
};

/**
 * Enrich the raw transcript with silence, overlap, and duration info.
 *
 * Handles the two voice backends we ship:
 *   • Simulate voice — `startTimeSeconds` / `endTimeSeconds` as Unix epoch
 *     milliseconds (field names lie). `duration` (when present) also ms.
 *   • Observe voice — `time` as an ISO-8601 string, `duration` as a float
 *     in seconds, no end field.
 *
 * Returns sorted, normalized turns where:
 *   start/end  → seconds from call start
 *   duration   → seconds
 *   silenceBefore → seconds (null if < 0.3s or indeterminate)
 *   overlapsPrev  → true when this turn started before the previous ended
 */
export const enrichTurns = (transcript) => {
  if (!Array.isArray(transcript) || transcript.length === 0) return [];

  // Step 1: raw extract
  const raw = transcript.map((item, i) => {
    const role = normalizeRole(item.speakerRole || item.role);
    const start = readTime(item, [
      "startTimeSeconds",
      "start_time_seconds",
      "startTime",
      "start_time",
      "start",
      "time",
      "timeStamp",
      "timestamp",
    ]);
    const end = readTime(item, [
      "endTimeSeconds",
      "end_time_seconds",
      "endTime",
      "end_time",
      "end",
    ]);
    const directDuration = readNum(item, [
      "duration",
      "durationSeconds",
      "duration_seconds",
    ]);
    return {
      id: item.id ?? `turn-${i}`,
      role,
      rawRole: item.speakerRole || item.role,
      content: getContent(item),
      start,
      end,
      duration: directDuration,
      _originalIndex: i,
    };
  });

  // Step 2: absolute-epoch → offset-seconds normalization. Transcripts
  // arrive with times in one of three units depending on the backend:
  //   • ms since epoch (simulate voice, ~1.7e12 in 2026)
  //   • s  since epoch (observe voice from some providers, ~1.7e9 in 2026)
  //   • s  since call start (already relative, < ~1 hour == 3600s)
  // Detect by magnitude of the earliest start and rebase accordingly.
  // The old >1e10 threshold only caught ms; epoch-seconds slipped through
  // and rendered as raw numbers (e.g. "29608380:45" in the KPI strip).
  let epochMinStart = Infinity;
  for (const t of raw) {
    if (t.start != null && t.start < epochMinStart) epochMinStart = t.start;
  }
  if (epochMinStart !== Infinity) {
    if (epochMinStart >= 1e12) {
      // Epoch ms — subtract base and convert to seconds.
      raw.forEach((t) => {
        if (t.start != null) t.start = (t.start - epochMinStart) / 1000;
        if (t.end != null) t.end = (t.end - epochMinStart) / 1000;
      });
    } else if (epochMinStart >= 1e9) {
      // Epoch seconds — subtract base only.
      raw.forEach((t) => {
        if (t.start != null) t.start = t.start - epochMinStart;
        if (t.end != null) t.end = t.end - epochMinStart;
      });
    }
  }

  // Step 3: per-row duration unit detection. Anything > 300 is ms.
  raw.forEach((t) => {
    if (t.duration != null && t.duration > 300) t.duration /= 1000;
  });

  // Step 4: synthesize sequential positions when nothing has a start
  // time but durations are known.
  const hasAnyStart = raw.some((t) => t.start != null);
  const hasAnyDuration = raw.some((t) => t.duration != null);
  if (!hasAnyStart && hasAnyDuration) {
    let cursor = 0;
    raw.forEach((t) => {
      const d = t.duration != null ? t.duration : 0;
      t.start = cursor;
      t.end = cursor + d;
      cursor += d;
    });
  }

  // Step 5: back-fill `end` from start + duration, and `duration` from
  // end - start. Observe rows don't ship an explicit end (without this,
  // silence/overlap pass skips every pair). Simulate rows ship start +
  // end but no duration (without this, computeTotals skips every turn
  // → talk-time split renders as "—").
  raw.forEach((t) => {
    if (t.end == null && t.start != null && t.duration != null) {
      t.end = t.start + t.duration;
    } else if (t.duration == null && t.start != null && t.end != null) {
      t.duration = t.end - t.start;
    }
  });

  // Step 6: sort by start time (stable fallback to original index).
  const sorted = [...raw].sort((a, b) => {
    if (a.start == null && b.start == null) {
      return a._originalIndex - b._originalIndex;
    }
    if (a.start == null) return 1;
    if (b.start == null) return -1;
    return a.start - b.start;
  });

  // Step 7: silence + overlap on adjacent pairs.
  for (let i = 0; i < sorted.length; i++) {
    const cur = sorted[i];
    const prev = sorted[i - 1];
    if (!prev || prev.end == null || cur.start == null) {
      cur.silenceBefore = null;
      cur.overlapsPrev = false;
      continue;
    }
    const gap = cur.start - prev.end;
    if (gap < -0.1) {
      cur.silenceBefore = 0;
      cur.overlapsPrev = true;
    } else {
      cur.silenceBefore = gap > 0.3 ? gap : null;
      cur.overlapsPrev = false;
    }
  }
  return sorted;
};

export const computeTotals = (turns) => {
  const byRole = {};
  let total = 0;
  for (const t of turns) {
    if (t.duration == null) continue;
    byRole[t.role] = (byRole[t.role] || 0) + t.duration;
    total += t.duration;
  }
  return { byRole, total };
};

const countWords = (text) => {
  if (!text || typeof text !== "string") return 0;
  return text.trim().split(/\s+/).filter(Boolean).length;
};

/**
 * Aggregate call-level metrics from an already-enriched turn list.
 * Used by the KPI strip in CallAnalyticsView. Everything is optional —
 * returns null on the corresponding key when the source data is missing.
 */
export const computeCallMetrics = (turns) => {
  if (!Array.isArray(turns) || turns.length === 0) {
    return {
      duration: null,
      turnCount: 0,
      wordCount: 0,
      userTalkPct: null,
      assistantTalkPct: null,
      interruptionCount: 0,
      silenceTotal: 0,
      silenceCount: 0,
      timeToFirstWord: null,
    };
  }

  const userTurns = turns.filter((t) => t.role === "user");
  const assistantTurns = turns.filter((t) => t.role === "assistant");

  let wordCount = 0;
  turns.forEach((t) => {
    if (t.role === "user" || t.role === "assistant") {
      wordCount += countWords(t.content);
    }
  });

  let lastEnd = 0;
  turns.forEach((t) => {
    if (t.end != null && t.end > lastEnd) lastEnd = t.end;
    else if (t.start != null && t.duration != null) {
      const e = t.start + t.duration;
      if (e > lastEnd) lastEnd = e;
    }
  });

  const totals = computeTotals(turns);
  const userTotal = totals.byRole.user || 0;
  const assistantTotal = totals.byRole.assistant || 0;
  const totalSpeech = userTotal + assistantTotal;

  let silenceTotal = 0;
  let silenceCount = 0;
  let interruptionCount = 0;
  turns.forEach((t) => {
    if (t.silenceBefore != null && t.silenceBefore > 0) {
      silenceTotal += t.silenceBefore;
      silenceCount += 1;
    }
    if (t.overlapsPrev) interruptionCount += 1;
  });

  // Time to first word: earliest start among non-system turns. For voice
  // drive-thru-style calls this is how long before the agent greets you.
  let ttfw = null;
  for (const t of turns) {
    if (t.role === "user" || t.role === "assistant") {
      if (t.start != null && (ttfw == null || t.start < ttfw)) {
        ttfw = t.start;
      }
    }
  }


  return {
    duration: lastEnd > 0 ? lastEnd : null,
    turnCount: userTurns.length + assistantTurns.length,
    wordCount,
    userTalkPct:
      totalSpeech > 0 ? Math.round((userTotal / totalSpeech) * 100) : null,
    assistantTalkPct:
      totalSpeech > 0 ? Math.round((assistantTotal / totalSpeech) * 100) : null,
    interruptionCount,
    silenceTotal,
    silenceCount,
    timeToFirstWord: ttfw,
  };
};
