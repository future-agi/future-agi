import React, {
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  CircularProgress,
  Collapse,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { useInfiniteQuery } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import {
  CategoryOptions,
  LevelOptions,
} from "src/components/CallLogsDetailDrawer/CallDetailLogs/common";
import NestedJsonTable, { deepMatch } from "./NestedJsonTable";

// ─────────────────────────────────────────────────────────────────────────────
// Level style map
// ─────────────────────────────────────────────────────────────────────────────

const LEVEL_STYLES = {
  ERROR: { label: "Error", color: "#EF4444", bg: "rgba(239, 68, 68, 0.08)" },
  WARN: { label: "Warn", color: "#F59E0B", bg: "rgba(245, 158, 11, 0.08)" },
  INFO: { label: "Info", color: "#3B82F6", bg: "rgba(59, 130, 246, 0.08)" },
  LOG: { label: "Log", color: "#64748B", bg: "rgba(100, 116, 139, 0.08)" },
  DEBUG: { label: "Debug", color: "#94A3B8", bg: "rgba(148, 163, 184, 0.08)" },
};

const levelStyle = (level) =>
  LEVEL_STYLES[String(level || "").toUpperCase()] || LEVEL_STYLES.LOG;

// ─────────────────────────────────────────────────────────────────────────────
// Row normalization — API and span-attributes shapes both feed in here
// ─────────────────────────────────────────────────────────────────────────────

const normalizeRow = (row, i) => {
  const severity = (row?.severityText || row?.severity_text || row?.level || "")
    .toString()
    .toUpperCase();
  return {
    id: row?.id ?? `log-${i}`,
    loggedAt: row?.loggedAt || row?.logged_at || row?.timestamp || null,
    severity,
    category: row?.category || "",
    body: row?.body || row?.message || "",
    attributes: row?.attributes || row?.payload || {},
    raw: row,
  };
};

// Format the timestamp exactly the way the user asked: a full wall-clock
// string, monospace-friendly. Falls back to the raw value if Date parsing
// fails.
const fmtTs = (value) => {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
    `.${pad(d.getMilliseconds(), 3)}`
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Level pill row
// ─────────────────────────────────────────────────────────────────────────────

const LevelPill = ({ label, count, color, active, onClick }) => (
  <Box
    onClick={onClick}
    sx={{
      display: "inline-flex",
      alignItems: "center",
      gap: 0.5,
      px: 0.75,
      py: "3px",
      borderRadius: "12px",
      cursor: "pointer",
      border: "1px solid",
      borderColor: active ? color : "divider",
      bgcolor: active ? `${color}14` : "transparent",
      transition: "all 120ms",
      "&:hover": { borderColor: color, bgcolor: `${color}0a` },
    }}
  >
    {color && (
      <Box
        sx={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          bgcolor: color,
        }}
      />
    )}
    <Typography
      sx={{
        fontSize: 10.5,
        fontWeight: active ? 600 : 500,
        color: active ? color : "text.secondary",
        lineHeight: 1,
      }}
    >
      {label}
    </Typography>
    <Typography
      sx={{
        fontSize: 10,
        color: active ? color : "text.disabled",
        fontFamily: "monospace",
        lineHeight: 1,
      }}
    >
      {count}
    </Typography>
  </Box>
);

LevelPill.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  color: PropTypes.string,
  active: PropTypes.bool,
  onClick: PropTypes.func,
};

// ─────────────────────────────────────────────────────────────────────────────
// Attribute table — flat key/value renderer for the expanded row
// ─────────────────────────────────────────────────────────────────────────────

const LogAttributes = ({ attributes, body }) => {
  const [query, setQuery] = useState("");
  // `appliedQuery` is debounced + transitioned so the expensive deep
  // filter runs once per typing burst, not once per keystroke — same
  // pattern used in `AttributesTable`.
  const [appliedQuery, setAppliedQuery] = useState("");
  const [, startFilterTransition] = useTransition();
  const debounceRef = useRef(null);

  const handleQueryChange = (e) => {
    const value = e.target.value;
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      startFilterTransition(() => setAppliedQuery(value));
    }, 120);
  };

  const handleClearQuery = (e) => {
    e.stopPropagation();
    setQuery("");
    if (debounceRef.current) clearTimeout(debounceRef.current);
    startFilterTransition(() => setAppliedQuery(""));
  };

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  const hasAttrs =
    attributes &&
    typeof attributes === "object" &&
    Object.keys(attributes).length > 0;
  const q = appliedQuery.trim();

  return (
    <Box
      sx={{
        bgcolor: "background.default",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        mt: 0.5,
        mx: 1.25,
        mb: 0.75,
        p: 1,
        // NestedJsonTable's value column enforces a minWidth so primitives
        // don't wrap character-by-line; when the pane is narrow the row
        // needs a horizontal scroll container here.
        overflowX: "auto",
      }}
    >
      {(body || hasAttrs) && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "3px",
            px: 0.75,
            py: 0.25,
            bgcolor: "background.paper",
            mb: 0.75,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <Iconify icon="mdi:magnify" width={12} color="text.disabled" />
          <Box
            component="input"
            placeholder="Filter attributes (deep)"
            value={query}
            onChange={handleQueryChange}
            onClick={(e) => e.stopPropagation()}
            sx={{
              flex: 1,
              border: "none",
              outline: "none",
              bgcolor: "transparent",
              fontSize: 10.5,
              color: "text.primary",
              fontFamily: "inherit",
              py: 0,
              minWidth: 0,
              "&::placeholder": { color: "text.disabled" },
            }}
          />
          {query && (
            <Iconify
              icon="mdi:close"
              width={11}
              onClick={handleClearQuery}
              sx={{
                cursor: "pointer",
                color: "text.disabled",
                "&:hover": { color: "text.primary" },
              }}
            />
          )}
        </Box>
      )}

      {/* Token-based match (all whitespace-separated terms must appear)
          keeps the body filter aligned with the attribute search below.
          `deepMatch` already lowercases each token so `q` goes through
          verbatim. */}
      {body && (!q || deepMatch(body, q)) && (
        <Box sx={{ mb: hasAttrs ? 1 : 0 }}>
          <Typography
            sx={{
              fontSize: 10,
              fontWeight: 600,
              color: "text.secondary",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              mb: 0.25,
            }}
          >
            Message
          </Typography>
          <Typography
            sx={{
              fontSize: 11,
              color: "text.primary",
              fontFamily: "monospace",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {body}
          </Typography>
        </Box>
      )}

      {hasAttrs && (
        <NestedJsonTable
          data={attributes}
          searchQuery={appliedQuery}
          emptyMessage="No attributes"
        />
      )}
    </Box>
  );
};

LogAttributes.propTypes = {
  attributes: PropTypes.object,
  body: PropTypes.string,
};

// ─────────────────────────────────────────────────────────────────────────────
// One log line
// ─────────────────────────────────────────────────────────────────────────────

const LogLine = ({ row, expanded, onToggle, onCategoryClick }) => {
  const s = levelStyle(row.severity);
  return (
    <Box
      sx={{
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box
        onClick={onToggle}
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 1,
          pl: "4px",
          pr: 1,
          py: 0.75,
          cursor: "pointer",
          borderLeft: "3px solid",
          borderLeftColor: s.color,
          bgcolor: expanded ? `${s.color}0a` : "transparent",
          "&:hover": { bgcolor: expanded ? `${s.color}14` : "action.hover" },
          transition: "background-color 80ms",
        }}
      >
        <Iconify
          icon="mdi:chevron-right"
          width={12}
          sx={{
            color: "text.disabled",
            flexShrink: 0,
            mt: "2px",
            transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
            transition: "transform 120ms",
          }}
        />

        {/* Full timestamp — monospace, fixed-width so everything aligns */}
        <Typography
          sx={{
            fontSize: 10.5,
            fontFamily: "monospace",
            color: "text.disabled",
            flexShrink: 0,
            lineHeight: "16px",
            letterSpacing: "-0.01em",
          }}
        >
          {fmtTs(row.loggedAt)}
        </Typography>

        {/* Level label */}
        <Typography
          sx={{
            fontSize: 9.5,
            fontWeight: 700,
            color: s.color,
            fontFamily: "monospace",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            flexShrink: 0,
            minWidth: 36,
            lineHeight: "16px",
          }}
        >
          {s.label}
        </Typography>

        {/* Category pill — click-to-filter */}
        {row.category && (
          <Box
            onClick={(e) => {
              e.stopPropagation();
              onCategoryClick?.(row.category);
            }}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              px: 0.5,
              borderRadius: "3px",
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              flexShrink: 0,
              cursor: "pointer",
              height: 16,
              "&:hover": { borderColor: "primary.main" },
            }}
          >
            <Typography
              sx={{
                fontSize: 9.5,
                fontWeight: 500,
                color: "text.secondary",
                textTransform: "lowercase",
                letterSpacing: "0.02em",
                lineHeight: 1,
                fontFamily: "monospace",
              }}
            >
              {row.category}
            </Typography>
          </Box>
        )}

        {/* Message — single line, truncates when collapsed */}
        <Typography
          sx={{
            flex: 1,
            minWidth: 0,
            fontSize: 11.5,
            fontFamily: "monospace",
            color: "text.primary",
            lineHeight: "16px",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {row.body || (
            <Box component="span" sx={{ color: "text.disabled" }}>
              (no message)
            </Box>
          )}
        </Typography>
      </Box>
      <Collapse in={expanded} unmountOnExit>
        <LogAttributes attributes={row.attributes} body={row.body} />
      </Collapse>
    </Box>
  );
};

LogLine.propTypes = {
  row: PropTypes.object.isRequired,
  expanded: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  onCategoryClick: PropTypes.func,
};

// ─────────────────────────────────────────────────────────────────────────────
// Main view
// ─────────────────────────────────────────────────────────────────────────────

const VoiceLogsView = ({ callLogId, vapiId, module, callLogs }) => {
  const theme = useTheme();
  const [search, setSearch] = useState("");
  const [level, setLevel] = useState("");
  const [category, setCategory] = useState("");
  const [expanded, setExpanded] = useState(new Set());

  const useClientSide = Boolean(callLogs);

  // Server-side paginated logs — useInfiniteQuery so the "Load more"
  // button stacks pages instead of replacing them. Mirrors the AG-Grid
  // server-side datasource used by CallDetailLogGrid but in a form that
  // renders as a list rather than a data grid.
  const serverQuery = useInfiniteQuery({
    queryKey: [
      "voice-logs",
      module,
      module === "simulate" ? callLogId : vapiId,
      search,
      level,
      category,
    ],
    queryFn: async ({ pageParam = 1 }) => {
      const id = module === "simulate" ? callLogId : vapiId;
      const { data } = await axios.get(
        endpoints.testExecutions.getDetailLogs(id),
        {
          params: {
            page: pageParam,
            search,
            ...(level ? { severity_text: level } : {}),
            ...(category ? { category } : {}),
            ...(module === "project" ? { vapi_call_id: vapiId } : {}),
          },
        },
      );
      return data;
    },
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce(
        (sum, p) => sum + (p?.results?.results?.length || 0),
        0,
      );
      const total = lastPage?.count || 0;
      return loaded < total ? allPages.length + 1 : undefined;
    },
    enabled: !useClientSide && !!(module === "simulate" ? callLogId : vapiId),
    initialPageParam: 1,
    staleTime: 30_000,
    refetchInterval: (query) => {
      const pages = query.state.data?.pages || [];
      return pages.some((p) => p?.results?.ingestion_pending) ? 2000 : false;
    },
    meta: { errorHandled: true },
  });

  const allRows = useMemo(() => {
    if (useClientSide) {
      let rows = callLogs || [];
      const q = search.trim().toLowerCase();
      if (q) {
        rows = rows.filter((r) => {
          const body = (r.body || r.message || "").toLowerCase();
          const cat = (r.category || "").toLowerCase();
          const lvl = (
            r.severityText ||
            r.severity_text ||
            r.level ||
            ""
          ).toLowerCase();
          return (
            body.includes(q) ||
            cat.includes(q) ||
            lvl.includes(q) ||
            JSON.stringify(r.attributes || r.payload || "")
              .toLowerCase()
              .includes(q)
          );
        });
      }
      if (level) {
        rows = rows.filter(
          (r) =>
            (
              r.severityText ||
              r.severity_text ||
              r.level ||
              ""
            ).toUpperCase() === level.toUpperCase(),
        );
      }
      if (category) {
        rows = rows.filter(
          (r) => (r.category || "").toLowerCase() === category.toLowerCase(),
        );
      }
      return rows.map(normalizeRow);
    }
    const pages = serverQuery.data?.pages || [];
    const flat = pages.flatMap((p) => p?.results?.results || []);
    return flat.map(normalizeRow);
  }, [useClientSide, callLogs, search, level, category, serverQuery.data]);

  // Level counts for the pill row — drive off the full source, not the
  // currently-filtered list, so pills always show the real distribution
  // across the loaded pages.
  const levelCounts = useMemo(() => {
    const base = useClientSide
      ? callLogs || []
      : (serverQuery.data?.pages || []).flatMap(
          (p) => p?.results?.results || [],
        );
    const counts = { ALL: base.length };
    LevelOptions.forEach((lo) => {
      counts[lo.value] = 0;
    });
    base.forEach((r) => {
      const k = (
        r.severityText ||
        r.severity_text ||
        r.level ||
        ""
      ).toUpperCase();
      if (counts[k] == null) counts[k] = 0;
      counts[k] += 1;
    });
    return counts;
  }, [useClientSide, callLogs, serverQuery.data]);

  const totalCount = useClientSide
    ? callLogs?.length || 0
    : serverQuery.data?.pages?.[0]?.count || 0;
  const ingestionPending =
    !useClientSide &&
    (serverQuery.data?.pages || []).some((p) => p?.results?.ingestion_pending);

  const handleToggle = (id) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleLevelClick = (lvl) => {
    setLevel((prev) => (prev === lvl ? "" : lvl));
  };

  const handleCategoryClick = (cat) => {
    setCategory((prev) => (prev === cat ? "" : cat));
  };

  const loading =
    !useClientSide && serverQuery.isFetching && !serverQuery.isFetchingNextPage;

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
      {/* Toolbar — search on one row, level pills on the next */}
      <Stack
        gap={0.75}
        sx={{
          px: 1,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.default",
          flexShrink: 0,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Box
            sx={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              px: 1,
              py: 0.25,
              bgcolor: "background.paper",
            }}
          >
            <Iconify icon="mdi:magnify" width={13} color="text.disabled" />
            <Box
              component="input"
              placeholder="Search logs"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              sx={{
                flex: 1,
                border: "none",
                outline: "none",
                bgcolor: "transparent",
                fontSize: 11,
                color: "text.primary",
                fontFamily: "inherit",
                py: 0.25,
                "&::placeholder": { color: "text.disabled" },
              }}
            />
            {search && (
              <Iconify
                icon="mdi:close"
                width={12}
                onClick={() => setSearch("")}
                sx={{
                  cursor: "pointer",
                  color: "text.disabled",
                  "&:hover": { color: "text.primary" },
                }}
              />
            )}
          </Box>

          {/* Category filter — compact select chip that doubles as a
              status display when a category is selected */}
          {category ? (
            <Box
              onClick={() => setCategory("")}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.25,
                px: 0.75,
                py: 0.25,
                borderRadius: "3px",
                border: "1px solid",
                borderColor: "primary.main",
                color: "primary.main",
                cursor: "pointer",
                fontSize: 10.5,
                fontFamily: "monospace",
                flexShrink: 0,
              }}
            >
              {category}
              <Iconify icon="mdi:close" width={11} />
            </Box>
          ) : null}
        </Stack>

        {/* Level pill row + total */}
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ flexWrap: "wrap" }}
        >
          <LevelPill
            label="All"
            count={levelCounts.ALL || 0}
            color={theme.palette.text.disabled}
            active={!level}
            onClick={() => setLevel("")}
          />
          {LevelOptions.map((lo) => {
            const s = levelStyle(lo.value);
            const count = levelCounts[lo.value] || 0;
            return (
              <LevelPill
                key={lo.value}
                label={s.label}
                count={count}
                color={s.color}
                active={level === lo.value}
                onClick={() => handleLevelClick(lo.value)}
              />
            );
          })}
          <Box sx={{ flex: 1 }} />
          <Typography
            sx={{
              fontSize: 10,
              color: "text.disabled",
              fontFamily: "monospace",
            }}
          >
            {totalCount} logs
          </Typography>
        </Stack>
      </Stack>

      {/* Log lines */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          bgcolor: "background.paper",
        }}
      >
        {loading ? (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              minHeight: 120,
            }}
          >
            <CircularProgress size={18} thickness={5} />
          </Box>
        ) : ingestionPending && allRows.length === 0 ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              minHeight: 160,
              p: 2,
              gap: 0.75,
            }}
          >
            <CircularProgress size={18} thickness={5} />
            <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
              Collecting logs from provider
            </Typography>
            <Typography sx={{ fontSize: 10.5, color: "text.disabled" }}>
              This usually takes a few seconds
            </Typography>
          </Box>
        ) : allRows.length === 0 ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              minHeight: 160,
              p: 2,
              gap: 0.5,
            }}
          >
            <Iconify
              icon="mdi:text-box-search-outline"
              width={22}
              sx={{ color: "text.disabled" }}
            />
            <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
              No logs match the filters
            </Typography>
            <Typography sx={{ fontSize: 10.5, color: "text.disabled" }}>
              Try clearing level / category / search above
            </Typography>
          </Box>
        ) : (
          allRows.map((row) => (
            <LogLine
              key={row.id}
              row={row}
              expanded={expanded.has(row.id)}
              onToggle={() => handleToggle(row.id)}
              onCategoryClick={handleCategoryClick}
            />
          ))
        )}

        {/* Load more */}
        {!useClientSide && serverQuery.hasNextPage && (
          <Box sx={{ p: 1, display: "flex", justifyContent: "center" }}>
            <Button
              size="small"
              variant="outlined"
              onClick={() => serverQuery.fetchNextPage()}
              disabled={serverQuery.isFetchingNextPage}
              sx={{
                textTransform: "none",
                fontSize: 11,
                py: 0.25,
                px: 1.25,
              }}
            >
              {serverQuery.isFetchingNextPage ? (
                <Stack direction="row" alignItems="center" gap={0.75}>
                  <CircularProgress size={10} thickness={5} />
                  Loading
                </Stack>
              ) : (
                "Load more"
              )}
            </Button>
          </Box>
        )}
      </Box>
    </Stack>
  );
};

VoiceLogsView.propTypes = {
  callLogId: PropTypes.string,
  vapiId: PropTypes.string,
  module: PropTypes.string,
  callLogs: PropTypes.array,
};

// Keep `CategoryOptions` as an import so it's not tree-shaken out when we
// decide to add a dropdown filter back. Silences the linter without
// adding a throwaway usage.
export { CategoryOptions };

export default VoiceLogsView;
