import React, {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import { messageText, sniffValue } from "./sniff";
import countTurns from "./countTurns";
import { copyAs } from "./copyAs";
import useColumnViewMode, { useCellColumnId } from "./useColumnViewMode";
import { MONO } from "./CodeBlock";
import { chatValueColumns, jsonValueColumns } from "./columnKinds";
import { conversationOf } from "src/sections/common/Media/mediaRef";
import { failingTurnOf } from "src/sections/common/Media/mediaModel";
import { toolNameOf } from "src/sections/develop-detail/DataTab/DatapointDrawerV2/toolCallModel";

// Columns seen holding chat values, so an empty `[]` in them reads
// `Empty chat` (module-level: every cell of a column shares it).
const chatColumns = chatValueColumns;

const TIP_MAX = 2000;

const compact = (v) => {
  let s;
  try {
    s = typeof v === "string" ? v : JSON.stringify(v);
  } catch {
    s = String(v);
  }
  return s.length > TIP_MAX ? `${s.slice(0, TIP_MAX)}…` : s;
};

const Chip = ({ children, mono = false, tone }) => (
  <Box
    component="span"
    sx={{
      px: 0.625,
      borderRadius: 0.5,
      typography: "s3",
      lineHeight: "18px",
      whiteSpace: "nowrap",
      overflow: "hidden",
      textOverflow: "ellipsis",
      flexShrink: 0,
      maxWidth: 160,
      fontFamily: mono ? MONO : undefined,
      bgcolor: tone
        ? (t) => alpha(t.palette.accent[tone], 0.12)
        : "action.hover",
      color: tone ? `accent.${tone}` : "text.secondary",
    }}
  >
    {children}
  </Box>
);

Chip.propTypes = {
  children: PropTypes.node,
  mono: PropTypes.bool,
  tone: PropTypes.string,
};

const ROLE_TONE = {
  system: "neutral",
  user: "info",
  assistant: "agent",
  tool: "tool",
  function: "tool",
};
const ROLE_SHORT = {
  system: "system",
  user: "user",
  assistant: "assistant",
  tool: "tool",
};

const scalar = (v) =>
  v === null
    ? "null"
    : typeof v === "string"
      ? v
      : typeof v === "object"
        ? Array.isArray(v)
          ? `[${v.length} ${v.length === 1 ? "item" : "items"}]`
          : `{${Object.keys(v).length} ${Object.keys(v).length === 1 ? "key" : "keys"}}`
        : String(v);

/** Sparkline of the first 64 values of an embedding. */
const Sparkline = ({ values }) => {
  const pts = values.slice(0, 64);
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const d = pts
    .map(
      (v, i) =>
        `${(i / (pts.length - 1)) * 60},${14 - ((v - min) / span) * 12}`,
    )
    .join(" ");
  return (
    <Box
      component="svg"
      viewBox="0 0 60 16"
      sx={{ width: 60, height: 16, flexShrink: 0 }}
    >
      <Box
        component="polyline"
        points={d}
        sx={{
          fill: "none",
          stroke: (t) => t.palette.accent.info,
          strokeWidth: 1,
        }}
      />
    </Box>
  );
};

Sparkline.propTypes = { values: PropTypes.array };

const Line = ({ children }) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={0.5}
    sx={{ minWidth: 0, overflow: "hidden", height: 22, flexWrap: "nowrap" }}
  >
    {children}
  </Stack>
);

Line.propTypes = { children: PropTypes.node };

const Preview = ({ text }) => (
  <Typography
    component="span"
    typography="s2"
    color="text.primary"
    noWrap
    sx={{ minWidth: 0, flex: 1 }}
  >
    {text}
  </Typography>
);

Preview.propTypes = { text: PropTypes.string };

/** Amber corner + tooltip when a cell does not fit its column type. */
export const MismatchCorner = ({ text }) => (
  <CustomTooltip show size="small" placement="top" arrow title={text}>
    <Box
      aria-label={text}
      sx={{
        position: "absolute",
        top: 0,
        right: 0,
        width: 0,
        height: 0,
        borderStyle: "solid",
        borderWidth: "0 10px 10px 0",
        borderColor: (t) =>
          `transparent ${t.palette.amber[500]} transparent transparent`,
        zIndex: 1,
        cursor: "help",
      }}
    />
  </CustomTooltip>
);

MismatchCorner.propTypes = { text: PropTypes.string };

/**
 * GridValueSummary — one json / text cell in the Data grid (R2-SPEC P6
 * §3.1): Auto shows a one-line typed summary (chat `6 turns` + role chips +
 * last message, `{3 keys}` + the first two `key: value` chips, `[500
 * items]`, `vec[1536]` + sparkline, a `YAML` hint, a size badge for huge
 * text); the column's `View as` JSON / YAML / Text shows the first lines in
 * monospace. Tooltip: the compact JSON (first 2,000 chars).
 */
const GridValueSummary = ({ value, dataType, fallback, bare = false }) => {
  const ref = useRef(null);
  const columnId = useCellColumnId(ref);
  const [mode] = useColumnViewMode(columnId);
  const sniffed = useMemo(
    () => sniffValue(value, { declaredType: dataType }),
    [value, dataType],
  );
  // R4 (lane M, ir-4): `{turns: [...]}` objects and message arrays read as a
  // conversation (`8 turns · "My blender arrived cracked…" · failing turn 6`),
  // never `{5 keys} turns: [8 items]`.
  const conv = useMemo(
    () =>
      sniffed.kind === "chat" || sniffed.kind === "object"
        ? conversationOf(value)
        : null,
    [sniffed.kind, value],
  );
  const [chatCol, setChatCol] = useState(false);
  // Whole lines only: as many 14 px lines as the row height holds.
  const [maxLines, setMaxLines] = useState(2);
  useLayoutEffect(() => {
    const h = ref.current?.closest?.(".ag-cell")?.clientHeight;
    if (h) setMaxLines(Math.max(1, Math.floor((h - 4) / 14)));
  }, [columnId]);
  if ((sniffed.kind === "chat" || conv) && columnId) chatColumns.add(columnId);
  if (
    columnId &&
    ["object", "array", "chat", "ref", "embedding"].includes(sniffed.kind)
  )
    jsonValueColumns.add(columnId);
  useEffect(() => {
    if (columnId && chatColumns.has(columnId)) setChatCol(true);
  }, [columnId, value]);

  const isText = typeof value === "string";
  const parsedJson = sniffed.sub === "json_text" || (!isText && value != null);

  let body = null;
  if (mode !== "auto" && sniffed.kind !== "empty") {
    const text =
      mode === "json"
        ? parsedJson
          ? copyAs(value, "json")
          : String(value)
        : mode === "yaml"
          ? sniffed.kind === "yaml_text"
            ? String(value)
            : copyAs(value, "yaml")
          : isText
            ? value
            : compact(value);
    const lines = text.split("\n");
    const shown = lines.slice(0, maxLines);
    if (lines.length > maxLines && shown.length)
      shown[shown.length - 1] = `${shown[shown.length - 1]} …`;
    body = (
      <Box sx={{ fontFamily: MONO, typography: "s3", color: "text.primary" }}>
        {shown.map((line, i) => (
          <Box
            key={i}
            sx={{
              lineHeight: "14px",
              height: 14,
              whiteSpace: "pre",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {line.length > 300 ? line.slice(0, 300) : line || " "}
          </Box>
        ))}
      </Box>
    );
  } else if (conv && conv.n) {
    const roles = [...new Set(conv.turns.map((t) => t.role))].filter(
      (r) => r !== "system",
    );
    const first = conv.firstCustomer ?? conv.turns.find((t) => t.text)?.text;
    body = (
      <Line>
        <Chip>
          {`${countTurns(conv.turns).toLocaleString("en-US")} ${countTurns(conv.turns) === 1 ? "turn" : "turns"}`}
        </Chip>
        {roles.slice(0, 3).map((r) => (
          <Chip key={r} tone={ROLE_TONE[r] ?? "neutral"}>
            {ROLE_SHORT[r] ?? r}
          </Chip>
        ))}
        {failingTurnOf(value, conv) != null ? (
          <Chip tone="fail">{`failing turn ${failingTurnOf(value, conv)}`}</Chip>
        ) : null}
        <Preview text={first ? `"${first}"` : ""} />
      </Line>
    );
  } else {
    switch (sniffed.kind) {
      case "chat": {
        const msgs = sniffed.normalized;
        if (!msgs.length) {
          body = (
            <Typography
              typography="s2"
              color="text.disabled"
              sx={{ fontStyle: "italic" }}
            >
              Empty chat
            </Typography>
          );
          break;
        }
        const roles = [...new Set(msgs.map((m) => m.role))].filter(
          (r) => r !== "system",
        );
        const last = msgs[msgs.length - 1];
        const lastText =
          messageText(last) ||
          (last.tool_calls?.length
            ? `→ ${toolNameOf(last.tool_calls[0])}()`
            : "");
        const turns = countTurns(msgs);
        body = (
          <Line>
            <Chip>{`${turns.toLocaleString("en-US")} ${turns === 1 ? "turn" : "turns"}`}</Chip>
            {roles.slice(0, 3).map((r) => (
              <Chip key={r} tone={ROLE_TONE[r] ?? "neutral"}>
                {ROLE_SHORT[r] ?? r}
              </Chip>
            ))}
            <Preview text={lastText} />
          </Line>
        );
        break;
      }
      case "object":
      case "ref": {
        const entries = Object.entries(sniffed.normalized);
        body = (
          <Line>
            <Chip
              mono
            >{`{${entries.length} ${entries.length === 1 ? "key" : "keys"}}`}</Chip>
            {entries.slice(0, 2).map(([k, v]) => (
              <Chip key={k} mono>{`${k}: ${scalar(v)}`}</Chip>
            ))}
          </Line>
        );
        break;
      }
      case "array":
      case "images": {
        const arr = sniffed.normalized;
        if (!arr.length && chatCol) {
          body = (
            <Typography
              typography="s2"
              color="text.disabled"
              sx={{ fontStyle: "italic" }}
            >
              Empty chat
            </Typography>
          );
          break;
        }
        body = (
          <Line>
            <Chip
              mono
            >{`[${arr.length.toLocaleString("en-US")} ${arr.length === 1 ? "item" : "items"}]`}</Chip>
            <Preview text={arr.slice(0, 3).map(scalar).join(", ")} />
          </Line>
        );
        break;
      }
      case "embedding":
        body = (
          <Line>
            <Chip mono>{`vec[${sniffed.normalized.length}]`}</Chip>
            <Sparkline values={sniffed.normalized} />
          </Line>
        );
        break;
      default:
        body = null;
    }
  }

  const textLength = isText ? value.length : 0;
  const yamlHint = mode === "auto" && sniffed.kind === "yaml_text";
  const sizeBadge = mode === "auto" && textLength > 20000;

  return (
    <Box
      ref={ref}
      sx={{
        position: "relative",
        height: "100%",
        minWidth: 0,
        overflow: "hidden",
      }}
    >
      {sniffed.meta?.mismatch && (
        <MismatchCorner text={sniffed.meta.mismatch} />
      )}
      <CustomTooltip
        show={
          sniffed.kind !== "empty" && (body !== null || yamlHint || sizeBadge)
        }
        size="small"
        placement="bottom-start"
        enterDelay={600}
        enterNextDelay={600}
        title={
          <Box
            component="span"
            sx={{
              fontFamily: MONO,
              fontSize: 11,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {compact(
              sniffed.kind === "chat" ? value : sniffed.normalized ?? value,
            )}
          </Box>
        }
      >
        <Box
          sx={{
            ...(bare ? {} : { px: 1, py: 0.5 }),
            height: "100%",
            minWidth: 0,
            overflow: "hidden",
          }}
        >
          {body ?? (
            <Stack
              direction="row"
              alignItems="flex-start"
              gap={0.5}
              sx={{ minWidth: 0 }}
            >
              <Box
                sx={{
                  flex: 1,
                  minWidth: 0,
                  ...(typeof fallback === "string" && {
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }),
                }}
              >
                {typeof fallback === "string"
                  ? fallback.replace(/\s*\n\s*/g, "  ")
                  : fallback}
              </Box>
              {yamlHint && (
                <CustomTooltip
                  show
                  size="small"
                  placement="top"
                  title="Looks like YAML · View as YAML"
                >
                  <Box component="span">
                    <Chip mono>YAML</Chip>
                  </Box>
                </CustomTooltip>
              )}
              {sizeBadge && (
                <Chip>{`${Math.round(textLength / 1024).toLocaleString("en-US")} KB`}</Chip>
              )}
            </Stack>
          )}
        </Box>
      </CustomTooltip>
    </Box>
  );
};

GridValueSummary.propTypes = {
  value: PropTypes.any,
  dataType: PropTypes.string,
  fallback: PropTypes.node,
  bare: PropTypes.bool,
};

export default GridValueSummary;
