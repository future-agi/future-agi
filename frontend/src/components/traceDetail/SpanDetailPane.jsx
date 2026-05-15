import React, {
  useState,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Collapse,
  IconButton,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import {
  formatLatency,
  formatCost,
  formatTokenCount,
} from "src/sections/projects/LLMTracing/formatters";
import Markdown from "react-markdown";
import { JsonView, allExpanded, defaultStyles } from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { enqueueSnackbar } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import SmartPreview from "./SmartPreview";
import { isOpenAIMessages } from "./ChatMessageView";
import useSearchHighlight from "./useSearchHighlight";
import ScoresListSection from "src/components/ScoresListSection/ScoresListSection";
import { normalizeTags } from "./tagUtils";
import TagChip from "./TagChip";
import TagInput from "./TagInput";
import EvalsTabView, { collectAllEvalsFromEntry } from "./EvalsTabView";
import { openFixWithFalcon } from "src/sections/falcon-ai/helpers/openFixWithFalcon";
import ImageCard from "src/components/multimodal/ImageCard";
import AudioCellRenderer from "src/sections/common/DevelopCellRenderer/CellRenderers/AudioCellRenderer";
import useWavesurferCache from "src/hooks/use-wavesurfer-cache";
import {
  isImageAttrPath,
  isAudioAttrPath,
} from "src/components/multimodal/extractMediaSrc";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import SingleImageViewerProvider from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider";

/* ── helpers ──────────────────────────────────────────── */

function getSpan(entry) {
  return entry?.observation_span || {};
}

function chipValue(v, fallback = "-") {
  return v != null && v !== "" && v !== 0 ? String(v) : fallback;
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => {
    enqueueSnackbar("Copied", { variant: "info", autoHideDuration: 1500 });
  });
}

function stringify(val) {
  if (val == null) return "";
  if (typeof val === "string") return val;
  return JSON.stringify(val, null, 2);
}

/* ── JsonSyntax — lightweight JSON syntax highlighting ── */

const JsonSyntax = ({ json }) => {
  if (!json) return null;
  // Regex-based syntax coloring — matches keys, strings, numbers, booleans, null
  const parts = json.split(/("(?:[^"\\]|\\.)*")\s*:/g);
  const result = [];
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      // This is a key
      result.push(
        <span key={i} style={{ color: "var(--text-primary)", fontWeight: 500 }}>
          {parts[i]}
        </span>,
      );
      result.push(
        <span key={`${i}c`} style={{ color: "var(--text-disabled)" }}>
          :{" "}
        </span>,
      );
    } else {
      // This is value content — colorize inline
      const chunk = parts[i];
      const colored = chunk.replace(
        /("(?:[^"\\]|\\.)*")|(\b(?:true|false)\b)|(\bnull\b)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
        (match, str, bool, nul, num) => {
          if (str) return `<span style="color:#b5520a">${str}</span>`;
          if (bool) return `<span style="color:#9333EA">${match}</span>`;
          if (nul)
            return `<span style="color:var(--text-disabled)">${match}</span>`;
          if (num) return `<span style="color:#1750EB">${match}</span>`;
          return match;
        },
      );
      result.push(
        <span key={i} dangerouslySetInnerHTML={{ __html: colored }} />,
      );
    }
  }
  return <>{result}</>;
};

JsonSyntax.propTypes = { json: PropTypes.string };

/* ── MetricChip ───────────────────────────────────────── */

const MetricChip = ({ label, value }) => (
  <Box
    sx={{
      display: "inline-flex",
      alignItems: "center",
      gap: 0.5,
      px: 1,
      py: 0.25,
      bgcolor: "background.neutral",
      border: "1px solid",
      borderColor: "divider",
      borderRadius: "2px",
      minWidth: 64,
      fontSize: 11,
      color: "text.primary",
      lineHeight: "16px",
      whiteSpace: "nowrap",
    }}
  >
    {label} : {value}
  </Box>
);

MetricChip.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
};

/* ── Highlight — wraps matching text in a yellow bg span ── */

const Highlight = ({ text, query }) => {
  if (!query || !text) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <Box
        component="span"
        sx={{
          bgcolor: (theme) => alpha(theme.palette.warning.main, 0.25),
          borderRadius: "2px",
          px: "1px",
        }}
      >
        {text.slice(idx, idx + query.length)}
      </Box>
      {text.slice(idx + query.length)}
    </>
  );
};

Highlight.propTypes = { text: PropTypes.string, query: PropTypes.string };

/* ── ContentCard (collapsible I/O card) ───────────────── */

const ContentCard = ({ title, content, viewMode = "markdown" }) => {
  const [expanded, setExpanded] = useState(true);
  const [localViewMode, setLocalViewMode] = useState(null); // null = use parent viewMode
  const effectiveViewMode = localViewMode || viewMode;

  // Detect if content is JSON (object/array or parseable string)
  const { isJson, jsonPretty, textContent } = useMemo(() => {
    if (content == null || content === "")
      return { isJson: false, jsonPretty: "", textContent: "" };

    // Already an object/array
    if (typeof content === "object") {
      return {
        isJson: true,
        jsonPretty: JSON.stringify(content, null, 2),
        textContent: JSON.stringify(content, null, 2),
      };
    }

    // String — try to parse as JSON
    if (typeof content === "string") {
      try {
        const parsed = JSON.parse(content);
        if (typeof parsed === "object" && parsed !== null) {
          return {
            isJson: true,
            jsonPretty: JSON.stringify(parsed, null, 2),
            textContent: content,
          };
        }
      } catch {
        // Not JSON — treat as plain text
      }
      return { isJson: false, jsonPretty: content, textContent: content };
    }

    return {
      isJson: false,
      jsonPretty: String(content),
      textContent: String(content),
    };
  }, [content]);

  const displayText = isJson ? jsonPretty : textContent;
  if (!displayText) return null;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {/* Header — excluded from find-in-page */}
      <Stack
        data-search-skip="true"
        direction="row"
        alignItems="center"
        sx={{ px: 1.5, py: 0.75, cursor: "pointer" }}
        onClick={() => setExpanded((p) => !p)}
      >
        <Typography
          variant="body2"
          sx={{
            fontSize: 13,
            fontWeight: 500,
            fontFamily: "'IBM Plex Sans', sans-serif",
            flex: 1,
          }}
        >
          {title}
        </Typography>
        {/* Per-card Markdown/Raw toggle */}
        <Box sx={{ display: "flex", alignItems: "center", gap: 0 }}>
          {["markdown", "json"].map((mode) => (
            <Typography
              key={mode}
              onClick={(e) => {
                e.stopPropagation();
                setLocalViewMode(mode);
              }}
              sx={{
                fontSize: 10.5,
                px: 0.75,
                py: 0.15,
                cursor: "pointer",
                color:
                  effectiveViewMode === mode ? "text.primary" : "text.disabled",
                fontWeight: effectiveViewMode === mode ? 600 : 400,
                "&:hover": { color: "text.secondary" },
              }}
            >
              {mode === "json" ? "Raw" : "Markdown"}
            </Typography>
          ))}
        </Box>
        <IconButton
          size="small"
          sx={{ p: 0.25 }}
          onClick={(e) => {
            e.stopPropagation();
            copyText(displayText);
          }}
        >
          <Iconify icon="tabler:copy" width={14} color="text.disabled" />
        </IconButton>
        <Iconify
          icon={expanded ? "mdi:chevron-up" : "mdi:chevron-down"}
          width={16}
          sx={{ color: "text.disabled", ml: 0.25 }}
        />
      </Stack>

      <Collapse in={expanded}>
        {/* Content */}
        <Box
          sx={{
            mx: 1.5,
            mb: 1.5,
            p: 1,
            bgcolor: "background.neutral",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            fontSize: 12,
            fontFamily: "'Inter', sans-serif",
            color: "text.primary",
            maxHeight: 300,
            overflow: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            lineHeight: "18px",
          }}
        >
          {effectiveViewMode === "json" ||
          (isJson && effectiveViewMode !== "markdown") ? (
            /* JSON/Raw — syntax highlighted like a code editor */
            <SyntaxHighlighter
              language="json"
              customStyle={{
                margin: 0,
                padding: 0,
                background: "transparent",
                fontSize: "11.5px",
                lineHeight: "18px",
                fontFamily: "'IBM Plex Mono', 'Fira Code', monospace",
              }}
              wrapLongLines
            >
              {jsonPretty}
            </SyntaxHighlighter>
          ) : (
            /* Plain text / Markdown */
            <Box sx={{ "& p": { m: 0 }, "& pre": { whiteSpace: "pre-wrap" } }}>
              <Markdown>{textContent}</Markdown>
            </Box>
          )}
        </Box>
      </Collapse>
    </Box>
  );
};

ContentCard.propTypes = {
  title: PropTypes.string.isRequired,
  content: PropTypes.any,
  viewMode: PropTypes.string,
};

/* ── FormatToggle (Markdown/JSON pill) ────────────────── */

const MODE_LABELS = { markdown: "Markdown", json: "JSON", chat: "Chat" };

const FormatToggle = ({ value, onChange, modes = ["markdown", "json"] }) => (
  <Box
    sx={{
      display: "inline-flex",
      bgcolor: "background.neutral",
      borderRadius: "4px",
      overflow: "hidden",
      border: "1px solid",
      borderColor: "divider",
      flexShrink: 0,
    }}
  >
    {modes.map((mode) => (
      <Box
        key={mode}
        onClick={() => onChange(mode)}
        sx={{
          px: 1.25,
          py: 0.25,
          fontSize: 11,
          cursor: "pointer",
          fontWeight: value === mode ? 500 : 400,
          bgcolor: value === mode ? "background.paper" : "transparent",
          boxShadow: value === mode ? "0 1px 2px rgba(0,0,0,0.08)" : "none",
          color: value === mode ? "text.primary" : "text.secondary",
        }}
      >
        {MODE_LABELS[mode] || mode}
      </Box>
    ))}
  </Box>
);

FormatToggle.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  modes: PropTypes.array,
};

/* ── JsonPreviewBlock — full JSON view with search + copy ── */

const JsonPreviewBlock = ({
  span,
  input,
  output,
  attributes,
  searchQuery,
  hideInlineSearch = false,
}) => {
  const [jsonSearch, setJsonSearch] = useState("");

  const jsonData = useMemo(() => {
    const data = {};
    if (input != null && input !== "" && input !== "{}") data.input = input;
    if (output != null && output !== "" && output !== "{}")
      data.output = output;
    if (attributes && Object.keys(attributes).length > 0)
      data.attributes = attributes;
    if (
      span?.metadata &&
      typeof span.metadata === "object" &&
      Object.keys(span.metadata).length > 0
    ) {
      data.metadata = span.metadata;
    }
    // If nothing above, show the full span
    if (Object.keys(data).length === 0) {
      return span || {};
    }
    return data;
  }, [span, input, output, attributes]);

  const jsonString = useMemo(
    () => JSON.stringify(jsonData, null, 2),
    [jsonData],
  );

  // Search: find matching line numbers
  const activeSearch = (jsonSearch || searchQuery || "").trim().toLowerCase();
  const matchCount = useMemo(() => {
    if (!activeSearch) return 0;
    const lines = jsonString.split("\n");
    return lines.filter((l) => l.toLowerCase().includes(activeSearch)).length;
  }, [jsonString, activeSearch]);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        overflow: "hidden",
      }}
    >
      {/* Toolbar: search + copy */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{
          px: 1.25,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.default",
        }}
      >
        {hideInlineSearch ? (
          <Box sx={{ flex: 1 }} />
        ) : (
          <>
            <Iconify icon="mdi:magnify" width={13} color="text.disabled" />
            <Box
              component="input"
              placeholder="Search JSON..."
              value={jsonSearch}
              onChange={(e) => setJsonSearch(e.target.value)}
              sx={{
                border: "none",
                outline: "none",
                flex: 1,
                fontSize: 11,
                color: "text.primary",
                bgcolor: "transparent",
                py: 0.15,
                "&::placeholder": { color: "text.disabled" },
              }}
            />
            {activeSearch && (
              <Typography
                variant="caption"
                sx={{ fontSize: 10, color: "text.disabled", flexShrink: 0 }}
              >
                {matchCount} match{matchCount !== 1 ? "es" : ""}
              </Typography>
            )}
          </>
        )}
        <IconButton
          size="small"
          sx={{ p: 0.25 }}
          onClick={() => {
            copyText(jsonString);
          }}
        >
          <Iconify icon="tabler:copy" width={13} color="text.disabled" />
        </IconButton>
      </Stack>

      {/* JSON tree view */}
      <Box
        sx={{
          maxHeight: "calc(100vh - 380px)",
          overflow: "auto",
          p: 1,
          fontSize: 12,
          "& > div": { fontFamily: "monospace !important" },
          // Highlight matching text lines
          ...(activeSearch
            ? {
                "& span": {
                  // Can't directly highlight JsonView nodes, but we wrap in a searchable context
                },
              }
            : {}),
        }}
      >
        <JsonView
          data={jsonData}
          shouldExpandNode={allExpanded}
          clickToExpandNode
          style={{
            ...defaultStyles,
            container: {
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: "11px",
              lineHeight: "18px",
              backgroundColor: "transparent",
            },
            basicChildStyle: { paddingLeft: "16px" },
            label: { color: "var(--text-primary)", fontWeight: 500 },
            stringValue: { color: "#b5520a" },
            numberValue: { color: "#1750EB" },
            booleanValue: { color: "#9333EA" },
            nullValue: { color: "var(--text-disabled)" },
            undefinedValue: { color: "var(--text-disabled)" },
            punctuation: { color: "var(--text-disabled)" },
          }}
        />
      </Box>
    </Box>
  );
};

JsonPreviewBlock.propTypes = {
  span: PropTypes.object,
  input: PropTypes.any,
  output: PropTypes.any,
  attributes: PropTypes.object,
  searchQuery: PropTypes.string,
  hideInlineSearch: PropTypes.bool,
};

/* ── LogViewTable — flat log view (root span only) ── */

import { getTypeConfig } from "./spanTypeConfig";

function flattenSpansChronological(entries) {
  const result = [];
  if (!entries) return result;
  const walk = (list, depth) => {
    for (const entry of list) {
      const s = entry?.observation_span || {};
      result.push({ span: s, depth, entry });
      if (entry.children?.length) walk(entry.children, depth + 1);
    }
  };
  walk(entries, 0);
  result.sort((a, b) => {
    const aT = new Date(a.span.start_time || 0);
    const bT = new Date(b.span.start_time || 0);
    return aT - bT;
  });
  return result;
}

function formatRelativeMs(spanStart, traceStart) {
  if (!spanStart || !traceStart) return "+0ms";
  const diffMs = new Date(spanStart) - new Date(traceStart);
  if (diffMs <= 0) return "+0ms";
  if (diffMs < 1000) return `+${Math.round(diffMs)}ms`;
  if (diffMs < 60000) return `+${(diffMs / 1000).toFixed(1)}s`;
  const mins = Math.floor(diffMs / 60000);
  const secs = Math.floor((diffMs % 60000) / 1000);
  return `+${mins}m${secs}s`;
}

function formatDuration(startTime, latencyMs) {
  if (latencyMs != null && latencyMs > 0) return formatLatency(latencyMs);
  return "-";
}

const LogViewRow = ({
  item,
  traceStartTime,
  isExpanded,
  onToggle,
  viewMode = "markdown",
  drawerOpen = true,
}) => {
  const { span, depth, entry } = item;
  const type = (span.observation_type || "unknown").toLowerCase();
  const cfg = getTypeConfig(type);
  const name = span.name || "unnamed";
  const latency = span.latency_ms ?? span.latency ?? 0;
  const startTime = span.start_time;
  const hasError = span.status === "ERROR";
  const childCount = entry?.children?.length || 0;
  const input = span?.input;
  const output = span?.output;
  const spanAttributes = span?.span_attributes || {};
  const metadata = span?.metadata || {};
  const attributes = { ...metadata, ...spanAttributes };
  const model = span.model;
  const provider = span.provider;
  const totalTokens = span.total_tokens;
  const cost = span.cost;
  const evalCount = entry?.eval_scores?.length || 0;
  const annotationCount = entry?.annotations?.length || 0;
  const isDimmed = entry?._filterMatch === false;
  const hasAnyContent = input || output || Object.keys(attributes).length > 0;

  return (
    <Box>
      <Box
        onClick={onToggle}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0,
          px: 1.5,
          py: "5px",
          cursor: "pointer",
          borderBottom: "1px solid",
          borderColor: "divider",
          opacity: isDimmed ? 0.4 : 1,
          bgcolor: isExpanded ? "rgba(120, 87, 252, 0.04)" : "transparent",
          "&:hover": {
            opacity: 1,
            bgcolor: isExpanded ? "rgba(120, 87, 252, 0.06)" : "action.hover",
          },
          minHeight: 30,
        }}
      >
        {/* Expand chevron */}
        <Iconify
          icon="mdi:chevron-right"
          width={14}
          sx={{
            color: "text.disabled",
            mr: 0.5,
            flexShrink: 0,
            transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)",
            transition: "transform 100ms",
          }}
        />

        {/* Observation: type badge + name */}
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            minWidth: 0,
            overflow: "hidden",
          }}
        >
          <Box
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: "3px",
              px: 0.5,
              py: "1px",
              borderRadius: "3px",
              bgcolor: `${cfg.color}12`,
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
                textTransform: "uppercase",
                letterSpacing: "0.02em",
                lineHeight: 1.2,
              }}
            >
              {cfg.label}
            </Typography>
          </Box>
          <Typography
            noWrap
            sx={{
              fontSize: 11.5,
              fontWeight: 500,
              color: hasError ? "error.main" : "text.primary",
              lineHeight: 1.2,
              flex: 1,
              minWidth: 0,
            }}
          >
            {name}
          </Typography>
          {childCount > 0 && (
            <Typography
              sx={{ fontSize: 10, color: "text.disabled", flexShrink: 0 }}
            >
              {childCount} {childCount === 1 ? "item" : "items"}
            </Typography>
          )}
        </Box>

        {/* Depth */}
        <Typography
          sx={{
            fontSize: 10,
            color: "text.disabled",
            width: 40,
            textAlign: "right",
            flexShrink: 0,
          }}
        >
          L{depth}
        </Typography>

        {/* Start */}
        <Typography
          sx={{
            fontSize: 10,
            color: "text.disabled",
            fontFamily: "monospace",
            width: 56,
            textAlign: "right",
            flexShrink: 0,
            ml: 1,
          }}
        >
          {formatRelativeMs(startTime, traceStartTime)}
        </Typography>

        {/* Duration */}
        <Typography
          sx={{
            fontSize: 10,
            color: "text.secondary",
            fontWeight: 500,
            width: 56,
            textAlign: "right",
            flexShrink: 0,
            ml: 1,
          }}
        >
          {formatDuration(startTime, latency)}
        </Typography>

        {/* Evals */}
        <Typography
          sx={{
            fontSize: 10,
            color: evalCount > 0 ? "text.primary" : "text.disabled",
            width: 36,
            textAlign: "right",
            flexShrink: 0,
            ml: 1,
          }}
        >
          {evalCount > 0 ? evalCount : "-"}
        </Typography>

        {/* Annotations */}
        <Typography
          sx={{
            fontSize: 10,
            color: annotationCount > 0 ? "text.primary" : "text.disabled",
            width: 36,
            textAlign: "right",
            flexShrink: 0,
            ml: 1,
          }}
        >
          {annotationCount > 0 ? annotationCount : "-"}
        </Typography>
      </Box>

      {/* Expanded content — full span detail like Preview tab */}
      <Collapse in={isExpanded} unmountOnExit>
        <Box
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: "background.default",
          }}
        >
          {/* Metric summary row */}
          <Stack
            direction="row"
            sx={{ flexWrap: "wrap", gap: "4px 8px", mb: 1 }}
          >
            {type !== "unknown" && <MetricChip label="Type" value={type} />}
            {model && <MetricChip label="Model" value={model} />}
            {provider && <MetricChip label="Provider" value={provider} />}
            {latency > 0 && (
              <MetricChip label="Duration" value={formatLatency(latency)} />
            )}
            {totalTokens != null && totalTokens > 0 && (
              <MetricChip
                label="Tokens"
                value={formatTokenCount(totalTokens)}
              />
            )}
            {cost != null && cost > 0 && (
              <MetricChip label="Cost" value={formatCost(cost)} />
            )}
            {hasError && <MetricChip label="Status" value="ERROR" />}
          </Stack>

          {hasAnyContent ? (
            <Stack spacing={1}>
              <ContentCard title="Input" content={input} viewMode={viewMode} />
              <ContentCard
                title="Output"
                content={output}
                viewMode={viewMode}
              />
              {Object.keys(attributes).length > 0 && (
                <AttributesCard
                  attributes={attributes}
                  spanId={span?.id}
                  traceId={span?.trace}
                  drawerOpen={drawerOpen}
                />
              )}
            </Stack>
          ) : (
            <Typography
              sx={{
                fontSize: 12,
                color: "text.disabled",
                py: 1,
                textAlign: "center",
              }}
            >
              No data available
            </Typography>
          )}
        </Box>
      </Collapse>
    </Box>
  );
};

LogViewRow.propTypes = {
  item: PropTypes.object.isRequired,
  traceStartTime: PropTypes.any,
  isExpanded: PropTypes.bool,
  onToggle: PropTypes.func,
  viewMode: PropTypes.string,
  drawerOpen: PropTypes.bool,
};

const LOG_COLUMNS = [
  { key: "observation", label: "Observation", flex: 1 },
  { key: "depth", label: "Depth", width: 40 },
  { key: "start", label: "Start", width: 56 },
  { key: "duration", label: "Duration", width: 56 },
  { key: "evals", label: "Evals", width: 36 },
  { key: "annotations", label: "Ann.", width: 36 },
];

const LogViewTable = ({ allSpans, traceStartTime, drawerOpen = true }) => {
  const [logSearch, setLogSearch] = useState("");
  const [expandedRows, setExpandedRows] = useState(new Set());
  const [logViewMode, setLogViewMode] = useState("markdown"); // "markdown" | "json"

  const flatList = useMemo(
    () => flattenSpansChronological(allSpans),
    [allSpans],
  );

  const q = logSearch.trim().toLowerCase();
  const filtered = q
    ? flatList.filter((item) => {
        const name = (item.span.name || "").toLowerCase();
        const type = (item.span.observation_type || "").toLowerCase();
        const id = (item.span.id || "").toLowerCase();
        return name.includes(q) || type.includes(q) || id.includes(q);
      })
    : flatList;

  const allExpanded =
    filtered.length > 0 &&
    filtered.every((item) => expandedRows.has(item.span.id));

  const handleToggle = useCallback((id) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleExpandAll = useCallback(() => {
    if (allExpanded) {
      setExpandedRows(new Set());
    } else {
      setExpandedRows(new Set(filtered.map((item) => item.span.id)));
    }
  }, [allExpanded, filtered]);

  const handleCopyAll = useCallback(() => {
    const data = filtered.map((item) => {
      const s = item.span;
      return {
        name: s.name,
        type: s.observation_type,
        depth: item.depth,
        latency_ms: s.latency_ms ?? s.latency,
        cost: s.cost,
        input: s.input,
        output: s.output,
      };
    });
    navigator.clipboard.writeText(JSON.stringify(data, null, 2)).then(() => {
      enqueueSnackbar("Log data copied", {
        variant: "info",
        autoHideDuration: 1500,
      });
    });
  }, [filtered]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Toolbar: Search | Markdown/JSON | Align | Expand | Copy */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.5,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {/* Search */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            flex: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "2px",
            px: 1,
            py: 0.25,
          }}
        >
          <Iconify icon="mdi:magnify" width={14} color="text.disabled" />
          <Box
            component="input"
            placeholder="Search"
            value={logSearch}
            onChange={(e) => setLogSearch(e.target.value)}
            sx={{
              border: "none",
              outline: "none",
              flex: 1,
              fontSize: 11,
              color: "text.primary",
              bgcolor: "transparent",
              py: 0.15,
              fontFamily: "'Inter', sans-serif",
              "&::placeholder": { color: "text.disabled" },
            }}
          />
        </Box>

        {/* Markdown / JSON toggle */}
        <Box
          sx={{
            display: "inline-flex",
            bgcolor: "background.neutral",
            borderRadius: "4px",
            overflow: "hidden",
            flexShrink: 0,
          }}
        >
          {["markdown", "json"].map((mode) => (
            <Box
              key={mode}
              onClick={() => setLogViewMode(mode)}
              sx={{
                px: 1,
                py: 0.25,
                fontSize: 11,
                cursor: "pointer",
                fontWeight: logViewMode === mode ? 500 : 400,
                bgcolor:
                  logViewMode === mode ? "background.paper" : "transparent",
                boxShadow:
                  logViewMode === mode
                    ? "2px 2px 6px rgba(0,0,0,0.08)"
                    : "none",
                color: logViewMode === mode ? "text.primary" : "text.secondary",
                fontFamily: "'IBM Plex Sans', sans-serif",
                borderRadius: "4px",
              }}
            >
              {mode === "json" ? "JSON" : "Markdown"}
            </Box>
          ))}
        </Box>

        {/* Align left */}
        <CustomTooltip show type="black" size="small" title="Align left">
          <IconButton
            size="small"
            sx={{
              width: 24,
              height: 24,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              bgcolor: "background.neutral",
            }}
          >
            <Iconify icon="tabler:align-left" width={14} color="text.primary" />
          </IconButton>
        </CustomTooltip>

        {/* Expand / Collapse all */}
        <CustomTooltip
          show
          type="black"
          size="small"
          title={allExpanded ? "Collapse all" : "Expand all"}
        >
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
              color="text.primary"
            />
          </IconButton>
        </CustomTooltip>

        {/* Copy all */}
        <CustomTooltip show type="black" size="small" title="Copy all">
          <IconButton
            size="small"
            onClick={handleCopyAll}
            sx={{
              width: 24,
              height: 24,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
            }}
          >
            <Iconify icon="tabler:copy" width={14} color="text.primary" />
          </IconButton>
        </CustomTooltip>
      </Stack>

      {/* Column headers */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          px: 1.5,
          py: 0.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.default",
        }}
      >
        <Box sx={{ width: 20 }} />
        <Typography
          sx={{
            flex: 1,
            fontSize: 10,
            fontWeight: 600,
            color: "text.disabled",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          Observation
        </Typography>
        <Typography
          sx={{
            fontSize: 10,
            fontWeight: 600,
            color: "text.disabled",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            width: 40,
            textAlign: "right",
          }}
        >
          Depth
        </Typography>
        <Typography
          sx={{
            fontSize: 10,
            fontWeight: 600,
            color: "text.disabled",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            width: 56,
            textAlign: "right",
            ml: 1,
          }}
        >
          Start
        </Typography>
        <Typography
          sx={{
            fontSize: 10,
            fontWeight: 600,
            color: "text.disabled",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            width: 56,
            textAlign: "right",
            ml: 1,
          }}
        >
          Duration
        </Typography>
        <Typography
          sx={{
            fontSize: 10,
            fontWeight: 600,
            color: "text.disabled",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            width: 36,
            textAlign: "right",
            ml: 1,
          }}
        >
          Evals
        </Typography>
        <Typography
          sx={{
            fontSize: 10,
            fontWeight: 600,
            color: "text.disabled",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            width: 36,
            textAlign: "right",
            ml: 1,
          }}
        >
          Ann.
        </Typography>
      </Box>

      {/* Rows */}
      <Box sx={{ flex: 1, overflow: "auto" }}>
        {filtered.length === 0 ? (
          <Box sx={{ p: 3, textAlign: "center" }}>
            <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
              {q ? `No observations match "${q}"` : "No observations"}
            </Typography>
          </Box>
        ) : (
          filtered.map((item) => (
            <LogViewRow
              key={item.span.id}
              item={item}
              traceStartTime={traceStartTime}
              isExpanded={expandedRows.has(item.span.id)}
              onToggle={() => handleToggle(item.span.id)}
              viewMode={logViewMode}
              drawerOpen={drawerOpen}
            />
          ))
        )}
      </Box>
    </Box>
  );
};

LogViewTable.propTypes = {
  allSpans: PropTypes.array,
  traceStartTime: PropTypes.any,
  drawerOpen: PropTypes.bool,
};

/* ── InlineTagsRow — always-visible tag chips with add/remove ── */

const InlineTagsRow = ({ tags = [], traceId, spanId }) => {
  const [isAdding, setIsAdding] = useState(false);
  const queryClient = useQueryClient();

  const normalized = useMemo(() => normalizeTags(tags), [tags]);

  const { mutate: saveTags, isPending } = useMutation({
    mutationFn: (newTags) => {
      if (spanId) {
        return axios.post(`/tracer/observation-span/update-tags/`, {
          span_id: spanId,
          tags: newTags,
        });
      }
      return axios.patch(`/tracer/trace/${traceId}/tags/`, { tags: newTags });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["trace-detail"] });
    },
    onError: () => {
      enqueueSnackbar("Failed to update tags", { variant: "error" });
    },
  });

  const persist = useCallback((next) => saveTags(next), [saveTags]);

  return (
    <Stack
      direction="row"
      sx={{ mt: 0.75, flexWrap: "wrap", gap: 0.5, alignItems: "center" }}
    >
      <Iconify
        icon="mdi:tag-outline"
        width={13}
        sx={{ color: "text.disabled" }}
      />

      {normalized.map((tag, idx) => (
        <TagChip
          key={`${tag.name}-${idx}`}
          name={tag.name}
          color={tag.color}
          size="small"
          onRemove={() => persist(normalized.filter((_, i) => i !== idx))}
          onColorChange={(c) =>
            persist(
              normalized.map((t, i) => (i === idx ? { ...t, color: c } : t)),
            )
          }
          onRename={(n) => {
            if (normalized.some((t, i) => i !== idx && t.name === n)) return;
            persist(
              normalized.map((t, i) => (i === idx ? { ...t, name: n } : t)),
            );
          }}
        />
      ))}

      {isAdding ? (
        <Box
          sx={{ minWidth: 130 }}
          onBlur={(e) => {
            // Close if focus leaves the TagInput entirely
            if (!e.currentTarget.contains(e.relatedTarget)) setIsAdding(false);
          }}
        >
          <TagInput
            onAdd={(newTag) => {
              persist([...normalized, newTag]);
              setIsAdding(false);
            }}
            existingNames={normalized.map((t) => t.name)}
            disabled={isPending}
            placeholder="tag name"
          />
        </Box>
      ) : (
        <Box
          onClick={() => setIsAdding(true)}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: "2px",
            px: 0.5,
            py: "1px",
            borderRadius: "3px",
            border: "1px dashed",
            borderColor: "divider",
            fontSize: 11,
            color: "text.disabled",
            cursor: "pointer",
            lineHeight: "16px",
            "&:hover": { borderColor: "primary.main", color: "primary.main" },
          }}
        >
          <Iconify icon="mdi:plus" width={12} />
          tag
        </Box>
      )}
    </Stack>
  );
};

InlineTagsRow.propTypes = {
  tags: PropTypes.array,
  traceId: PropTypes.string,
  spanId: PropTypes.string,
};

/* ── EvalCard — single eval score display ─────────────── */

const EvalCard = ({ ev, spanLabel }) => {
  const [showExplanation, setShowExplanation] = useState(false);
  const score = ev.score;
  const isPassing = score != null && score >= 50;
  const explanation = ev.explanation || ev.eval_explanation;
  const evalName = ev.eval_name || ev.eval_config_id || "Eval";

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: (theme) =>
          alpha(
            isPassing ? theme.palette.success.main : theme.palette.error.main,
            0.2,
          ),
        borderRadius: "6px",
        bgcolor: (theme) =>
          alpha(
            isPassing ? theme.palette.success.main : theme.palette.error.main,
            0.04,
          ),
        overflow: "hidden",
      }}
    >
      {/* Header row */}
      <Box
        onClick={() => explanation && setShowExplanation((p) => !p)}
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 1.25,
          py: 0.75,
          cursor: explanation ? "pointer" : "default",
          "&:hover": explanation ? { bgcolor: "rgba(0,0,0,0.02)" } : {},
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            flex: 1,
            minWidth: 0,
          }}
        >
          <Iconify
            icon={isPassing ? "mdi:checkbox-marked-circle" : "mdi:close-circle"}
            width={14}
            sx={{
              color: isPassing ? "success.dark" : "error.main",
              flexShrink: 0,
            }}
          />
          <Box sx={{ minWidth: 0 }}>
            <Typography noWrap sx={{ fontSize: 12, fontWeight: 500 }}>
              {evalName}
            </Typography>
            {spanLabel && (
              <Typography noWrap sx={{ fontSize: 10, color: "text.disabled" }}>
                on {spanLabel}
              </Typography>
            )}
          </Box>
        </Box>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            flexShrink: 0,
          }}
        >
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: isPassing ? "success.dark" : "error.main",
            }}
          >
            {score != null ? `${score}%` : String(ev.result ?? "—")}
          </Typography>
          {explanation && (
            <Iconify
              icon={showExplanation ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={14}
              color="text.disabled"
            />
          )}
        </Box>
      </Box>

      {/* Explanation (collapsible) */}
      {showExplanation && explanation && (
        <Box
          sx={{
            px: 1.25,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.default",
          }}
        >
          <Typography
            sx={{
              fontSize: 11,
              color: "text.secondary",
              lineHeight: 1.5,
              whiteSpace: "pre-wrap",
            }}
          >
            {explanation}
          </Typography>
        </Box>
      )}
    </Box>
  );
};

EvalCard.propTypes = { ev: PropTypes.object, spanLabel: PropTypes.string };

/* ── AnnotationsTabContent — uses ScoresListSection ── */

const AnnotationsTabContent = ({ spanId, traceId, onAction }) => (
  <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
    <Box sx={{ flex: 1, overflow: "auto", p: 2 }}>
      <ScoresListSection
        sourceType="observation_span"
        sourceId={spanId}
        secondarySourceType="trace"
        secondarySourceId={traceId}
        title=""
        openQueueItemOnRowClick
        renderActions={
          onAction ? (
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon="mingcute:add-line" width={14} />}
              onClick={(e) => onAction("annotate", e.currentTarget)}
              sx={{
                textTransform: "none",
                fontSize: 12,
                fontWeight: 500,
                borderColor: "divider",
                color: "text.primary",
                borderRadius: "4px",
                px: 1.5,
                py: 0.25,
              }}
            >
              Add Label
            </Button>
          ) : null
        }
      />
    </Box>
  </Box>
);

AnnotationsTabContent.propTypes = {
  spanId: PropTypes.string,
  traceId: PropTypes.string,
  onAction: PropTypes.func,
};

/* ── EventCard — formatted event display ── */

const EventCard = ({ event }) => {
  const [showTrace, setShowTrace] = useState(false);
  const isException = event?.name === "exception";
  const attrs = event?.attributes || {};
  const exType = attrs["exception.type"] || "";
  const exMessage = attrs["exception.message"] || "";
  const exStacktrace = attrs["exception.stacktrace"] || "";
  const exEscaped = attrs["exception.escaped"];

  // Format timestamp
  const ts = event?.timestamp;
  const formattedTime = useMemo(() => {
    if (!ts) return "";
    try {
      // Handle nanosecond timestamps, ISO strings, or ms
      const d =
        typeof ts === "string" && ts.includes("T")
          ? new Date(ts)
          : typeof ts === "number" && ts > 1e15
            ? new Date(ts / 1e6)
            : typeof ts === "number"
              ? new Date(ts)
              : null;
      if (d && !isNaN(d)) return d.toLocaleString();
    } catch {
      /* ignore */
    }
    return String(ts);
  }, [ts]);

  // Non-exception attributes
  const otherAttrs = useMemo(() => {
    return Object.entries(attrs).filter(([k]) => !k.startsWith("exception."));
  }, [attrs]);

  if (isException) {
    return (
      <Box
        sx={{
          border: "1px solid",
          borderColor: (theme) => alpha(theme.palette.error.main, 0.3),
          borderRadius: "8px",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            px: 2,
            py: 1,
            bgcolor: (theme) => alpha(theme.palette.error.main, 0.08),
            borderBottom: "1px solid",
            borderColor: (theme) => alpha(theme.palette.error.main, 0.3),
          }}
        >
          <Iconify
            icon="mdi:alert-circle"
            width={18}
            sx={{ color: "error.main" }}
          />
          <Typography
            sx={{ fontSize: 14, fontWeight: 600, color: "error.main", flex: 1 }}
          >
            {exType || "Exception"}
          </Typography>
          {formattedTime && (
            <Typography
              sx={{ fontSize: 11, color: "error.main", opacity: 0.7 }}
            >
              {formattedTime}
            </Typography>
          )}
        </Box>

        {/* Message */}
        {exMessage && (
          <Box
            sx={{
              px: 2,
              py: 1.5,
              borderBottom: exStacktrace ? "1px solid" : "none",
              borderColor: (theme) => alpha(theme.palette.error.main, 0.3),
            }}
          >
            <Typography
              sx={{ fontSize: 13, color: "text.primary", lineHeight: 1.5 }}
            >
              {exMessage}
            </Typography>
          </Box>
        )}

        {/* Stacktrace */}
        {exStacktrace && (
          <Box>
            <Box
              onClick={() => setShowTrace(!showTrace)}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                px: 2,
                py: 0.75,
                cursor: "pointer",
                "&:hover": {
                  bgcolor: (theme) => alpha(theme.palette.error.main, 0.08),
                },
              }}
            >
              <Iconify
                icon={showTrace ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={16}
                sx={{ color: "error.main" }}
              />
              <Typography
                sx={{ fontSize: 12, fontWeight: 500, color: "error.main" }}
              >
                Stack Trace
              </Typography>
            </Box>
            <Collapse in={showTrace}>
              <Box sx={{ px: 2, pb: 1.5, maxHeight: 300, overflow: "auto" }}>
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    p: 1.5,
                    bgcolor: (theme) =>
                      theme.palette.mode === "dark"
                        ? theme.palette.background.neutral
                        : theme.palette.grey[900],
                    color: (theme) =>
                      theme.palette.mode === "dark"
                        ? theme.palette.text.primary
                        : theme.palette.grey[300],
                    borderRadius: "6px",
                    fontSize: 11,
                    fontFamily: "'IBM Plex Mono', monospace",
                    lineHeight: 1.6,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    overflow: "auto",
                  }}
                >
                  {typeof exStacktrace === "string"
                    ? exStacktrace.replace(/^"|"$/g, "").replace(/\\n/g, "\n")
                    : exStacktrace}
                </Box>
              </Box>
            </Collapse>
          </Box>
        )}
      </Box>
    );
  }

  // Non-exception event
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 2,
          py: 1,
          bgcolor: "background.default",
        }}
      >
        <Iconify
          icon="mdi:bell-outline"
          width={16}
          sx={{ color: "text.disabled" }}
        />
        <Typography
          sx={{ fontSize: 13, fontWeight: 600, color: "text.primary", flex: 1 }}
        >
          {event?.name || "Event"}
        </Typography>
        {formattedTime && (
          <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
            {formattedTime}
          </Typography>
        )}
      </Box>
      {otherAttrs.length > 0 && (
        <Box sx={{ px: 2, py: 1 }}>
          {otherAttrs.map(([k, v]) => (
            <Box key={k} sx={{ display: "flex", gap: 1, py: 0.25 }}>
              <Typography
                sx={{
                  fontSize: 12,
                  fontWeight: 500,
                  color: "text.secondary",
                  minWidth: 120,
                  flexShrink: 0,
                }}
              >
                {k}
              </Typography>
              <Typography
                sx={{
                  fontSize: 12,
                  color: "text.primary",
                  wordBreak: "break-word",
                }}
              >
                {typeof v === "object" ? JSON.stringify(v) : String(v)}
              </Typography>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
};

EventCard.propTypes = { event: PropTypes.object };

/* ── SpanDetailPane ───────────────────────────────────── */

const SpanDetailPane = ({
  entry,
  allSpans,
  traceStartTime,
  isRootSpan,
  traceTags,
  projectId,
  onClose,
  onAction,
  onSelectSpan,
  drawerOpen = true,
}) => {
  const [activeTab, setActiveTab] = useState("preview");
  const [searchQuery, setSearchQuery] = useState("");
  const [viewMode, setViewMode] = useState("markdown"); // "markdown" | "json" | "chat"

  // Find-in-page: highlights matches inside the preview container and
  // scrolls the active match into view.
  //
  // The raw `searchQuery` drives the <input>'s value so keystrokes land
  // immediately. Everything downstream (the hook's DOM walk + range
  // build, AttributesCard's row filter, and SmartPreview's per-card
  // passes) reads the *deferred* query so React can interrupt those
  // expensive passes when a new keystroke arrives — without that, the
  // ~1000 dev-synthetic attributes cause visible input lag.
  const deferredSearchQuery = useDeferredValue(searchQuery);
  const previewContentRef = useRef(null);
  const { matchCount, activeIndex, next, prev } = useSearchHighlight(
    previewContentRef,
    deferredSearchQuery,
  );

  const span = getSpan(entry);
  const spanAttributes = span?.span_attributes || span?.eval_attributes || {};
  const metadata = span?.metadata || {};
  // Merge span_attributes + metadata for the Attributes table
  const attributes = { ...metadata, ...spanAttributes };
  const input = span?.input;
  const output = span?.output;
  const spanEvents = span?.span_events || [];

  // Build metric chips — show key properties
  const chips = useMemo(() => {
    if (!span) return [];
    const startTime = span.start_time;
    const observationType = span.observation_type;
    const model = span.model;
    const provider = span.provider;
    const latency = span.latency_ms || span.latency;
    const totalTokens = span.total_tokens;
    const promptTokens = span.prompt_tokens;
    const completionTokens = span.completion_tokens;
    const cost = span.cost;
    const status = span.status;

    // Only include chips that have values (skip empty ones)
    const all = [
      observationType && { label: "Type", value: observationType },
      model && { label: "Model", value: model },
      provider && { label: "Provider", value: provider },
      status && { label: "Status", value: status },
      startTime && {
        label: "Start time",
        value: new Date(startTime).toLocaleString(),
      },
      latency != null && {
        label: "Duration",
        value: formatLatency(latency),
      },
      totalTokens != null && {
        label: "Total tokens",
        value: formatTokenCount(totalTokens),
      },
      promptTokens != null && {
        label: "Prompt tokens",
        value: formatTokenCount(promptTokens),
      },
      completionTokens != null && {
        label: "Completion tokens",
        value: formatTokenCount(completionTokens),
      },
      cost != null && {
        label: "Cost",
        value: formatCost(cost),
      },
    ];
    return all.filter(Boolean);
  }, [span]);

  const hasLogView = allSpans?.length > 0;
  const TAB_CONFIG = useMemo(() => {
    const tabs = [
      { key: "preview", label: "Preview", icon: "mdi:file-document-outline" },
    ];
    if (hasLogView) {
      tabs.push({
        key: "log",
        label: "Log View",
        icon: "mdi:format-list-bulleted",
      });
    }
    tabs.push(
      {
        key: "evals",
        label: "Evals",
        icon: "mdi:checkbox-marked-circle-outline",
      },
      { key: "annotations", label: "Annotations", icon: "mdi:pencil-outline" },
      { key: "events", label: "Events", icon: "mdi:bell-outline" },
    );
    return tabs;
  }, [hasLogView]);

  if (!span) return null;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* ── Span Header ────────────────────────────── */}
      <Box
        sx={{
          px: 2,
          pt: 1.5,
          pb: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Typography
                variant="body1"
                noWrap
                sx={{
                  fontSize: 13,
                  fontWeight: 500,
                  fontFamily: "'IBM Plex Sans', sans-serif",
                }}
              >
                {span.name || "unnamed"}
              </Typography>
              <IconButton
                size="small"
                sx={{ p: 0.25 }}
                onClick={() => copyText(span.name || "")}
              >
                <Iconify icon="tabler:copy" width={12} color="text.disabled" />
              </IconButton>
            </Stack>
            <Stack direction="row" alignItems="center" spacing={0.25}>
              <Typography
                variant="caption"
                sx={{
                  fontFamily: "monospace",
                  color: "text.secondary",
                  fontSize: 11,
                }}
              >
                {span.id}
              </Typography>
              <IconButton
                size="small"
                sx={{ p: 0.25 }}
                onClick={() => copyText(span.id || "")}
              >
                <Iconify icon="tabler:copy" width={11} color="text.disabled" />
              </IconButton>
            </Stack>
          </Box>

          {/* Actions button */}
          {onAction && (
            <Button
              size="small"
              variant="outlined"
              endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
              onClick={(e) => onAction("_open", e.currentTarget)}
              sx={{
                fontSize: 12,
                textTransform: "none",
                borderColor: "divider",
                color: "text.primary",
                height: 28,
                px: 1.5,
                "&:hover": { borderColor: "border.hover" },
              }}
            >
              Actions
            </Button>
          )}
        </Stack>

        {/* Metric chips */}
        <Stack direction="row" sx={{ mt: 1, flexWrap: "wrap", gap: "4px 8px" }}>
          {chips.map((c) => (
            <MetricChip key={c.label} label={c.label} value={c.value} />
          ))}
        </Stack>

        {/* Tags — span-level, inline add/remove */}
        <InlineTagsRow
          tags={span?.tags || []}
          traceId={span?.trace}
          spanId={span?.id}
        />
      </Box>

      {/* ── Tab Bar ────────────────────────────────── */}
      <Tabs
        value={activeTab}
        onChange={(_, v) => setActiveTab(v)}
        variant="scrollable"
        scrollButtons="auto"
        allowScrollButtonsMobile
        sx={{
          minHeight: 32,
          px: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          "& .MuiTabs-flexContainer": { gap: 0 },
          "& .MuiTab-root": {
            minHeight: 32,
            fontSize: 12,
            fontWeight: 500,
            textTransform: "none",
            minWidth: "unset !important",
            padding: "0 10px !important",
            marginRight: "0 !important",
            gap: "4px",
            color: "text.secondary",
            fontFamily: "'Inter', sans-serif",
            letterSpacing: 0,
          },
          "& .Mui-selected": { color: "primary.main", fontWeight: 600 },
          "& .MuiTabs-indicator": {
            backgroundColor: "primary.main",
            height: 2,
          },
          "& .MuiTabs-scrollButtons": {
            width: 24,
            "&.Mui-disabled": { opacity: 0.3 },
          },
        }}
      >
        {TAB_CONFIG.map((t) => (
          <Tab
            key={t.key}
            value={t.key}
            icon={<Iconify icon={t.icon} width={14} />}
            iconPosition="start"
            label={t.label}
          />
        ))}
      </Tabs>

      {/* ── Tab Content ────────────────────────────── */}
      <Box
        sx={{
          flex: 1,
          overflow: "auto",
          // The Preview tab's sticky search bar overlaps the top of this
          // scroll container. Reserve that space so scroll-to-match
          // (useSearchHighlight) doesn't park an active match behind it.
          scrollPaddingTop: 68,
        }}
      >
        {/* Preview Tab */}
        {activeTab === "preview" && (
          <Box sx={{ px: 2, py: 1.5 }}>
            {/* Search + Format toggle — pinned to the top of the scroll
                container so find-in-page controls stay reachable while
                the user scrolls through long content. */}
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                mb: 1.5,
                position: "sticky",
                top: 0,
                zIndex: 2,
                bgcolor: "background.paper",
                // Extend the background up into the parent padding so
                // nothing peeks through above the bar as it sticks.
                mx: -2,
                mt: -1.5,
                px: 2,
                py: 1.5,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.75,
                  px: 1.25,
                  py: 0.5,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "2px",
                  bgcolor: "background.paper",
                  flex: 1,
                }}
              >
                <Iconify icon="mdi:magnify" width={14} color="text.disabled" />
                <Box
                  component="input"
                  placeholder="Search"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && matchCount > 0) {
                      e.preventDefault();
                      if (e.shiftKey) prev();
                      else next();
                    }
                  }}
                  sx={{
                    border: "none",
                    outline: "none",
                    flex: 1,
                    fontSize: 11,
                    color: "text.primary",
                    bgcolor: "transparent",
                    "&::placeholder": { color: "text.disabled" },
                  }}
                />
                {searchQuery && (
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.25}
                    sx={{ flexShrink: 0 }}
                  >
                    <Typography
                      variant="caption"
                      sx={{
                        fontSize: 10,
                        color: matchCount ? "text.secondary" : "text.disabled",
                        minWidth: 42,
                        textAlign: "right",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {matchCount
                        ? `${activeIndex + 1} of ${matchCount}`
                        : "No matches"}
                    </Typography>
                    <IconButton
                      size="small"
                      disabled={!matchCount}
                      onClick={prev}
                      sx={{ p: 0.25 }}
                      aria-label="Previous match"
                    >
                      <Iconify icon="mdi:chevron-up" width={14} />
                    </IconButton>
                    <IconButton
                      size="small"
                      disabled={!matchCount}
                      onClick={next}
                      sx={{ p: 0.25 }}
                      aria-label="Next match"
                    >
                      <Iconify icon="mdi:chevron-down" width={14} />
                    </IconButton>
                  </Stack>
                )}
              </Box>
              <FormatToggle
                value={viewMode}
                onChange={setViewMode}
                modes={
                  isOpenAIMessages(input)
                    ? ["chat", "markdown", "json"]
                    : ["markdown", "json"]
                }
              />
            </Stack>

            <Box ref={previewContentRef}>
              <SmartPreview
                span={span}
                input={input}
                output={output}
                attributes={attributes}
                viewMode={viewMode}
                searchQuery={deferredSearchQuery}
                ContentCard={ContentCard}
                AttributesCard={AttributesCard}
                JsonPreviewBlock={JsonPreviewBlock}
                drawerOpen={drawerOpen}
              />
            </Box>
          </Box>
        )}

        {/* Log View Tab — only for root span */}
        {activeTab === "log" && (
          <LogViewTable
            allSpans={allSpans}
            traceStartTime={traceStartTime}
            drawerOpen={drawerOpen}
          />
        )}

        {/* Evals Tab — this span + child span evals. Rendered via the
            shared EvalsTabView component so the trace drawer and the
            voice drawer use the same eval UI. */}
        {activeTab === "evals" && (
          <EvalsTabView
            evals={collectAllEvalsFromEntry(entry)}
            onSelectSpan={onSelectSpan}
            emptyMessage="No evaluations for this span or its children"
            onFixWithFalcon={({ level, ev, failingEvals, allEvals }) => {
              const traceId = span?.trace;
              if (level === "eval" && ev) {
                openFixWithFalcon({
                  level: "eval",
                  context: {
                    trace_id: traceId,
                    span_id: ev.spanId || ev.observation_span_id || span?.id,
                    eval_log_id: ev.eval_log_id || ev.cell_id || ev.log_id,
                    custom_eval_config_id:
                      ev.custom_eval_config_id || ev.eval_config_id,
                    eval_name: ev.eval_name,
                    score: ev.score,
                    explanation: ev.explanation || ev.eval_explanation,
                    span_name: ev.spanName,
                    project_id: projectId,
                  },
                });
                return;
              }
              // Span-level or trace-level — include pass/fail summary so
              // Falcon can gate (no fabricated failures when everything passes).
              const total = (allEvals || []).length;
              const passCount = (allEvals || []).filter(
                (e) => e.score != null && e.score >= 50,
              ).length;
              openFixWithFalcon({
                level: isRootSpan ? "trace" : "span",
                context: {
                  trace_id: traceId,
                  span_id: isRootSpan ? undefined : span?.id,
                  span_name: isRootSpan ? undefined : span?.name,
                  evals_summary: `${passCount}/${total} passed`,
                  failing_evals: (failingEvals || []).map((e) => ({
                    name: e.eval_name,
                    score: e.score,
                  })),
                  project_id: projectId,
                },
              });
            }}
          />
        )}

        {/* Annotations Tab */}
        {activeTab === "annotations" && (
          <AnnotationsTabContent
            spanId={span?.id}
            traceId={span?.trace}
            onAction={onAction}
          />
        )}

        {/* Events Tab */}
        {activeTab === "events" && (
          <Box sx={{ p: 2 }}>
            {spanEvents?.length > 0 ? (
              <Stack spacing={1.5}>
                {spanEvents.map((evt, i) => (
                  <EventCard key={i} event={evt} />
                ))}
              </Stack>
            ) : (
              <Box sx={{ textAlign: "center", py: 4, color: "text.secondary" }}>
                <Iconify
                  icon="mdi:lightning-bolt-outline"
                  width={32}
                  sx={{ mb: 1, opacity: 0.4 }}
                />
                <Typography variant="body2" fontSize={12}>
                  No events recorded
                </Typography>
              </Box>
            )}
          </Box>
        )}
      </Box>
    </Box>
  );
};

/* ── deepMatch — recursive search through nested values ── */

function deepMatch(val, q) {
  if (val === null || val === undefined) return false;
  if (typeof val === "string") return val.toLowerCase().includes(q);
  if (typeof val === "number" || typeof val === "boolean")
    return String(val).toLowerCase().includes(q);
  if (Array.isArray(val)) return val.some((v) => deepMatch(v, q));
  if (typeof val === "object") {
    return Object.entries(val).some(
      ([k, v]) => k.toLowerCase().includes(q) || deepMatch(v, q),
    );
  }
  return false;
}

/* ── AttrValueCell — renders a value with expand/collapse ── */

const AttrValueCell = ({
  value,
  expanded,
  onToggle,
  searchQuery: _sq,
  path,
  spanId,
  traceId,
  audioCache,
}) => {
  if (value === null || value === undefined) {
    return (
      <Typography variant="caption" color="text.disabled" sx={{ fontSize: 11 }}>
        null
      </Typography>
    );
  }

  if (typeof value === "string" && value && path) {
    if (isImageAttrPath(path)) {
      return <ImageCard image={value} />;
    }
    if (isAudioAttrPath(path)) {
      return (
        <AudioCellRenderer
          value={value}
          cacheKey={`${traceId}-${spanId}::${path}`}
          getWaveSurferInstance={audioCache?.getWaveSurferInstance}
          storeWaveSurferInstance={audioCache?.storeWaveSurferInstance}
          updateWaveSurferInstance={audioCache?.updateWaveSurferInstance}
          editable={false}
        />
      );
    }
  }

  const isObj = typeof value === "object" && !Array.isArray(value);
  const isArr = Array.isArray(value);

  if (isObj || isArr) {
    const count = isArr ? value.length : Object.keys(value).length;
    if (count === 0) {
      return (
        <Typography
          variant="caption"
          color="text.disabled"
          sx={{ fontSize: 11 }}
        >
          empty {isArr ? "array" : "object"}
        </Typography>
      );
    }
    return (
      <Box>
        <Box
          onClick={onToggle}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            cursor: "pointer",
            "&:hover": { opacity: 0.7 },
          }}
        >
          <Iconify
            icon={expanded ? "mdi:chevron-down" : "mdi:chevron-right"}
            width={12}
            sx={{ color: "text.disabled" }}
          />
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontSize: 11 }}
          >
            {count} items
          </Typography>
        </Box>
        {expanded && (
          <Box
            sx={{
              ml: 1,
              mt: 0.25,
              borderLeft: "1px solid",
              borderColor: "divider",
              pl: 1,
            }}
          >
            {(isArr
              ? value.map((v, i) => [String(i), v])
              : Object.entries(value)
            ).map(([k, v]) => (
              <AttrNestedRow
                key={k}
                path={k}
                value={v}
                spanId={spanId}
                traceId={traceId}
                audioCache={audioCache}
              />
            ))}
          </Box>
        )}
      </Box>
    );
  }

  // Primitive value — highlight search matches
  const display = typeof value === "string" ? `"${value}"` : String(value);
  const sq = (_sq || "").trim().toLowerCase();
  const shouldHighlight = sq && display.toLowerCase().includes(sq);

  return (
    <Typography
      variant="caption"
      sx={{
        fontSize: 11,
        color: typeof value === "string" ? "#b5520a" : "text.primary",
        wordBreak: "break-all",
      }}
    >
      {shouldHighlight ? <Highlight text={display} query={sq} /> : display}
    </Typography>
  );
};

AttrValueCell.propTypes = {
  value: PropTypes.any,
  expanded: PropTypes.bool,
  onToggle: PropTypes.func,
  searchQuery: PropTypes.string,
  path: PropTypes.string,
  spanId: PropTypes.string,
  traceId: PropTypes.string,
  audioCache: PropTypes.object,
};

/* ── AttrNestedRow — recursive nested row ── */

const AttrNestedRow = ({ path, value, spanId, traceId, audioCache }) => {
  const [open, setOpen] = useState(false);
  const isComplex = value !== null && typeof value === "object";

  return (
    <Box sx={{ py: 0.15 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 0.5,
          cursor: isComplex ? "pointer" : "default",
          "&:hover": isComplex
            ? { bgcolor: "action.hover", borderRadius: "2px" }
            : {},
          px: 0.25,
          py: 0.1,
        }}
        onClick={(e) => {
          if (!isComplex) return;
          e.stopPropagation();
          setOpen(!open);
        }}
      >
        <Typography
          variant="caption"
          fontWeight={500}
          sx={{
            fontSize: 11,
            minWidth: 60,
            flexShrink: 0,
            color: "text.secondary",
          }}
        >
          {path}
        </Typography>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <AttrValueCell
            value={value}
            expanded={open}
            onToggle={() => setOpen(!open)}
            path={path}
            spanId={spanId}
            traceId={traceId}
            audioCache={audioCache}
          />
        </Box>
      </Box>
    </Box>
  );
};

AttrNestedRow.propTypes = {
  path: PropTypes.string,
  value: PropTypes.any,
  spanId: PropTypes.string,
  traceId: PropTypes.string,
  audioCache: PropTypes.object,
};

/* ── AttributesCard — searchable Path | Value table ───── */

const AttributesCard = ({
  attributes,
  searchQuery,
  hideInlineSearch = false,
  spanId,
  traceId,
  drawerOpen = true,
}) => {
  const [expanded, setExpanded] = useState(true);
  const [attrSearch, setAttrSearch] = useState("");
  const [expandedKeys, setExpandedKeys] = useState({});

  const {
    getWaveSurferInstance,
    storeWaveSurferInstance,
    updateWaveSurferInstance,
    clearWaveSurferCache,
  } = useWavesurferCache();

  // Drawer uses variant="persistent" and the card is reused across span/trace
  // navigation, so unmount cleanup never fires — re-run on each dep change to
  // destroy stale wavesurfer instances.
  useEffect(() => {
    return () => {
      clearWaveSurferCache();
    };
  }, [drawerOpen, traceId, spanId, clearWaveSurferCache]);

  const audioCache = useMemo(
    () => ({
      getWaveSurferInstance,
      storeWaveSurferInstance,
      updateWaveSurferInstance,
    }),
    [getWaveSurferInstance, storeWaveSurferInstance, updateWaveSurferInstance],
  );

  // Parse attributes if string
  const parsed = useMemo(() => {
    if (!attributes) return {};
    if (typeof attributes === "string") {
      try {
        return JSON.parse(attributes);
      } catch {
        return {};
      }
    }
    return attributes;
  }, [attributes]);

  const allEntries = Object.entries(parsed);

  // Filter by search. When inline search is hidden the parent owns the
  // query; otherwise prefer the inline input, falling back to the parent.
  const query = (
    hideInlineSearch ? searchQuery || "" : attrSearch || searchQuery || ""
  )
    .trim()
    .toLowerCase();
  const filteredEntries = query
    ? allEntries.filter(
        ([key, val]) =>
          key.toLowerCase().includes(query) || deepMatch(val, query),
      )
    : allEntries;

  const toggleKey = useCallback((key) => {
    setExpandedKeys((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  return (
    <SingleImageViewerProvider>
      <AudioPlaybackProvider>
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {/* Header — excluded from find-in-page */}
      <Stack
        data-search-skip="true"
        direction="row"
        alignItems="center"
        sx={{ px: 1.5, py: 0.75, cursor: "pointer" }}
        onClick={() => setExpanded((p) => !p)}
      >
        <Typography
          variant="body2"
          sx={{
            fontSize: 13,
            fontWeight: 500,
            fontFamily: "'IBM Plex Sans', sans-serif",
            flex: 1,
          }}
        >
          Attributes
        </Typography>
        <IconButton
          size="small"
          sx={{ p: 0.25 }}
          onClick={(e) => {
            e.stopPropagation();
            copyText(JSON.stringify(parsed, null, 2));
          }}
        >
          <Iconify icon="tabler:copy" width={14} color="text.disabled" />
        </IconButton>
        <Iconify
          icon={expanded ? "mdi:chevron-up" : "mdi:chevron-down"}
          width={16}
          sx={{ color: "text.disabled", ml: 0.25 }}
        />
      </Stack>

      <Collapse in={expanded}>
        {/* Search within attributes — hidden when parent owns the query */}
        {!hideInlineSearch && (
          <Box sx={{ px: 1.5, pb: 0.75 }}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                px: 1,
                py: 0.25,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "2px",
                bgcolor: "background.default",
              }}
            >
              <Iconify icon="mdi:magnify" width={12} color="text.disabled" />
              <Box
                component="input"
                placeholder="Search attributes..."
                value={attrSearch}
                onChange={(e) => setAttrSearch(e.target.value)}
                sx={{
                  border: "none",
                  outline: "none",
                  flex: 1,
                  fontSize: 11,
                  color: "text.primary",
                  bgcolor: "transparent",
                  py: 0.15,
                  "&::placeholder": { color: "text.disabled" },
                }}
              />
            </Box>
          </Box>
        )}

        <Box
          sx={{
            mx: 1.5,
            mb: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            overflow: "hidden",
          }}
        >
          {/* Table header — excluded from find-in-page */}
          <Box
            data-search-skip="true"
            sx={{
              display: "flex",
              px: 1.5,
              py: 0.5,
              bgcolor: "background.default",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              variant="caption"
              sx={{
                fontWeight: 600,
                fontSize: 11,
                width: "40%",
                flexShrink: 0,
              }}
            >
              Path
            </Typography>
            <Typography
              variant="caption"
              sx={{ fontWeight: 600, fontSize: 11, flex: 1 }}
            >
              Value
            </Typography>
          </Box>

          {/* Rows */}
          <Box sx={{ maxHeight: 350, overflowY: "auto" }}>
            {filteredEntries.length === 0 ? (
              <Typography
                variant="caption"
                color="text.disabled"
                sx={{ p: 1.5, display: "block", textAlign: "center" }}
              >
                {query ? "No matching attributes" : "No attributes"}
              </Typography>
            ) : (
              filteredEntries.map(([key, val]) => {
                const isObj =
                  val !== null &&
                  val !== undefined &&
                  typeof val === "object" &&
                  !Array.isArray(val);
                const isArr = Array.isArray(val);
                const isEmpty =
                  val === null ||
                  val === undefined ||
                  val === "" ||
                  (isObj && Object.keys(val).length === 0) ||
                  (isArr && val.length === 0);

                return (
                  <Box
                    key={key}
                    sx={{
                      display: "flex",
                      alignItems: "flex-start",
                      px: 1.5,
                      py: 0.5,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      "&:last-child": { borderBottom: "none" },
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Typography
                      variant="caption"
                      fontWeight={500}
                      noWrap
                      sx={{
                        width: "40%",
                        flexShrink: 0,
                        pt: 0.15,
                        fontSize: 11,
                        color: "text.secondary",
                      }}
                    >
                      {query && key.toLowerCase().includes(query) ? (
                        <Highlight text={key} query={query} />
                      ) : (
                        key
                      )}
                    </Typography>
                    <Box sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                      {isEmpty ? (
                        <Typography
                          variant="caption"
                          color="text.disabled"
                          sx={{ fontSize: 11 }}
                        >
                          {isObj || isArr
                            ? `empty ${isArr ? "array" : "object"}`
                            : "—"}
                        </Typography>
                      ) : (
                        <AttrValueCell
                          value={val}
                          expanded={expandedKeys[key]}
                          onToggle={() => toggleKey(key)}
                          searchQuery={query}
                          path={key}
                          spanId={spanId}
                          traceId={traceId}
                          audioCache={audioCache}
                        />
                      )}
                    </Box>
                  </Box>
                );
              })
            )}
          </Box>
        </Box>
      </Collapse>
    </Box>
      </AudioPlaybackProvider>
    </SingleImageViewerProvider>
  );
};

AttributesCard.propTypes = {
  attributes: PropTypes.object,
  searchQuery: PropTypes.string,
  hideInlineSearch: PropTypes.bool,
  spanId: PropTypes.string,
  traceId: PropTypes.string,
  drawerOpen: PropTypes.bool,
};

SpanDetailPane.propTypes = {
  entry: PropTypes.shape({
    observation_span: PropTypes.object,
    observationSpan: PropTypes.object,
    eval_scores: PropTypes.array,
    evalScores: PropTypes.array,
    annotations: PropTypes.array,
  }),
  allSpans: PropTypes.array,
  traceStartTime: PropTypes.any,
  isRootSpan: PropTypes.bool,
  traceTags: PropTypes.array,
  projectId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
  onAction: PropTypes.func,
  onSelectSpan: PropTypes.func,
  drawerOpen: PropTypes.bool,
};

export default React.memo(SpanDetailPane);
