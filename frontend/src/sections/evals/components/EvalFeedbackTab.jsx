import {
  Box,
  Chip,
  IconButton,
  Slide,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import Editor from "@monaco-editor/react";
import { useQueryClient } from "@tanstack/react-query";
import { DataTable, DataTablePagination } from "src/components/data-table";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import { useDebounce } from "src/hooks/use-debounce";
import AddEvalsFeedbackDrawer from "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer";

import { useEvalFeedbackList } from "../hooks/useEvalFeedback";

// ── Columns ──
const useColumns = () =>
  useMemo(
    () => [
      {
        id: "indicator",
        accessorKey: "value",
        header: "",
        size: 4,
        enableSorting: false,
        cell: ({ getValue }) => {
          const v = getValue();
          const color =
            v === "passed" ? "#22c55e" : v === "failed" ? "#ef4444" : "#94a3b8";
          return (
            <Box
              sx={{
                width: 3,
                height: 28,
                borderRadius: 1,
                backgroundColor: color,
              }}
            />
          );
        },
      },
      {
        id: "value",
        accessorKey: "value",
        header: "Feedback",
        size: 110,
        cell: ({ getValue }) => {
          const v = getValue();
          const isPassed = v === "passed";
          const isFailed = v === "failed";
          return (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <Iconify
                icon={
                  isPassed
                    ? "mingcute:thumb-up-2-fill"
                    : isFailed
                      ? "mingcute:thumb-down-2-fill"
                      : "mingcute:chat-3-line"
                }
                width={14}
                sx={{
                  color: isPassed
                    ? "success.main"
                    : isFailed
                      ? "error.main"
                      : "text.secondary",
                }}
              />
              <Chip
                label={isPassed ? "Correct" : isFailed ? "Incorrect" : v}
                size="small"
                color={isPassed ? "success" : isFailed ? "error" : "default"}
                variant="outlined"
                sx={{ fontSize: "11px", height: 20 }}
              />
            </Box>
          );
        },
      },
      {
        id: "explanation",
        accessorKey: "explanation",
        header: "Improvement Note",
        meta: { flex: 2 },
        minSize: 200,
        cell: ({ getValue }) => (
          <Typography
            variant="body2"
            noWrap
            sx={{
              fontSize: "12px",
              color: "text.secondary",
              fontStyle: "italic",
            }}
          >
            {getValue() || "—"}
          </Typography>
        ),
      },
      {
        id: "actionType",
        accessorKey: "action_type",
        header: "Action",
        size: 120,
        cell: ({ getValue }) => {
          const v = getValue();
          if (!v) return null;
          return (
            <Chip
              label={
                v === "retune"
                  ? "Re-tune"
                  : v === "recalculate"
                    ? "Re-calculate"
                    : v
              }
              size="small"
              variant="outlined"
              sx={{ fontSize: "10px", height: 18 }}
            />
          );
        },
      },
      {
        id: "source",
        accessorKey: "source",
        header: "Source",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue();
          if (!v) return null;
          const label =
            v === "eval_playground"
              ? "Playground"
              : v === "dataset"
                ? "Dataset"
                : v;
          return (
            <Chip
              label={label}
              size="small"
              variant="outlined"
              sx={{ fontSize: "10px", height: 18 }}
            />
          );
        },
      },
      {
        id: "userName",
        accessorKey: "user_name",
        header: "By",
        size: 120,
        cell: ({ getValue }) => (
          <Typography
            variant="body2"
            noWrap
            sx={{ fontSize: "12px", color: "text.secondary" }}
          >
            {getValue() || "—"}
          </Typography>
        ),
      },
      {
        id: "createdAt",
        accessorKey: "created_at",
        header: "Date",
        size: 140,
        cell: ({ getValue }) => {
          const v = getValue();
          if (!v) return null;
          const d = new Date(v);
          return (
            <Typography
              variant="body2"
              noWrap
              sx={{ fontSize: "11px", color: "text.disabled" }}
            >
              {d.toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
              })}
              ,{" "}
              {d.toLocaleTimeString(undefined, {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </Typography>
          );
        },
      },
    ],
    [],
  );

// ── Main ──
const EvalFeedbackTab = ({ templateId }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const queryClient = useQueryClient();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [searchQuery, setSearchQuery] = useState("");
  const [detailIndex, setDetailIndex] = useState(null);
  const [feedbackEditOpen, setFeedbackEditOpen] = useState(false);
  const debouncedSearch = useDebounce(searchQuery.trim(), 400);

  const { data, isLoading, isFetching } = useEvalFeedbackList(templateId, {
    page,
    pageSize,
  });
  const items = data?.items || [];
  const total = data?.total || 0;

  const filteredItems = useMemo(() => {
    if (!debouncedSearch) return items;
    const q = debouncedSearch.toLowerCase();
    return items.filter(
      (f) =>
        f.value?.toLowerCase().includes(q) ||
        f.explanation?.toLowerCase().includes(q) ||
        f.user_name?.toLowerCase().includes(q),
    );
  }, [items, debouncedSearch]);

  const columns = useColumns();
  const handleRowClick = useCallback(
    (row) => {
      const idx = filteredItems.findIndex((f) => f.id === row.id);
      setDetailIndex(idx >= 0 ? idx : 0);
    },
    [filteredItems],
  );

  const detailRow = detailIndex !== null ? filteredItems[detailIndex] : null;

  const handleFeedbackSubmitted = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["evals", "feedback-list", templateId],
    });
  }, [queryClient, templateId]);

  // j/k navigation
  React.useEffect(() => {
    if (detailIndex === null) return;
    const handler = (e) => {
      const tag = e.target?.tagName;
      const isEditable =
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        e.target?.isContentEditable;
      if (isEditable) return;
      if (e.key === "k") {
        e.preventDefault();
        setDetailIndex((i) => Math.max(0, (i ?? 0) - 1));
      } else if (e.key === "j") {
        e.preventDefault();
        setDetailIndex((i) => Math.min(filteredItems.length - 1, (i ?? 0) + 1));
      } else if (e.key === "Escape") {
        setDetailIndex(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [detailIndex, filteredItems.length]);

  return (
    <Box sx={{ display: "flex", height: "100%", minHeight: 0 }}>
      {/* Main table */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          minWidth: 0,
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            mb: 1,
            flexShrink: 0,
          }}
        >
          <Typography variant="body2" fontWeight={600}>
            Feedback History
            {isFetching && (
              <Typography
                component="span"
                variant="caption"
                color="text.disabled"
                sx={{ ml: 1 }}
              >
                Updating...
              </Typography>
            )}
          </Typography>
          <Box sx={{ width: 200 }}>
            <FormSearchField
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search..."
              size="small"
            />
          </Box>
        </Box>

        <Box sx={{ flex: 1, minHeight: 0 }}>
          <DataTable
            columns={columns}
            data={filteredItems}
            isLoading={isLoading && !data}
            rowCount={total}
            onRowClick={handleRowClick}
            emptyMessage="No feedback submitted yet"
          />
        </Box>

        <Box sx={{ flexShrink: 0 }}>
          <DataTablePagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
            onPageSizeChange={(s) => {
              setPageSize(s);
              setPage(0);
            }}
          />
        </Box>
      </Box>

      {/* Side panel */}
      <Slide
        direction="left"
        in={detailIndex !== null}
        mountOnEnter
        unmountOnExit
      >
        <Box
          sx={{
            width: 400,
            flexShrink: 0,
            borderLeft: "1px solid",
            borderColor: "divider",
            display: "flex",
            flexDirection: "column",
            height: "100%",
            backgroundColor: "background.paper",
          }}
        >
          {/* Header */}
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              px: 1.5,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <Tooltip title="Previous (k)">
                <span>
                  <IconButton
                    size="small"
                    disabled={detailIndex === 0}
                    onClick={() =>
                      setDetailIndex((i) => Math.max(0, (i ?? 0) - 1))
                    }
                  >
                    <Iconify icon="mingcute:arrow-up-line" width={16} />
                  </IconButton>
                </span>
              </Tooltip>
              <Tooltip title="Next (j)">
                <span>
                  <IconButton
                    size="small"
                    disabled={detailIndex >= filteredItems.length - 1}
                    onClick={() =>
                      setDetailIndex((i) =>
                        Math.min(filteredItems.length - 1, (i ?? 0) + 1),
                      )
                    }
                  >
                    <Iconify icon="mingcute:arrow-down-line" width={16} />
                  </IconButton>
                </span>
              </Tooltip>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ ml: 0.5 }}
              >
                {detailIndex !== null
                  ? `${detailIndex + 1} / ${filteredItems.length}`
                  : ""}
              </Typography>
              <Box sx={{ display: "flex", gap: 0.25, ml: 0.5 }}>
                <Box
                  sx={{
                    px: 0.5,
                    py: 0.125,
                    borderRadius: "3px",
                    fontSize: "9px",
                    fontFamily: "monospace",
                    border: "1px solid",
                    borderColor: "divider",
                    color: "text.disabled",
                    lineHeight: 1.4,
                  }}
                >
                  k
                </Box>
                <Box
                  sx={{
                    px: 0.5,
                    py: 0.125,
                    borderRadius: "3px",
                    fontSize: "9px",
                    fontFamily: "monospace",
                    border: "1px solid",
                    borderColor: "divider",
                    color: "text.disabled",
                    lineHeight: 1.4,
                  }}
                >
                  j
                </Box>
              </Box>
            </Box>
            <IconButton size="small" onClick={() => setDetailIndex(null)}>
              <Iconify icon="mingcute:close-line" width={16} />
            </IconButton>
          </Box>

          {/* Content */}
          {detailRow && (
            <Box
              sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 1.5, py: 1 }}
            >
              {/* Feedback value */}
              <Box
                sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2 }}
              >
                <Iconify
                  icon={
                    detailRow.value === "passed"
                      ? "mingcute:thumb-up-2-fill"
                      : "mingcute:thumb-down-2-fill"
                  }
                  width={20}
                  sx={{
                    color:
                      detailRow.value === "passed"
                        ? "success.main"
                        : "error.main",
                  }}
                />
                <Chip
                  label={
                    detailRow.value === "passed"
                      ? "Correct"
                      : detailRow.value === "failed"
                        ? "Incorrect"
                        : detailRow.value
                  }
                  size="small"
                  color={detailRow.value === "passed" ? "success" : "error"}
                  sx={{ fontSize: "12px", height: 24, fontWeight: 600 }}
                />
                {detailRow.action_type && (
                  <Chip
                    label={
                      detailRow.action_type === "retune"
                        ? "Re-tune"
                        : "Re-calculate"
                    }
                    size="small"
                    variant="outlined"
                    sx={{ fontSize: "10px", height: 18 }}
                  />
                )}
              </Box>

              {/* Improvement note */}
              {detailRow.explanation && (
                <Box sx={{ mb: 2 }}>
                  <Typography
                    variant="caption"
                    fontWeight={600}
                    sx={{ mb: 0.5, display: "block" }}
                  >
                    Improvement Note
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{
                      fontSize: "12px",
                      color: "text.secondary",
                      lineHeight: 1.6,
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {detailRow.explanation}
                  </Typography>
                </Box>
              )}

              {/* Meta */}
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.5,
                  mb: 2,
                }}
              >
                {detailRow.user_name && (
                  <Box sx={{ display: "flex", gap: 1 }}>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ width: 60 }}
                    >
                      By
                    </Typography>
                    <Typography variant="caption">
                      {detailRow.user_name}
                    </Typography>
                  </Box>
                )}
                {detailRow.source && (
                  <Box sx={{ display: "flex", gap: 1 }}>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ width: 60 }}
                    >
                      Source
                    </Typography>
                    <Typography variant="caption">
                      {detailRow.source === "eval_playground"
                        ? "Playground"
                        : detailRow.source}
                    </Typography>
                  </Box>
                )}
                {detailRow.created_at && (
                  <Box sx={{ display: "flex", gap: 1 }}>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ width: 60 }}
                    >
                      Date
                    </Typography>
                    <Typography variant="caption">
                      {new Date(detailRow.created_at).toLocaleString()}
                    </Typography>
                  </Box>
                )}
                {detailRow.source_id && (
                  <Box sx={{ display: "flex", gap: 1 }}>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ width: 60 }}
                    >
                      Log ID
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{ fontFamily: "monospace", fontSize: "10px" }}
                    >
                      {detailRow.source_id}
                    </Typography>
                  </Box>
                )}
              </Box>

              {/* Edit button */}
              <Box
                component="button"
                onClick={() => setFeedbackEditOpen(true)}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  px: 2,
                  py: 0.75,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  backgroundColor: "transparent",
                  color: "text.primary",
                  cursor: "pointer",
                  fontSize: "12px",
                  fontWeight: 500,
                  width: "100%",
                  "&:hover": {
                    borderColor: "primary.main",
                    backgroundColor: (t) =>
                      t.palette.mode === "dark"
                        ? "rgba(124,77,255,0.06)"
                        : "rgba(124,77,255,0.04)",
                  },
                }}
              >
                <Iconify
                  icon="mingcute:edit-line"
                  width={14}
                  sx={{ color: "primary.main" }}
                />
                Edit Feedback
              </Box>

              {/* Raw JSON */}
              <Box sx={{ mt: 2 }}>
                <Typography
                  variant="caption"
                  fontWeight={600}
                  sx={{ mb: 0.5, display: "block" }}
                >
                  Raw Data
                </Typography>
                <Box
                  sx={{
                    height: 200,
                    borderRadius: "6px",
                    overflow: "hidden",
                    border: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Editor
                    key={detailRow.id}
                    height="100%"
                    language="json"
                    value={JSON.stringify(detailRow, null, 2)}
                    theme={isDark ? "vs-dark" : "vs"}
                    options={{
                      readOnly: true,
                      minimap: { enabled: false },
                      fontSize: 11,
                      fontFamily: "'Fira Code', Menlo, monospace",
                      lineNumbers: "off",
                      scrollBeyondLastLine: false,
                      automaticLayout: true,
                      wordWrap: "on",
                      folding: true,
                      padding: { top: 4 },
                      renderLineHighlight: "none",
                      domReadOnly: true,
                    }}
                  />
                </Box>
              </Box>
            </Box>
          )}

          {/* Edit feedback drawer */}
          {detailRow && (
            <AddEvalsFeedbackDrawer
              open={feedbackEditOpen}
              onClose={(submitted) => {
                setFeedbackEditOpen(false);
                if (submitted) handleFeedbackSubmitted();
              }}
              selectedAddFeedback={{ id: detailRow.source_id || detailRow.id }}
              output={{ reason: detailRow.explanation || "" }}
              evalsId={templateId}
              existingFeedback={detailRow}
            />
          )}
        </Box>
      </Slide>
    </Box>
  );
};

EvalFeedbackTab.propTypes = {
  templateId: PropTypes.string.isRequired,
};

export default EvalFeedbackTab;
