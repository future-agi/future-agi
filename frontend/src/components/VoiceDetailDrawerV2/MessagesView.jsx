import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Collapse,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import NestedJsonTable from "./NestedJsonTable";

const ROLE_CONFIG = {
  system: { label: "SYS", accent: "neutral" },
  user: { label: "USER", color: "#0ea5e9" },
  assistant: { label: "AI", accent: "brand" },
  tool: { label: "TOOL", color: "#f59e0b" },
  function: { label: "FN", color: "#f59e0b" },
  developer: { label: "DEV", accent: "violet" },
};

// The badge tints are built by concatenating alpha onto the hex, so the colour
// has to be a resolved value rather than a palette path.
const roleConfig = (role, palette) => {
  const key = String(role || "unknown").toLowerCase();
  const cfg = ROLE_CONFIG[key] || {
    label: key.toUpperCase(),
    accent: "neutral",
  };
  return { ...cfg, color: cfg.color || palette.accent[cfg.accent] };
};

// Backend ships every field in BOTH snake_case and camelCase. Keep the
// snake_case name as the canonical column key and drop the duplicate so
// the detail table doesn't show e.g. both `end_time` and `endTime`.
const SNAKE_TO_CAMEL = {
  end_time: "endTime",
  seconds_from_start: "secondsFromStart",
  message_count: "messageCount",
  tool_calls: "toolCalls",
  tool_call_id: "toolCallId",
  function_call: "functionCall",
  stop_reason: "stopReason",
  finish_reason: "finishReason",
};

const CANONICAL_ORDER = [
  "role",
  "message",
  "content",
  "source",
  "time",
  "seconds_from_start",
  "end_time",
  "duration",
  "metadata",
  "tool_calls",
  "tool_call_id",
  "function_call",
  "name",
  "stop_reason",
  "finish_reason",
];

const humanizeKey = (key) =>
  String(key)
    .replace(/[_-]+/g, " ")
    .replace(/([A-Z])/g, " $1")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\w/, (c) => c.toUpperCase());

const getEntries = (obj) => {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return [];
  const seen = new Set();
  const keys = Object.keys(obj);
  const ordered = [
    ...CANONICAL_ORDER.filter((k) => keys.includes(k)),
    ...keys.filter((k) => !CANONICAL_ORDER.includes(k)),
  ];
  const out = [];
  for (const key of ordered) {
    if (seen.has(key)) continue;
    // Drop the camelCase dup when the snake_case version is present.
    const camelDup = SNAKE_TO_CAMEL[key];
    if (camelDup && keys.includes(camelDup)) seen.add(camelDup);
    // Conversely, if we hit a camel key whose snake version was already
    // processed, skip it.
    const reverseSnake = Object.keys(SNAKE_TO_CAMEL).find(
      (sk) => SNAKE_TO_CAMEL[sk] === key,
    );
    if (reverseSnake && keys.includes(reverseSnake)) continue;
    seen.add(key);
    out.push([key, obj[key]]);
  }
  return out;
};

const getContentPreview = (message) => {
  const content = message?.content;
  if (content == null) {
    if (message?.tool_calls) return "[tool calls]";
    return "";
  }
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const text = content
      .map((c) => (typeof c === "string" ? c : c?.text || ""))
      .filter(Boolean)
      .join(" ");
    return text || "[structured content]";
  }
  try {
    return JSON.stringify(content);
  } catch {
    return "";
  }
};

// Render any primitive as a compact, monospace cell. Null-ish becomes
// an em-dash in the disabled color so missing fields don't look like
// bugs.
const ValueCell = ({ value }) => {
  if (value == null || value === "") {
    return (
      <Typography
        component="span"
        sx={{
          fontSize: 11,
          color: "text.disabled",
          fontFamily: "monospace",
        }}
      >
        —
      </Typography>
    );
  }
  if (typeof value === "boolean") {
    return (
      <Typography
        component="span"
        sx={{
          fontSize: 11,
          color: value ? "success.main" : "error.main",
          fontFamily: "monospace",
          fontWeight: 600,
        }}
      >
        {value ? "true" : "false"}
      </Typography>
    );
  }
  if (typeof value === "number") {
    return (
      <Typography
        component="span"
        sx={{
          fontSize: 11,
          color: "text.primary",
          fontFamily: "monospace",
        }}
      >
        {Number.isInteger(value) ? value : value.toFixed(3)}
      </Typography>
    );
  }
  return (
    <Typography
      component="span"
      sx={{
        fontSize: 11,
        color: "text.primary",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {String(value)}
    </Typography>
  );
};

ValueCell.propTypes = { value: PropTypes.any };

// Arrays of identically-shaped objects render as real tables: the first
// row's keys become the columns. This is the format `metadata.word_level_confidence`
// arrives in, so you get a readable word-timing grid instead of a JSON blob.
const ObjectArrayTable = ({ rows }) => {
  const columns = useMemo(() => {
    const seen = new Set();
    rows.forEach((r) => {
      if (r && typeof r === "object") {
        Object.keys(r).forEach((k) => seen.add(k));
      }
    });
    // Dedupe camel/snake dups
    const arr = Array.from(seen);
    const filtered = arr.filter((k) => {
      const reverseSnake = Object.keys(SNAKE_TO_CAMEL).find(
        (sk) => SNAKE_TO_CAMEL[sk] === k,
      );
      return !(reverseSnake && arr.includes(reverseSnake));
    });
    return filtered;
  }, [rows]);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        overflow: "auto",
        maxHeight: 220,
      }}
    >
      <Box
        component="table"
        sx={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: 11,
        }}
      >
        <Box component="thead" sx={{ position: "sticky", top: 0 }}>
          <Box component="tr">
            {columns.map((col) => (
              <Box
                key={col}
                component="th"
                sx={{
                  textAlign: "left",
                  px: 1,
                  py: 0.5,
                  fontWeight: 600,
                  fontSize: 10,
                  color: "text.secondary",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  bgcolor: "background.default",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  whiteSpace: "nowrap",
                }}
              >
                {humanizeKey(col)}
              </Box>
            ))}
          </Box>
        </Box>
        <Box component="tbody">
          {rows.map((row, i) => (
            <Box
              key={i}
              component="tr"
              sx={{
                "&:nth-of-type(even)": { bgcolor: "background.default" },
              }}
            >
              {columns.map((col) => (
                <Box
                  key={col}
                  component="td"
                  sx={{
                    px: 1,
                    py: 0.5,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    verticalAlign: "top",
                  }}
                >
                  <ValueCell value={row?.[col]} />
                </Box>
              ))}
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  );
};

ObjectArrayTable.propTypes = { rows: PropTypes.array.isRequired };

// Two-column key/value table. Nested objects recurse; arrays of objects
// delegate to `ObjectArrayTable`; arrays of primitives render inline.
const KeyValueTable = ({ data, depth = 0 }) => {
  const entries = getEntries(data);
  if (entries.length === 0) {
    return (
      <Typography
        sx={{ fontSize: 11, color: "text.disabled", fontStyle: "italic" }}
      >
        (empty)
      </Typography>
    );
  }
  return (
    <Box
      component="table"
      sx={{
        width: "100%",
        borderCollapse: "collapse",
        fontSize: 11,
      }}
    >
      <Box component="tbody">
        {entries.map(([key, value]) => {
          const isObject =
            value && typeof value === "object" && !Array.isArray(value);
          const isObjectArray =
            Array.isArray(value) &&
            value.length > 0 &&
            value.every((v) => v && typeof v === "object" && !Array.isArray(v));
          const isPrimitiveArray = Array.isArray(value) && !isObjectArray;

          return (
            <Box
              component="tr"
              key={key}
              sx={{
                "&:nth-of-type(even)": { bgcolor: "background.default" },
              }}
            >
              <Box
                component="th"
                sx={{
                  textAlign: "left",
                  px: 1,
                  py: 0.5,
                  fontWeight: 600,
                  fontSize: 10,
                  color: "text.secondary",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  verticalAlign: "top",
                  width: 140,
                  whiteSpace: "nowrap",
                }}
              >
                {humanizeKey(key)}
              </Box>
              <Box
                component="td"
                sx={{
                  px: 1,
                  py: 0.5,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  verticalAlign: "top",
                }}
              >
                {isObjectArray ? (
                  <ObjectArrayTable rows={value} />
                ) : isObject ? (
                  depth >= 2 ? (
                    <Typography
                      sx={{
                        fontSize: 11,
                        fontFamily: "monospace",
                        color: "text.secondary",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      {JSON.stringify(value)}
                    </Typography>
                  ) : (
                    <KeyValueTable data={value} depth={depth + 1} />
                  )
                ) : isPrimitiveArray ? (
                  <Typography
                    sx={{
                      fontSize: 11,
                      fontFamily: "monospace",
                      color: "text.primary",
                      wordBreak: "break-word",
                    }}
                  >
                    [{value.map((v) => JSON.stringify(v)).join(", ")}]
                  </Typography>
                ) : (
                  <ValueCell value={value} />
                )}
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
};

KeyValueTable.propTypes = {
  data: PropTypes.object.isRequired,
  depth: PropTypes.number,
};

const MessageRow = ({ message, index, isExpanded, onToggle }) => {
  const theme = useTheme();
  const cfg = roleConfig(message?.role, theme.palette);
  const preview = getContentPreview(message);

  return (
    <Box>
      <Box
        onClick={onToggle}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.25,
          py: "5px",
          cursor: "pointer",
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: isExpanded ? "rgba(123, 86, 219, 0.04)" : "transparent",
          "&:hover": {
            bgcolor: isExpanded ? "rgba(123, 86, 219, 0.06)" : "action.hover",
          },
          minHeight: 30,
        }}
      >
        <Iconify
          icon="mdi:chevron-right"
          width={14}
          sx={{
            color: "text.disabled",
            flexShrink: 0,
            transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)",
            transition: "transform 100ms",
          }}
        />

        {/* Role badge */}
        <Box
          sx={{
            display: "inline-flex",
            alignItems: "center",
            px: 0.5,
            py: "1px",
            borderRadius: "3px",
            bgcolor: `${cfg.color}14`,
            border: "1px solid",
            borderColor: `${cfg.color}30`,
            flexShrink: 0,
          }}
        >
          <Typography
            sx={{
              fontSize: 9,
              fontWeight: 600,
              color: cfg.color,
              letterSpacing: "0.02em",
              lineHeight: 1.2,
            }}
          >
            {cfg.label}
          </Typography>
        </Box>

        <Typography
          sx={{
            fontSize: 10,
            color: "text.disabled",
            width: 34,
            flexShrink: 0,
            fontFamily: "monospace",
          }}
        >
          #{index + 1}
        </Typography>

        <Typography
          noWrap
          sx={{
            flex: 1,
            minWidth: 0,
            fontSize: 11.5,
            color: "text.primary",
            lineHeight: 1.35,
          }}
        >
          {preview || "(empty)"}
        </Typography>
      </Box>

      <Collapse in={isExpanded} unmountOnExit>
        <Box
          sx={{
            px: 1.5,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: "background.default",
          }}
        >
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              bgcolor: "background.paper",
              maxHeight: "48vh",
              overflow: "auto",
              p: 0.75,
            }}
          >
            <NestedJsonTable data={message} emptyMessage="Empty message" />
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
};

MessageRow.propTypes = {
  message: PropTypes.object.isRequired,
  index: PropTypes.number.isRequired,
  isExpanded: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
};

const MessagesView = ({ messages }) => {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(new Set());

  const list = Array.isArray(messages) ? messages : [];

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return list.map((m, i) => ({ message: m, index: i }));
    return list
      .map((m, i) => ({ message: m, index: i }))
      .filter(({ message }) => {
        const role = String(message?.role || "").toLowerCase();
        const preview = getContentPreview(message).toLowerCase();
        return role.includes(q) || preview.includes(q);
      });
  }, [list, query]);

  const allExpanded =
    filtered.length > 0 && filtered.every(({ index }) => expanded.has(index));

  const handleToggle = useCallback((index) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const handleExpandAll = useCallback(() => {
    if (allExpanded) {
      setExpanded(new Set());
    } else {
      setExpanded(new Set(filtered.map(({ index }) => index)));
    }
  }, [allExpanded, filtered]);

  if (list.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 160,
          p: 2,
        }}
      >
        <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
          No messages
        </Typography>
      </Box>
    );
  }

  return (
    <Stack
      sx={{
        width: "100%",
        minWidth: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        overflow: "hidden",
        flex: 1,
        minHeight: 0,
      }}
    >
      {/* Search toolbar */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{
          px: 1,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.default",
          flexShrink: 0,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            flex: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            px: 1,
            py: 0.25,
            bgcolor: "background.paper",
          }}
        >
          <Iconify icon="mdi:magnify" width={14} color="text.disabled" />
          <Box
            component="input"
            placeholder="Search messages"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            sx={{
              flex: 1,
              border: "none",
              outline: "none",
              bgcolor: "transparent",
              fontSize: 11,
              color: "text.primary",
              py: 0.25,
              fontFamily: "inherit",
              "&::placeholder": { color: "text.disabled" },
            }}
          />
        </Box>
        <IconButton
          size="small"
          onClick={handleExpandAll}
          sx={{
            width: 24,
            height: 24,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
          }}
        >
          <Iconify
            icon={
              allExpanded
                ? "mdi:unfold-less-horizontal"
                : "mdi:unfold-more-horizontal"
            }
            width={14}
          />
        </IconButton>
      </Stack>

      {/* Rows */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {filtered.length === 0 ? (
          <Box
            sx={{
              p: 2,
              textAlign: "center",
            }}
          >
            <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
              No matching messages
            </Typography>
          </Box>
        ) : (
          filtered.map(({ message, index }) => (
            <MessageRow
              key={index}
              message={message}
              index={index}
              isExpanded={expanded.has(index)}
              onToggle={() => handleToggle(index)}
            />
          ))
        )}
      </Box>
    </Stack>
  );
};

MessagesView.propTypes = {
  messages: PropTypes.array,
};

export default MessagesView;
