import { enrichTurns } from "../VoiceDetailDrawerV2/transcriptUtils";

// Bounded: a drawer never walks or renders an unbounded tree on render.
const MAX_SPANS = 5000;
export const MAX_ACTIVITY = 200;

// A complete ISO date and time, optionally zoned. Anything looser ("12/31",
// a bare date) is not a stated instant, whatever Date.parse makes of it.
const ISO =
  /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}(:?\d{2})?)?$/i;
const ZONED = /(Z|[+-]\d{2}(:?\d{2})?)$/i;
// ClickHouse times are UTC; the API may omit the zone designator.
export function utcMs(value) {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!ISO.test(text)) return null;
  const iso = text.replace(" ", "T");
  const ms = Date.parse(ZONED.test(iso) ? iso : `${iso}Z`);
  return Number.isFinite(ms) ? ms : null;
}

// A transcript row's wall-clock time, only when the row states one: an
// ISO time or an epoch value. Call-relative seconds are not wall clock.
const WALL_KEYS = [
  "timestamp",
  "timeStamp",
  "time",
  "start_time",
  "startTime",
  "start",
  "startTimeSeconds",
  "start_time_seconds",
];
export function turnWallMs(turn) {
  for (const key of WALL_KEYS) {
    const value = turn?.[key];
    if (typeof value === "string" && !/^\s*-?\d+(\.\d+)?\s*$/.test(value)) {
      const ms = utcMs(value);
      if (ms !== null) return ms;
      continue;
    }
    const n = typeof value === "string" ? Number(value) : value;
    if (typeof n === "number" && Number.isFinite(n)) {
      if (n >= 1e12) return n;
      if (n >= 1e9) return n * 1000;
    }
  }
  return null;
}

// Rows a tool can sit between: what someone said. System prompts, tool
// results and actor actions are not speech, timed or not.
const NOT_SPEECH = new Set(["system", "tool", "world_actor"]);
const isSpeechRole = (role) => !NOT_SPEECH.has(role);

/**
 * The transcript with a stable id on every row, so activity can be anchored
 * to the turn the view renders. Supplied ids are kept; a generated id never
 * repeats one of them. Returns new row objects only where an id was added.
 */
export function withTurnIds(transcript) {
  const rows = Array.isArray(transcript) ? transcript : [];
  const taken = new Set(
    rows
      .filter((row) => row && typeof row === "object" && row.id != null)
      .map((row) => String(row.id)),
  );
  return rows.map((row, i) => {
    if (!row || typeof row !== "object" || row.id != null) return row;
    let id = `call-turn-${i}`;
    for (let n = 1; taken.has(id); n += 1) id = `call-turn-${i}-${n}`;
    taken.add(id);
    return { ...row, id };
  });
}

function findEntry(tree, id) {
  const stack = Array.isArray(tree) ? [...tree] : [];
  for (let seen = 0; stack.length && seen < MAX_SPANS; seen += 1) {
    const entry = stack.pop();
    if (entry?.observation_span?.id === id) return entry;
    if (Array.isArray(entry?.children)) stack.push(...entry.children);
  }
  return null;
}

function descendants(root) {
  const found = [];
  const stack = Array.isArray(root?.children) ? [...root.children] : [];
  for (let seen = 0; stack.length && seen < MAX_SPANS; seen += 1) {
    const entry = stack.pop();
    if (entry?.observation_span) found.push(entry.observation_span);
    if (Array.isArray(entry?.children)) stack.push(...entry.children);
  }
  return found;
}

const text = (value) =>
  typeof value === "string" && value.trim() ? value.trim() : null;
const attributesOf = (span) =>
  span.span_attributes && typeof span.span_attributes === "object"
    ? span.span_attributes
    : {};

// A tool invocation: a span typed as a tool, or LiveKit's own function-tool
// span, which names the invoked tool even when nothing typed it.
const isToolSpan = (span) =>
  span.id != null &&
  (String(span.observation_type || "").toLowerCase() === "tool" ||
    text(attributesOf(span)["lk.function_tool.name"]) !== null);

// Only identity, kind and measured timing leave the span: never its input,
// output, arguments or results.
function toolProjection(span, origin) {
  const attrs = attributesOf(span);
  const start = utcMs(span.start_time);
  const end = utcMs(span.end_time);
  return {
    key: String(span.id),
    spanId: String(span.id),
    name:
      text(attrs["gen_ai.tool.name"]) ||
      text(attrs["lk.function_tool.name"]) ||
      text(attrs["tool.name"]) ||
      text(attrs["tool_call.function.name"]) ||
      text(span.name) ||
      "Unnamed tool",
    // MCP only with protocol evidence on the span itself.
    mcp: Object.keys(attrs).some((key) => key.startsWith("mcp.")),
    failed:
      String(span.status || "").toUpperCase() === "ERROR" ||
      attrs["lk.function_tool.is_error"] === true,
    wallMs: start,
    startMs: start === null ? null : start - origin,
    // A finish only from a recorded end at or after the start; a bare
    // latency of zero is not a measured duration.
    endMs: start !== null && end !== null && end >= start ? end - origin : null,
  };
}

/**
 * The speech turns in the order the transcript view shows them, each with
 * its stated wall clock, or null when they cannot be placed on that clock:
 * a turn without one, or a display order the wall clock contradicts.
 */
function speechOnWallClock(rows) {
  // The same rows and ordering the Conversation transcript renders.
  const shown = rows.filter(
    (row) => row && typeof row === "object" && row.speaker_role !== "system",
  );
  const wall = new Map(shown.map((row) => [String(row.id), turnWallMs(row)]));
  const speech = enrichTurns(shown)
    .filter((turn) => isSpeechRole(turn.role))
    .map((turn) => ({ id: String(turn.id), wall: wall.get(String(turn.id)) }));
  if (!speech.length) return null;
  for (let i = 0; i < speech.length; i += 1) {
    if (speech[i].wall == null) return null;
    if (i && speech[i].wall < speech[i - 1].wall) return null;
  }
  return speech;
}

/**
 * Background tool activity of one call, for the Conversation view.
 *
 * Tools come only from the subtree of the root the call detail chose, so
 * another call's tools never sit beside this call's speech. Rows are placed
 * between utterances only when every utterance states a wall-clock time,
 * the clock span times use, in the order the view shows them; otherwise
 * they form their own group, timed from the call start. A row never claims
 * a recording position.
 *
 * Returns the transcript with a stable id on every row (so rows can be
 * anchored) and the activity list, without mutating the inputs.
 */
export function buildCallActivity({ tree, rootSpanId, transcript }) {
  const anchored = withTurnIds(transcript);
  const root = rootSpanId ? findEntry(tree, rootSpanId) : null;
  const origin = utcMs(root?.observation_span?.start_time);
  if (!root || origin === null) return { transcript: anchored, activity: [] };

  const tools = descendants(root)
    .filter(isToolSpan)
    .map((span) => toolProjection(span, origin))
    .sort(
      (a, b) =>
        (a.startMs ?? Infinity) - (b.startMs ?? Infinity) ||
        (a.key < b.key ? -1 : a.key > b.key ? 1 : 0),
    )
    .slice(0, MAX_ACTIVITY);

  const speech = tools.length ? speechOnWallClock(anchored) : null;
  const activity = tools.map(({ wallMs, ...item }) => {
    if (!speech || wallMs === null) return { ...item, interleaved: false };
    // Before the first utterance that starts after the tool; speech first on ties.
    const next = speech.find((row) => row.wall > wallMs);
    return { ...item, interleaved: true, anchorTurnId: next ? next.id : null };
  });
  return { transcript: anchored, activity };
}
