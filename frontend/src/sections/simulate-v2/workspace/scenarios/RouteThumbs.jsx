import PropTypes from "prop-types";
import { useTheme } from "@mui/material/styles";

/**
 * Miniature previews of what each route actually looks like.
 *
 * Abstract shapes read as decoration, so each of these is a tiny mock of the
 * real screen — a pack with its rows, a chat with a caller's goal, a table with
 * its columns — with a couple of legible words to anchor it. The rest is
 * texture: at this size the shape carries the meaning.
 *
 * Plain SVG rather than MUI Box, which routes width/height through the style
 * system and mangles SVG geometry.
 */
function usePalette() {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  return {
    paper: dark ? "#18181B" : "#FFFFFF",
    line: dark ? "#3F3F46" : "#E4E4E7",
    solid: dark ? "#52525B" : "#D4D4D8",
    text: dark ? "#E4E4E7" : "#27272A",
    muted: dark ? "#A1A1AA" : "#9CA3AF",
    accent: theme.palette.primary.main,
    tint: dark ? "rgba(120,87,252,0.22)" : "rgba(120,87,252,0.12)",
  };
}

const FONT = "ui-sans-serif, -apple-system, Segoe UI, Roboto, sans-serif";
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/** The floating card every thumbnail sits on, plus the reference's stray dots. */
function Card({ children, c }) {
  return (
    <svg width="176" height="96" viewBox="0 0 176 96" fill="none" style={{ display: "block" }}>
      <circle cx="166" cy="30" r="1.6" fill={c.accent} opacity="0.35" />
      <circle cx="10" cy="66" r="1.6" fill={c.accent} opacity="0.25" />
      <circle cx="160" cy="74" r="1" fill={c.accent} opacity="0.45" />
      <rect x="16" y="9" width="144" height="78" rx="7" fill={c.paper} stroke={c.line} strokeWidth="1" />
      {children}
    </svg>
  );
}
Card.propTypes = { children: PropTypes.node, c: PropTypes.object };

/** Scenario pack — a curated list, the first row picked. */
export function PackThumb() {
  const c = usePalette();
  return (
    <Card c={c}>
      <text x="26" y="26" fontFamily={FONT} fontSize="7.5" fontWeight="700" fill={c.text}>Core tasks</text>
      <rect x="128" y="19" width="22" height="10" rx="5" fill={c.line} />
      <text x="134" y="26.5" fontFamily={FONT} fontSize="6" fill={c.muted}>3 tasks</text>
      <line x1="26" y1="33" x2="150" y2="33" stroke={c.line} strokeWidth="1" />

      <rect x="22" y="38" width="132" height="14" rx="3" fill={c.tint} />
      <circle cx="30" cy="45" r="3" fill={c.accent} />
      <rect x="38" y="42" width="60" height="5" rx="2.5" fill={c.accent} opacity="0.55" />
      <text x="128" y="47.5" fontFamily={FONT} fontSize="6" fill={c.accent}>~5</text>

      <circle cx="30" cy="61" r="3" fill="none" stroke={c.solid} strokeWidth="1" />
      <rect x="38" y="58" width="72" height="5" rx="2.5" fill={c.line} />
      <circle cx="30" cy="75" r="3" fill="none" stroke={c.solid} strokeWidth="1" />
      <rect x="38" y="72" width="52" height="5" rx="2.5" fill={c.line} />
    </Card>
  );
}

/** Describe in chat — the caller, their goal, and the exchange. */
export function ChatThumb() {
  const c = usePalette();
  return (
    <Card c={c}>
      <circle cx="30" cy="24" r="5" fill={c.tint} />
      <circle cx="30" cy="24" r="2" fill={c.accent} />
      <text x="40" y="22" fontFamily={FONT} fontSize="7" fontWeight="700" fill={c.text}>Refund past window</text>
      <text x="40" y="30.5" fontFamily={FONT} fontSize="6" fill={c.muted}>goal: full refund</text>
      <rect x="128" y="17" width="22" height="11" rx="5.5" fill={c.line} />
      <text x="133" y="24.5" fontFamily={FONT} fontSize="6" fill={c.muted}>2 / 5</text>

      <rect x="26" y="40" width="72" height="16" rx="8" fill={c.line} />
      <rect x="34" y="46" width="46" height="4" rx="2" fill={c.solid} />
      <rect x="62" y="62" width="88" height="16" rx="8" fill={c.accent} opacity="0.85" />
      <rect x="70" y="68" width="60" height="4" rx="2" fill={c.paper} opacity="0.75" />
    </Card>
  );
}

/** From a dataset — columns, one picked, rows becoming tasks. */
export function DatasetThumb() {
  const c = usePalette();
  return (
    <Card c={c}>
      <rect x="70" y="17" width="40" height="62" fill={c.tint} />
      <text x="26" y="27" fontFamily={MONO} fontSize="6.5" fill={c.muted}>input</text>
      <text x="74" y="27" fontFamily={MONO} fontSize="6.5" fill={c.accent}>expected</text>
      <text x="118" y="27" fontFamily={MONO} fontSize="6.5" fill={c.muted}>label</text>
      <line x1="22" y1="32" x2="154" y2="32" stroke={c.solid} strokeWidth="1" />

      {[42, 56, 70].map((y, i) => (
        <g key={y}>
          <rect x="26" y={y} width={i === 1 ? 32 : 38} height="5" rx="2.5" fill={c.line} />
          <rect x="74" y={y} width={i === 2 ? 24 : 30} height="5" rx="2.5" fill={c.accent} opacity="0.45" />
          <rect x="118" y={y} width="22" height="5" rx="2.5" fill={c.line} />
        </g>
      ))}
      <line x1="22" y1="51" x2="154" y2="51" stroke={c.line} strokeWidth="0.75" />
      <line x1="22" y1="65" x2="154" y2="65" stroke={c.line} strokeWidth="0.75" />
    </Card>
  );
}

/** Upload a script — the file, and the beats we pull out of it. */
export function ScriptThumb() {
  const c = usePalette();
  return (
    <Card c={c}>
      <rect x="16" y="9" width="144" height="18" rx="7" fill={c.line} opacity="0.5" />
      <circle cx="26" cy="18" r="2" fill={c.solid} />
      <circle cx="33" cy="18" r="2" fill={c.solid} />
      <text x="96" y="20.5" fontFamily={MONO} fontSize="6.5" fill={c.muted}>returns-call.txt</text>

      <rect x="26" y="36" width="16" height="8" rx="4" fill={c.accent} opacity="0.85" />
      <text x="30" y="42.5" fontFamily={FONT} fontSize="5.5" fill={c.paper}>1</text>
      <rect x="48" y="37" width="76" height="5" rx="2.5" fill={c.accent} opacity="0.45" />

      <rect x="26" y="52" width="16" height="8" rx="4" fill={c.line} />
      <text x="30" y="58.5" fontFamily={FONT} fontSize="5.5" fill={c.muted}>2</text>
      <rect x="48" y="53" width="94" height="5" rx="2.5" fill={c.line} />

      <rect x="26" y="68" width="16" height="8" rx="4" fill={c.line} />
      <text x="30" y="74.5" fontFamily={FONT} fontSize="5.5" fill={c.muted}>3</text>
      <rect x="48" y="69" width="62" height="5" rx="2.5" fill={c.line} />
    </Card>
  );
}

/** From production — clustered failing traces from the Error Feed. */
export function ProductionThumb() {
  const c = usePalette();
  return (
    <Card c={c}>
      {/* header — Error Feed title strip */}
      <rect x="26" y="17" width="80" height="7" rx="2" fill={c.text} opacity="0.75" />
      <text x="128" y="22" fontFamily={FONT} fontSize="5.5" fill={c.muted}>Error Feed</text>

      {/* cluster 1 — hot */}
      <rect x="26" y="32" width="124" height="12" rx="2" fill={c.line} opacity="0.25" />
      <circle cx="32" cy="38" r="2.4" fill="#DC2626" />
      <rect x="40" y="35" width="46" height="3" rx="1.5" fill={c.text} opacity="0.8" />
      <rect x="40" y="40" width="30" height="2.5" rx="1.25" fill={c.muted} />
      <text x="140" y="40.5" textAnchor="end" fontFamily={MONO} fontSize="5.5" fill="#DC2626" fontWeight="700">47</text>

      {/* cluster 2 — being promoted (accent + arrow to right) */}
      <rect x="26" y="49" width="124" height="12" rx="2" fill={c.tint} />
      <rect x="26" y="49" width="2" height="12" fill={c.accent} />
      <circle cx="32" cy="55" r="2.4" fill={c.accent} />
      <rect x="40" y="52" width="52" height="3" rx="1.5" fill={c.text} opacity="0.9" />
      <rect x="40" y="57" width="36" height="2.5" rx="1.25" fill={c.muted} />
      <text x="140" y="57.5" textAnchor="end" fontFamily={MONO} fontSize="5.5" fill={c.accent} fontWeight="700">→ +1</text>

      {/* cluster 3 */}
      <rect x="26" y="66" width="124" height="12" rx="2" fill={c.line} opacity="0.25" />
      <circle cx="32" cy="72" r="2.4" fill="#B45309" />
      <rect x="40" y="69" width="40" height="3" rx="1.5" fill={c.text} opacity="0.8" />
      <rect x="40" y="74" width="26" height="2.5" rx="1.25" fill={c.muted} />
      <text x="140" y="74.5" textAnchor="end" fontFamily={MONO} fontSize="5.5" fill={c.muted}>18</text>
    </Card>
  );
}

/** From your agent — its tools, and the weak spot we aim at. */
export function AgentThumb() {
  const c = usePalette();
  return (
    <Card c={c}>
      <rect x="26" y="17" width="46" height="12" rx="6" fill={c.tint} />
      <circle cx="34" cy="23" r="2.5" fill={c.accent} />
      <text x="40" y="25.5" fontFamily={FONT} fontSize="6" fill={c.accent}>your agent</text>

      <rect x="26" y="38" width="52" height="11" rx="3" fill={c.line} />
      <text x="31" y="45.5" fontFamily={MONO} fontSize="6" fill={c.muted}>lookup_order</text>
      <rect x="84" y="38" width="50" height="11" rx="3" fill={c.line} />
      <text x="89" y="45.5" fontFamily={MONO} fontSize="6" fill={c.muted}>issue_refund</text>

      <path d="M52 53v8h52v6" stroke={c.accent} strokeWidth="1" strokeDasharray="3 3" fill="none" opacity="0.7" />
      <rect x="60" y="65" width="88" height="14" rx="4" fill={c.accent} opacity="0.14" />
      <circle cx="70" cy="72" r="3" fill={c.accent} />
      <text x="78" y="74.5" fontFamily={FONT} fontSize="6" fill={c.accent}>weak spot found</text>
    </Card>
  );
}

/** Shown below the routes until one is picked. */
export function PickRouteIllustration() {
  const c = usePalette();
  return (
    <svg width="150" height="94" viewBox="0 0 150 94" fill="none" style={{ display: "block", margin: "0 auto" }}>
      <rect x="10" y="8" width="130" height="70" rx="7" fill={c.paper} stroke={c.solid} strokeWidth="1.25" />
      <text x="22" y="26" fontFamily={FONT} fontSize="8" fontWeight="700" fill={c.text}>Scenarios</text>
      <line x1="10" y1="33" x2="140" y2="33" stroke={c.line} strokeWidth="1" />
      {[42, 56, 70].map((y, i) => (
        <g key={y}>
          <circle cx="24" cy={y} r="3" fill={i === 0 ? c.accent : c.line} />
          <rect x="33" y={y - 3} width={i === 1 ? 58 : 76} height="6" rx="3" fill={c.line} />
        </g>
      ))}
      <circle cx="122" cy="72" r="16" fill={c.accent} opacity="0.12" />
      <path d="M115 72h14M122 65v14" stroke={c.accent} strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
