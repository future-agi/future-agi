import PropTypes from "prop-types";
import { alpha, useTheme } from "@mui/material/styles";

/**
 * Miniature previews of what each route actually looks like.
 *
 * Abstract shapes read as decoration, so each of these is a tiny mock of the
 * real screen , a pack with its rows, a chat with a caller's goal, a table with
 * its columns , with a couple of legible words to anchor it. The rest is
 * texture: at this size the shape carries the meaning.
 *
 * Plain SVG rather than MUI Box, which routes width/height through the style
 * system and mangles SVG geometry.
 */
function usePalette() {
  const theme = useTheme();
  const { palette } = theme;
  return {
    paper: palette.background.paper,
    line: palette.divider,
    solid: palette.text.disabled,
    text: palette.text.primary,
    muted: palette.text.disabled,
    accent: palette.primary.main,
    tint: alpha(palette.primary.main, 0.12),
    danger: palette.error.main,
    warning: palette.warning.main,
  };
}

const FONT = "ui-sans-serif, -apple-system, Segoe UI, Roboto, sans-serif";
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/** The floating card every thumbnail sits on, plus the reference's stray dots. */
function Card({ children, c }) {
  return (
    <svg
      width="176"
      height="96"
      viewBox="0 0 176 96"
      fill="none"
      style={{ display: "block" }}
    >
      <circle cx="166" cy="30" r="1.6" fill={c.accent} opacity="0.35" />
      <circle cx="10" cy="66" r="1.6" fill={c.accent} opacity="0.25" />
      <circle cx="160" cy="74" r="1" fill={c.accent} opacity="0.45" />
      <rect
        x="16"
        y="9"
        width="144"
        height="78"
        rx="7"
        fill={c.paper}
        stroke={c.line}
        strokeWidth="1"
      />
      {children}
    </svg>
  );
}
Card.propTypes = { children: PropTypes.node, c: PropTypes.object };

/** Scenario pack , a curated list, the first row picked. */

/** Describe in chat , the caller, their goal, and the exchange. */
export function ChatThumb() {
  const c = usePalette();
  return (
    <Card c={c}>
      <circle cx="30" cy="24" r="5" fill={c.tint} />
      <circle cx="30" cy="24" r="2" fill={c.accent} />
      <text
        x="40"
        y="22"
        fontFamily={FONT}
        fontSize="7"
        fontWeight="600"
        fill={c.text}
      >
        Refund past window
      </text>
      <text x="40" y="30.5" fontFamily={FONT} fontSize="6" fill={c.muted}>
        goal: full refund
      </text>
      <rect x="128" y="17" width="22" height="11" rx="5.5" fill={c.line} />
      <text x="133" y="24.5" fontFamily={FONT} fontSize="6" fill={c.muted}>
        2 / 5
      </text>

      <rect x="26" y="40" width="72" height="16" rx="8" fill={c.line} />
      <rect x="34" y="46" width="46" height="4" rx="2" fill={c.solid} />
      <rect
        x="62"
        y="62"
        width="88"
        height="16"
        rx="8"
        fill={c.accent}
        opacity="0.85"
      />
      <rect
        x="70"
        y="68"
        width="60"
        height="4"
        rx="2"
        fill={c.paper}
        opacity="0.75"
      />
    </Card>
  );
}

/** AI generate , a depth picked, and new scenarios being written. */
export function GenerateThumb() {
  const c = usePalette();
  /* Four-point sparkle, centred on (x, y). */
  const sparkle = (x, y, r) =>
    `M${x} ${y - r}Q${x + r * 0.18} ${y - r * 0.18} ${x + r} ${y}Q${x + r * 0.18} ${y + r * 0.18} ${x} ${y + r}Q${x - r * 0.18} ${y + r * 0.18} ${x - r} ${y}Q${x - r * 0.18} ${y - r * 0.18} ${x} ${y - r}Z`;
  return (
    <Card c={c}>
      <path d={sparkle(30, 22, 5)} fill={c.accent} />
      <text
        x="40"
        y="25"
        fontFamily={FONT}
        fontSize="7.5"
        fontWeight="600"
        fill={c.text}
      >
        Generate
      </text>
      <rect x="128" y="17" width="22" height="10" rx="5" fill={c.line} />
      <text x="133" y="24.5" fontFamily={FONT} fontSize="6" fill={c.muted}>
        ~20
      </text>
      <line x1="26" y1="33" x2="150" y2="33" stroke={c.line} strokeWidth="1" />

      {/* depth , Standard picked */}
      <rect
        x="26"
        y="38"
        width="36"
        height="11"
        rx="3"
        fill="none"
        stroke={c.line}
        strokeWidth="1"
      />
      <text
        x="44"
        y="45.5"
        textAnchor="middle"
        fontFamily={FONT}
        fontSize="5.5"
        fill={c.muted}
      >
        Smoke
      </text>
      <rect
        x="66"
        y="38"
        width="44"
        height="11"
        rx="3"
        fill={c.tint}
        stroke={c.accent}
        strokeWidth="0.75"
      />
      <text
        x="88"
        y="45.5"
        textAnchor="middle"
        fontFamily={FONT}
        fontSize="5.5"
        fill={c.accent}
      >
        Standard
      </text>
      <rect
        x="114"
        y="38"
        width="36"
        height="11"
        rx="3"
        fill="none"
        stroke={c.line}
        strokeWidth="1"
      />
      <text
        x="132"
        y="45.5"
        textAnchor="middle"
        fontFamily={FONT}
        fontSize="5.5"
        fill={c.muted}
      >
        Deep
      </text>

      {/* scenarios arriving , the newest still being written */}
      <circle cx="30" cy="59" r="3" fill={c.accent} />
      <rect
        x="38"
        y="56.5"
        width="76"
        height="5"
        rx="2.5"
        fill={c.accent}
        opacity="0.45"
      />
      <circle
        cx="30"
        cy="73"
        r="3"
        fill="none"
        stroke={c.solid}
        strokeWidth="1"
      />
      <rect x="38" y="70.5" width="54" height="5" rx="2.5" fill={c.line} />
      <path d={sparkle(100, 73, 3)} fill={c.accent} opacity="0.7" />
    </Card>
  );
}

/** From production , clustered failing traces from the Error Feed. */
export function ProductionThumb() {
  const c = usePalette();
  return (
    <Card c={c}>
      {/* header , Error Feed title strip */}
      <rect
        x="26"
        y="17"
        width="80"
        height="7"
        rx="2"
        fill={c.text}
        opacity="0.75"
      />
      <text x="128" y="22" fontFamily={FONT} fontSize="5.5" fill={c.muted}>
        Error Feed
      </text>

      {/* cluster 1 , hot */}
      <rect
        x="26"
        y="32"
        width="124"
        height="12"
        rx="2"
        fill={c.line}
        opacity="0.25"
      />
      <circle cx="32" cy="38" r="2.4" fill={c.danger} />
      <rect
        x="40"
        y="35"
        width="46"
        height="3"
        rx="1.5"
        fill={c.text}
        opacity="0.8"
      />
      <rect x="40" y="40" width="30" height="2.5" rx="1.25" fill={c.muted} />
      <text
        x="140"
        y="40.5"
        textAnchor="end"
        fontFamily={MONO}
        fontSize="5.5"
        fill={c.danger}
        fontWeight="600"
      >
        47
      </text>

      {/* cluster 2 , being promoted (accent + arrow to right) */}
      <rect x="26" y="49" width="124" height="12" rx="2" fill={c.tint} />
      <rect x="26" y="49" width="2" height="12" fill={c.accent} />
      <circle cx="32" cy="55" r="2.4" fill={c.accent} />
      <rect
        x="40"
        y="52"
        width="52"
        height="3"
        rx="1.5"
        fill={c.text}
        opacity="0.9"
      />
      <rect x="40" y="57" width="36" height="2.5" rx="1.25" fill={c.muted} />
      <text
        x="140"
        y="57.5"
        textAnchor="end"
        fontFamily={MONO}
        fontSize="5.5"
        fill={c.accent}
        fontWeight="600"
      >
        → +1
      </text>

      {/* cluster 3 */}
      <rect
        x="26"
        y="66"
        width="124"
        height="12"
        rx="2"
        fill={c.line}
        opacity="0.25"
      />
      <circle cx="32" cy="72" r="2.4" fill={c.warning} />
      <rect
        x="40"
        y="69"
        width="40"
        height="3"
        rx="1.5"
        fill={c.text}
        opacity="0.8"
      />
      <rect x="40" y="74" width="26" height="2.5" rx="1.25" fill={c.muted} />
      <text
        x="140"
        y="74.5"
        textAnchor="end"
        fontFamily={MONO}
        fontSize="5.5"
        fill={c.muted}
      >
        18
      </text>
    </Card>
  );
}

/** From failing cases , a run's results, the failed row being picked. */
export function FailingCasesThumb() {
  const c = usePalette();
  const red = c.danger;
  return (
    <Card c={c}>
      <text
        x="26"
        y="25"
        fontFamily={FONT}
        fontSize="7.5"
        fontWeight="600"
        fill={c.text}
      >
        Run 3
      </text>
      <text x="52" y="25" fontFamily={FONT} fontSize="6" fill={c.muted}>
        agent v3
      </text>
      <rect x="122" y="17" width="28" height="10" rx="5" fill={c.line} />
      <text x="127" y="24.5" fontFamily={FONT} fontSize="6" fill={c.muted}>
        2 failed
      </text>
      <line x1="26" y1="33" x2="150" y2="33" stroke={c.line} strokeWidth="1" />

      {/* passed */}
      <circle
        cx="30"
        cy="42"
        r="3"
        fill="none"
        stroke={c.solid}
        strokeWidth="1"
      />
      <rect x="38" y="39.5" width="70" height="5" rx="2.5" fill={c.line} />

      {/* failed , being picked */}
      <rect x="22" y="49" width="132" height="14" rx="3" fill={c.tint} />
      <path
        d="M27.8 53.8l4.4 4.4M32.2 53.8l-4.4 4.4"
        stroke={red}
        strokeWidth="1.3"
        strokeLinecap="round"
      />
      <rect
        x="38"
        y="53.5"
        width="58"
        height="5"
        rx="2.5"
        fill={c.accent}
        opacity="0.55"
      />
      <text
        x="146"
        y="58.5"
        textAnchor="end"
        fontFamily={MONO}
        fontSize="5.5"
        fill={c.accent}
        fontWeight="600"
      >
        → +1
      </text>

      {/* flaky */}
      <circle
        cx="30"
        cy="72"
        r="3"
        fill="none"
        stroke={c.muted}
        strokeWidth="1"
        strokeDasharray="2 1.5"
      />
      <rect x="38" y="69.5" width="48" height="5" rx="2.5" fill={c.line} />
      <text
        x="146"
        y="74"
        textAnchor="end"
        fontFamily={MONO}
        fontSize="5.5"
        fill={c.muted}
      >
        1/3
      </text>
    </Card>
  );
}
