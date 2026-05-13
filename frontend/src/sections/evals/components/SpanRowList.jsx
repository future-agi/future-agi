import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { canonicalEntries } from "src/utils/utils";
import { JsonValueTree } from "./DatasetTestMode";

// Priority order for the per-span attribute table when a span is
// expanded. Mirrors the priority used at the top-level row table so
// the most-relevant fields land at the top regardless of how the BE
// orders them.
const PRIORITY_KEYS = ["span_attributes", "input", "output", "metadata"];
function sortEntries(entries) {
  return [...entries].sort(([a], [b]) => {
    const ai = PRIORITY_KEYS.indexOf(a);
    const bi = PRIORITY_KEYS.indexOf(b);
    if (ai !== -1 && bi !== -1) return ai - bi;
    if (ai !== -1) return -1;
    if (bi !== -1) return 1;
    return 0;
  });
}

// Recursive substring match — used by the per-span attribute filter so
// the table-search input also reaches into nested object values.
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

/**
 * Collapsible list of spans for the trace / session preview surfaces.
 *
 * Each span renders as a header row (index badge + type icon + name +
 * model / status / tokens / latency chips) that toggles a sub-table of
 * the span's attributes when clicked. Indentation reflects depth in the
 * original observation_spans tree (set by the caller via flattenSpanTree).
 *
 * Used by TaskLivePreview (the right pane on the eval task create page)
 * and TracingTestMode (the eval picker drawer's preview pane). Both
 * surfaces feed it the same `spanDetail.spans` shape.
 */
const SpanRowList = ({
  spans,
  expandedCols,
  setExpandedCols,
  tableSearch = "",
}) => {
  if (!spans?.length) return null;

  return (
    <>
      {spans.map((span, idx) => {
        const spanKey = `span-${span.id || idx}`;
        const depth = span._depth || 0;
        const indent = depth * 16;
        const spanName = span.name || span.span_name || "span";
        const spanType = span.observation_type || "";
        const isExpanded = expandedCols[spanKey];
        const hasDuplicateName = (span._nameTotal || 1) > 1;
        const nameLabel = hasDuplicateName
          ? `${spanName} #${span._nameIndex}`
          : spanName;

        return (
          <Box key={spanKey}>
            {/* Span header row */}
            <Box
              onClick={() =>
                setExpandedCols((prev) => ({
                  ...prev,
                  [spanKey]: !prev[spanKey],
                }))
              }
              sx={{
                display: "flex",
                alignItems: "center",
                px: 1.5,
                py: 0.75,
                pl: `${12 + indent}px`,
                borderBottom: "1px solid",
                borderColor: "divider",
                cursor: "pointer",
                backgroundColor: (theme) =>
                  theme.palette.mode === "dark"
                    ? "rgba(124,77,255,0.04)"
                    : "rgba(124,77,255,0.02)",
                "&:hover": { backgroundColor: "action.hover" },
                gap: 0.75,
              }}
            >
              <Iconify
                icon={isExpanded ? "mdi:chevron-down" : "mdi:chevron-right"}
                width={14}
                sx={{ color: "text.disabled", flexShrink: 0 }}
              />
              {/* Global index badge */}
              <Box
                sx={{
                  minWidth: 18,
                  height: 18,
                  borderRadius: "4px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: (theme) =>
                    theme.palette.mode === "dark"
                      ? "rgba(255,255,255,0.08)"
                      : "rgba(0,0,0,0.06)",
                  flexShrink: 0,
                }}
              >
                <Typography
                  sx={{
                    fontSize: "10px",
                    fontWeight: 700,
                    color: "text.secondary",
                  }}
                >
                  {idx + 1}
                </Typography>
              </Box>
              {/* Type icon */}
              <Iconify
                icon={
                  spanType === "GENERATION"
                    ? "mdi:creation"
                    : spanType === "TOOL"
                      ? "mdi:wrench"
                      : spanType === "RETRIEVAL"
                        ? "mdi:database-search"
                        : "mdi:layers-outline"
                }
                width={14}
                sx={{
                  color:
                    spanType === "GENERATION"
                      ? "primary.main"
                      : spanType === "TOOL"
                        ? "warning.main"
                        : spanType === "RETRIEVAL"
                          ? "info.main"
                          : "text.secondary",
                  flexShrink: 0,
                }}
              />
              <Typography
                variant="caption"
                fontWeight={600}
                sx={{ fontSize: "12px" }}
                noWrap
              >
                {nameLabel}
              </Typography>
              {/* Model chip */}
              {span.model && (
                <Box
                  sx={{
                    px: 0.75,
                    py: 0.1,
                    borderRadius: "4px",
                    flexShrink: 0,
                    backgroundColor: (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(255,255,255,0.06)"
                        : "rgba(0,0,0,0.04)",
                  }}
                >
                  <Typography
                    sx={{
                      fontSize: "10px",
                      color: "text.secondary",
                      fontFamily: "monospace",
                    }}
                  >
                    {span.model}
                  </Typography>
                </Box>
              )}
              {/* Status (only when it's not OK — fewer chips when
                  everything's normal) */}
              {span.status && span.status !== "OK" && (
                <Box
                  sx={(t) => ({
                    px: 0.5,
                    py: 0.1,
                    borderRadius: "4px",
                    backgroundColor:
                      span.status === "ERROR"
                        ? alpha(
                            t.palette.error.main,
                            t.palette.mode === "dark" ? 0.16 : 0.08,
                          )
                        : "transparent",
                  })}
                >
                  <Typography
                    sx={{
                      fontSize: "9px",
                      fontWeight: 600,
                      color:
                        span.status === "ERROR"
                          ? "error.main"
                          : "text.disabled",
                    }}
                  >
                    {span.status}
                  </Typography>
                </Box>
              )}
              {/* Tokens */}
              {span.total_tokens > 0 && (
                <Typography
                  sx={{ fontSize: "10px", color: "text.disabled" }}
                >
                  {span.total_tokens}tok
                </Typography>
              )}
              {/* Latency — pushed to right */}
              {span.latency_ms != null && (
                <Typography
                  sx={{
                    fontSize: "10px",
                    color: "text.disabled",
                    ml: "auto",
                    flexShrink: 0,
                  }}
                >
                  {span.latency_ms}ms
                </Typography>
              )}
            </Box>

            {/* Expanded span attributes — soft-flatten span_attributes so
                the dotted GenAI keys (gen_ai.output.messages.0.message.content,
                gen_ai.input.…) appear as direct rows alongside the span's
                top-level fields, instead of being hidden one click deeper
                inside a collapsed `span_attributes` JsonValueTree. Mirrors
                the same flatten the top-level RowDetailTable does for the
                spans row type. Top-level keys win the dedupe so a top-level
                `input` shadows `span_attributes.input`. */}
            {isExpanded && (
              <Box
                sx={{
                  pl: `${24 + indent}px`,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                {(() => {
                  const raw = canonicalEntries(span).filter(
                    ([k]) => !k.startsWith("_"),
                  );
                  const spanAttrs = span?.span_attributes;
                  if (
                    !spanAttrs ||
                    typeof spanAttrs !== "object" ||
                    Array.isArray(spanAttrs)
                  ) {
                    return sortEntries(raw);
                  }
                  const topKeys = new Set(raw.map(([k]) => k));
                  const flattened = raw.filter(([k]) => k !== "span_attributes");
                  for (const [k, v] of canonicalEntries(spanAttrs)) {
                    if (!topKeys.has(k)) {
                      flattened.push([k, v]);
                    }
                  }
                  return sortEntries(flattened);
                })()
                  .filter(([k, v]) => {
                    if (!tableSearch.trim()) return true;
                    const q = tableSearch.toLowerCase();
                    return (
                      k.toLowerCase().includes(q) || deepMatch(v, q)
                    );
                  })
                  .map(([k, v]) => {
                    const isO =
                      v !== null &&
                      v !== undefined &&
                      typeof v === "object";
                    const emp =
                      v === null ||
                      v === undefined ||
                      v === "" ||
                      (isO &&
                        !Array.isArray(v) &&
                        Object.keys(v).length === 0);
                    return (
                      <Box
                        key={k}
                        sx={{
                          display: "flex",
                          alignItems: "flex-start",
                          px: 1.5,
                          py: 0.4,
                          "&:hover": { backgroundColor: "action.hover" },
                        }}
                      >
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          noWrap
                          sx={{
                            width: 120,
                            flexShrink: 0,
                            pt: 0.15,
                            fontSize: "11px",
                          }}
                        >
                          {k}
                        </Typography>
                        <Box
                          sx={{
                            flex: 1,
                            minWidth: 0,
                            overflow: "hidden",
                          }}
                        >
                          {emp ? (
                            <Typography
                              variant="caption"
                              color="text.disabled"
                              sx={{ fontSize: "11px" }}
                            >
                              —
                            </Typography>
                          ) : isO ? (
                            <JsonValueTree
                              value={v}
                              expanded={expandedCols[`${spanKey}-${k}`]}
                              onToggle={() =>
                                setExpandedCols((prev) => ({
                                  ...prev,
                                  [`${spanKey}-${k}`]:
                                    !prev[`${spanKey}-${k}`],
                                }))
                              }
                            />
                          ) : (
                            <Typography
                              variant="caption"
                              color="primary.main"
                              sx={{
                                fontSize: "11px",
                                wordBreak: "break-all",
                              }}
                            >
                              {typeof v === "boolean"
                                ? String(v)
                                : typeof v === "string"
                                  ? `"${v}"`
                                  : String(v)}
                            </Typography>
                          )}
                        </Box>
                      </Box>
                    );
                  })}
              </Box>
            )}
          </Box>
        );
      })}
    </>
  );
};

SpanRowList.propTypes = {
  spans: PropTypes.array,
  expandedCols: PropTypes.object.isRequired,
  setExpandedCols: PropTypes.func.isRequired,
  tableSearch: PropTypes.string,
};

export default SpanRowList;
