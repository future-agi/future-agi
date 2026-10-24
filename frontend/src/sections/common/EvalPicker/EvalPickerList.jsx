import {
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  InputAdornment,
  Checkbox,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Typography,
  Tooltip,
  Avatar,
  Skeleton,
  Collapse,
  ButtonBase,
  useTheme,
} from "@mui/material";
// date-fns available if needed for timestamps
import React, { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { DataTablePagination } from "src/components/data-table";
import EvalTypeBadge from "src/sections/evals/components/EvalTypeBadge";
import TypeBadge from "src/sections/evals/components/TypeBadge";
import VersionBadge from "src/sections/evals/components/VersionBadge";
import {
  getEvalCode,
  getEvalCodeLanguage,
  normalizeEvalPickerEval,
} from "./evalPickerValue";
import EvalFilterPanel from "src/sections/evals/components/EvalFilterPanel";
import { EVAL_TAGS } from "src/sections/evals/constant";
import PropTypes from "prop-types";
import { evalDetailQuery } from "src/sections/evals/hooks/useEvalDetail";
import { useEvalPickerData } from "./hooks/useEvalPickerData";
import { useEvalPickerContext } from "./context/EvalPickerContext";
import { useCompositeDetail } from "src/sections/evals/hooks/useCompositeEval";

function getInitials(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

const OUTPUT_TYPE_LABELS = {
  pass_fail: "Pass/fail",
  percentage: "Percentage",
  deterministic: "Choices",
};

// ── Inline Detail Panel ──
//
// Panel content is type-aware. Three distinct shapes:
//   - LLM / Agent  → Output, Model, Variables, (Choices if deterministic), Instructions
//   - Code         → Output, Pass Threshold, Variables, Code snippet
//   - Composite    → Output, Aggregation, Children list with weights
// Previously every eval showed the same fields which produced meaningless
// "Model: —" rows for code evals and hid the actual child eval info on
// composite templates.

const LabeledValue = ({ label, value }) => (
  <Box>
    <Typography
      variant="caption"
      color="text.disabled"
      sx={{ fontSize: "12px" }}
    >
      {label}
    </Typography>
    <Typography variant="body2" sx={{ fontSize: "12px" }}>
      {value || "—"}
    </Typography>
  </Box>
);
LabeledValue.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
};

const SectionLabel = ({ children }) => (
  <Typography
    variant="caption"
    color="text.disabled"
    sx={{ fontSize: "12px", mb: 0.5, display: "block" }}
  >
    {children}
  </Typography>
);
SectionLabel.propTypes = { children: PropTypes.node };

const CodeBlock = ({ content }) => (
  <Box
    sx={{
      p: 1,
      borderRadius: "4px",
      maxHeight: 160,
      overflow: "auto",
      bgcolor: "background.neutral",
      border: "1px solid",
      borderColor: "divider",
    }}
  >
    <Typography
      variant="body2"
      sx={{
        fontSize: "12px",
        fontFamily: "monospace",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {content}
    </Typography>
  </Box>
);
CodeBlock.propTypes = {
  content: PropTypes.string.isRequired,
  theme: PropTypes.object.isRequired,
};

const VariablesList = ({ variables }) => {
  if (!variables?.length) return null;
  return (
    <Box>
      <SectionLabel>Variables</SectionLabel>
      <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
        {variables.map((v) => (
          <Chip
            key={v}
            label={`{{${v}}}`}
            size="small"
            sx={{
              fontFamily: "monospace",
              bgcolor: "action.hover",
              color: "primary.main",
              "& .MuiChip-label": { px: 0.5 },
            }}
          />
        ))}
      </Box>
    </Box>
  );
};
VariablesList.propTypes = { variables: PropTypes.array };

const EvalDetailPanel = ({ evalData }) => {
  const theme = useTheme();
  const templateId =
    evalData?.templateId || evalData?.template_id || evalData?.id;

  const { data: configData, isLoading } = useQuery({
    ...evalDetailQuery(templateId),
    enabled: !!templateId,
    staleTime: 30000,
  });

  // `template_type` tells us single vs composite; `eval_type` splits single
  // into llm / agent / code. Fall back to the row data (evalData) when the
  // detail fetch hasn't resolved yet so the panel still renders something.
  const templateType =
    configData?.template_type ||
    configData?.templateType ||
    evalData?.template_type ||
    "single";
  const isComposite = templateType === "composite";

  const { data: compositeDetail, isLoading: isLoadingComposite } =
    useCompositeDetail(isComposite ? templateId : null, isComposite);

  const normalizedConfigData = normalizeEvalPickerEval(configData);
  const normalizedEvalData = normalizeEvalPickerEval(evalData);

  const evalType =
    normalizedConfigData?.evalType || normalizedEvalData?.evalType || "llm";
  const outputType =
    normalizedConfigData?.outputType || normalizedEvalData?.outputType || "";
  const description = configData?.description || evalData?.description || "";
  const model = configData?.model || evalData?.model || "";
  const passThreshold =
    configData?.pass_threshold ??
    configData?.passThreshold ??
    evalData?.passThreshold;
  const config = configData?.config || {};
  const requiredKeys =
    normalizedConfigData?.requiredKeys ||
    normalizedEvalData?.requiredKeys ||
    [];
  const choicesMap =
    config.choicesMap ||
    config.choices_map ||
    configData?.choice_scores ||
    configData?.choicesMap ||
    {};
  const instructions = configData?.instructions || evalData?.instructions || "";
  const code =
    getEvalCode(normalizedConfigData) || getEvalCode(normalizedEvalData);
  const codeLanguage =
    getEvalCodeLanguage(normalizedConfigData) ||
    getEvalCodeLanguage(normalizedEvalData);

  // For composite evals, variables are the union of child required_keys —
  // the same derivation used by EvalPickerConfigFull so the panel matches
  // what the mapping UI will later show.
  const compositeVariables = useMemo(() => {
    if (!isComposite || !compositeDetail?.children) return [];
    const union = new Set();
    compositeDetail.children.forEach((c) => {
      (c?.required_keys || []).forEach((k) => union.add(k));
    });
    return [...union];
  }, [isComposite, compositeDetail]);

  if (isLoading || (isComposite && isLoadingComposite)) {
    return (
      <Box sx={{ p: 2, display: "flex", flexDirection: "column", gap: 1 }}>
        <Skeleton width="60%" height={20} />
        <Skeleton width="40%" height={16} />
        <Skeleton width="100%" height={60} />
      </Box>
    );
  }

  const panelSx = {
    p: 2,
    bgcolor: "background.neutral",
    display: "flex",
    flexDirection: "column",
    gap: 1.5,
  };

  // ── Composite ──
  if (isComposite) {
    const children = compositeDetail?.children || [];
    const aggregation =
      compositeDetail?.aggregation_function ||
      compositeDetail?.aggregationFunction ||
      "weighted_average";
    return (
      <Box sx={panelSx}>
        <Box sx={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
          <LabeledValue
            label="Output Type"
            value={OUTPUT_TYPE_LABELS[outputType] || outputType}
          />
          <LabeledValue label="Aggregation" value={aggregation} />
          <LabeledValue label="Children" value={children.length} />
        </Box>

        {description && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ fontSize: "12px" }}
          >
            {description}
          </Typography>
        )}

        <Box>
          <SectionLabel>Child evaluations</SectionLabel>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              p: 1,
              maxHeight: 200,
              overflow: "auto",
            }}
          >
            {children.length === 0 ? (
              <Typography variant="caption" color="text.disabled">
                No child evals configured
              </Typography>
            ) : (
              children.map((c) => (
                <Box
                  key={c.child_id || c.id || c.name}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 1,
                  }}
                >
                  <Typography
                    variant="body2"
                    sx={{ fontSize: "12px", fontWeight: 500 }}
                    noWrap
                  >
                    {c.name || c.child_name || c.child_id}
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{
                      fontSize: "12px",
                      color: "text.tertiary",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {`weight ${c.weight ?? 1}`}
                  </Typography>
                </Box>
              ))
            )}
          </Box>
        </Box>

        <VariablesList variables={compositeVariables} />
      </Box>
    );
  }

  // ── Code ──
  if (evalType === "code") {
    return (
      <Box sx={panelSx}>
        <Box sx={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
          <LabeledValue
            label="Output Type"
            value={OUTPUT_TYPE_LABELS[outputType] || outputType}
          />
          {passThreshold != null && (
            <LabeledValue
              label="Pass Threshold"
              value={`${Math.round(passThreshold * 100)}%`}
            />
          )}
          <LabeledValue label="Language" value={codeLanguage} />
        </Box>

        {description && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ fontSize: "12px" }}
          >
            {description}
          </Typography>
        )}

        <VariablesList variables={requiredKeys} />

        {code && (
          <Box>
            <SectionLabel>Code</SectionLabel>
            <CodeBlock content={code} theme={theme} />
          </Box>
        )}
      </Box>
    );
  }

  // ── LLM / Agent (single) ──
  return (
    <Box sx={panelSx}>
      <Box sx={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
        <LabeledValue
          label="Output Type"
          value={OUTPUT_TYPE_LABELS[outputType] || outputType}
        />
        {model && <LabeledValue label="Model" value={model} />}
        <LabeledValue
          label="Kind"
          value={evalType === "agent" ? "Agent" : "LLM-as-a-judge"}
        />
      </Box>

      {description && (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontSize: "12px" }}
        >
          {description}
        </Typography>
      )}

      <VariablesList variables={requiredKeys} />

      {Object.keys(choicesMap).length > 0 && (
        <Box>
          <SectionLabel>Choices</SectionLabel>
          <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
            {Object.entries(choicesMap).map(([label, score]) => (
              <Chip
                key={label}
                label={`${label} = ${score}`}
                size="small"
                variant="outlined"
                sx={{
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            ))}
          </Box>
        </Box>
      )}

      {instructions && (
        <Box>
          <SectionLabel>Instructions</SectionLabel>
          <CodeBlock content={instructions} theme={theme} />
        </Box>
      )}
    </Box>
  );
};

EvalDetailPanel.propTypes = { evalData: PropTypes.object.isRequired };

// ── Loading skeleton rows ──

const SkeletonRows = (
  { count = 8 }, // eslint-disable-line react/prop-types
) => (
  <>
    {Array.from({ length: count }).map((_, i) => (
      <TableRow key={i}>
        <TableCell sx={{ width: 40, p: 0.5 }}>
          <Skeleton variant="circular" width={24} height={24} />
        </TableCell>
        <TableCell sx={{ p: 0.5 }}>
          <Skeleton width={60} height={26} sx={{ borderRadius: 1 }} />
        </TableCell>
        <TableCell>
          <Skeleton width="70%" />
        </TableCell>
        <TableCell>
          <Skeleton width={50} />
        </TableCell>
        <TableCell>
          <Skeleton width={40} />
        </TableCell>
        <TableCell>
          <Skeleton width={70} />
        </TableCell>
        <TableCell>
          <Skeleton width={80} />
        </TableCell>
      </TableRow>
    ))}
  </>
);

// ── Added evals ──

/*
  The evals already on the caller, in their own box under the category chips.
  Collapsed by default with a count, so the list below is only what can still
  be added and nothing reads as greyed-out or broken. A search that matches an
  added eval opens the box and narrows it too — looking for an eval you already
  have should say so, not come back empty.
*/
const AddedEvalsSection = ({ addedEvals, searchQuery }) => {
  const [open, setOpen] = useState(false);
  const q = (searchQuery || "").trim().toLowerCase();
  const shown = q
    ? addedEvals.filter((e) =>
        `${e.name} ${e.meta || ""}`.toLowerCase().includes(q),
      )
    : addedEvals;
  const matching = q && shown.length > 0;
  const expanded = open || matching;

  if (!addedEvals.length) return null;
  return (
    <Box
      sx={{
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <ButtonBase
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
        sx={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.5,
          py: 1,
          justifyContent: "flex-start",
          textAlign: "left",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon="solar:alt-arrow-right-linear"
          width={14}
          sx={{
            color: "text.secondary",
            transition: "transform 150ms",
            transform: expanded ? "rotate(90deg)" : "none",
          }}
        />
        <Iconify
          icon="solar:check-circle-linear"
          width={16}
          sx={{ color: "text.secondary" }}
        />
        <Typography sx={{ fontSize: "13px", fontWeight: 600 }}>
          Added evaluations
        </Typography>
        <Typography
          sx={{
            fontSize: "12px",
            fontWeight: 400,
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {q ? `${shown.length}/${addedEvals.length}` : addedEvals.length}
        </Typography>
      </ButtonBase>

      <Collapse in={expanded} timeout={150} unmountOnExit>
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            maxHeight: 188,
            overflowY: "auto",
          }}
        >
          {shown.length === 0 ? (
            <Typography
              sx={{
                px: 1.5,
                py: 1.25,
                fontSize: "12px",
                color: "text.secondary",
              }}
            >
              None of the added evaluations match your search.
            </Typography>
          ) : (
            shown.map((e, i) => (
              <Box
                key={e.id}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  px: 1.5,
                  py: 0.75,
                  borderTop: i ? "1px solid" : "none",
                  borderColor: "border.subtle",
                }}
              >
                <Iconify
                  icon="solar:check-read-linear"
                  width={14}
                  sx={{ color: "success.main", flexShrink: 0 }}
                />
                <Typography
                  noWrap
                  sx={{ fontSize: "13px", fontWeight: 500, minWidth: 0 }}
                >
                  {e.name}
                </Typography>
                <Box sx={{ flex: 1 }} />
                {e.meta && (
                  <Typography
                    noWrap
                    sx={{
                      fontSize: "12px",
                      color: "text.tertiary",
                      flexShrink: 0,
                    }}
                  >
                    {e.meta}
                  </Typography>
                )}
              </Box>
            ))
          )}
        </Box>
      </Collapse>
    </Box>
  );
};

AddedEvalsSection.propTypes = {
  addedEvals: PropTypes.array.isRequired,
  searchQuery: PropTypes.string,
};

// ── Main Component ──

const EvalPickerList = ({ onSelectEval }) => {
  const {
    existingEvals,
    source,
    sourceId,
    lockedFilters,
    multiSelect,
    selectedIds,
    onToggleSelect,
    addedEvals,
  } = useEvalPickerContext();
  const useScopedEvals = source === "dataset" || source === "experiment";
  const {
    items,
    total,
    isLoading,
    isSearching,
    searchQuery,
    setSearchQuery,
    page,
    setPage,
    pageSize,
    setPageSize,
    sorting,
    setSorting,
    filters,
    setFilters,
  } = useEvalPickerData({
    sourceId: useScopedEvals ? sourceId : null,
    enabled: true,
    lockedFilters,
  });

  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const [expandedEvalId, setExpandedEvalId] = useState(null);

  const isAlreadyAdded = useCallback(
    (evalId) =>
      existingEvals?.some(
        (e) =>
          e.id === evalId ||
          e.eval_template_id === evalId ||
          e.template_id === evalId ||
          e["templateId"] === evalId,
      ),
    [existingEvals],
  );

  /* With the "Added evals" section on, added evals live there and are left
     out of the list — matched by id, or by name for library evals the caller
     only knows by name. */
  const addedNames = useMemo(
    () =>
      new Set(
        (addedEvals || []).map((e) => String(e.name || "").toLowerCase()),
      ),
    [addedEvals],
  );
  const listItems = useMemo(
    () =>
      Array.isArray(addedEvals)
        ? items.filter(
            (e) =>
              !isAlreadyAdded(e.id) &&
              !addedNames.has(String(e.name || "").toLowerCase()),
          )
        : items,
    [items, addedEvals, addedNames, isAlreadyAdded],
  );

  const activeFilterCount = useMemo(() => {
    if (!filters) return 0;
    let count = 0;
    if (filters.eval_type?.length) count += filters.eval_type.length;
    if (filters.output_type?.length) count += filters.output_type.length;
    if (filters.owner) count += 1;
    if (filters.tags?.length) count += filters.tags.length;
    return count;
  }, [filters]);

  const toggleExpand = useCallback((evalId) => {
    setExpandedEvalId((prev) => (prev === evalId ? null : evalId));
  }, []);

  const sortField = sorting[0]?.id || "last_updated";
  const sortDesc = sorting[0]?.desc ?? true;
  const handleSort = useCallback(
    (field) => {
      setSorting([{ id: field, desc: sortField === field ? !sortDesc : true }]);
    },
    [setSorting, sortField, sortDesc],
  );

  const headerCellSx = {
    fontSize: "12px",
    fontWeight: 600,
    color: "text.secondary",
    py: 1,
    px: 1,
    whiteSpace: "nowrap",
    borderBottom: "1px solid",
    borderColor: "divider",
  };
  const bodyCellSx = {
    fontSize: "13px",
    py: 0.75,
    px: 1,
    borderBottom: "1px solid",
    borderColor: "divider",
  };

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        minHeight: 0,
        overflow: "hidden",
      }}
    >
      {/* Top Controls */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 1,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <FormSearchField
            size="small"
            placeholder="Search evaluations..."
            sx={{
              minWidth: "250px",
              "& .MuiOutlinedInput-root": { height: "32px" },
            }}
            searchQuery={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setPage(0);
              setExpandedEvalId(null);
            }}
            InputProps={
              isSearching && searchQuery.trim()
                ? {
                    endAdornment: (
                      <InputAdornment position="end">
                        <CircularProgress
                          size={14}
                          thickness={5}
                          aria-label="Searching evaluations"
                        />
                      </InputAdornment>
                    ),
                  }
                : undefined
            }
          />

          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="solar:filter-linear" width={14} />}
            onClick={(e) => setFilterAnchorEl(e.currentTarget)}
            sx={{
              borderColor: activeFilterCount > 0 ? "primary.main" : "divider",
              color: activeFilterCount > 0 ? "primary.main" : "text.secondary",
            }}
          >
            Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}
          </Button>
        </Box>
      </Box>

      {/* Quick tag filters */}

      <Box
        sx={{
          display: "flex",
          gap: 0.5,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        {EVAL_TAGS.map((tag) => {
          const activeTagValues = filters?.tags || [];
          const tagValues = tag.match || [tag.value];
          const isActive = tagValues.some((v) => activeTagValues.includes(v));
          return (
            <Chip
              key={tag.value}
              icon={<Iconify icon={tag.icon} width={14} />}
              label={tag.label}
              size="small"
              variant={isActive ? "filled" : "outlined"}
              color={isActive ? "primary" : "default"}
              onClick={() => {
                if (isActive) {
                  const toRemove = new Set(tagValues);
                  setFilters((prev) => {
                    const safe = prev || {};
                    const remaining = (safe.tags || []).filter(
                      (v) => !toRemove.has(v),
                    );
                    if (!remaining.length) {
                      const next = { ...safe };
                      delete next.tags;
                      return Object.keys(next).length ? next : null;
                    }
                    return { ...safe, tags: remaining };
                  });
                } else {
                  setFilters((prev) => {
                    const safe = prev || {};
                    return {
                      ...safe,
                      tags: [...(safe.tags || []), ...tagValues],
                    };
                  });
                }
                setPage(0);
                setExpandedEvalId(null);
              }}
              sx={{ cursor: "pointer" }}
            />
          );
        })}
        {filters?.tags?.length ? (
          <Chip
            label="Clear"
            size="small"
            variant="outlined"
            onDelete={() => {
              setFilters((prev) => {
                const safe = prev || {};
                const next = { ...safe };
                delete next.tags;
                return Object.keys(next).length ? next : null;
              });
              setPage(0);
              setExpandedEvalId(null);
            }}
          />
        ) : null}
      </Box>

      {Array.isArray(addedEvals) && (
        <AddedEvalsSection addedEvals={addedEvals} searchQuery={searchQuery} />
      )}

      {/* Scrollable Table */}
      <TableContainer sx={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        <Table size="small" stickyHeader sx={{ tableLayout: "fixed" }}>
          <TableHead>
            <TableRow>
              {multiSelect && <TableCell sx={{ ...headerCellSx, width: 44 }} />}
              <TableCell sx={{ ...headerCellSx, width: 36 }} />
              <TableCell sx={{ ...headerCellSx, width: 72 }} />
              <TableCell sx={{ ...headerCellSx }}>
                <TableSortLabel
                  active={sortField === "name"}
                  direction={sortField === "name" && !sortDesc ? "asc" : "desc"}
                  onClick={() => handleSort("name")}
                >
                  Evaluation Name
                </TableSortLabel>
              </TableCell>
              <TableCell sx={{ ...headerCellSx, width: 80 }}>Type</TableCell>
              <TableCell sx={{ ...headerCellSx, width: 80 }}>
                Eval Type
              </TableCell>
              <TableCell sx={{ ...headerCellSx, width: 100 }}>Output</TableCell>
              <TableCell sx={{ ...headerCellSx, width: 110 }}>
                <TableSortLabel
                  active={sortField === "created_by_name"}
                  direction={
                    sortField === "created_by_name" && !sortDesc
                      ? "asc"
                      : "desc"
                  }
                  onClick={() => handleSort("created_by_name")}
                >
                  Created By
                </TableSortLabel>
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading ? (
              <SkeletonRows />
            ) : listItems.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={multiSelect ? 8 : 7}
                  align="center"
                  sx={{ py: 6, color: "text.disabled" }}
                >
                  No evaluations found
                </TableCell>
              </TableRow>
            ) : (
              listItems.map((evalItem) => {
                const isExpanded = expandedEvalId === evalItem.id;
                const added = isAlreadyAdded(evalItem.id);
                const createdBy = evalItem.created_by_name || "Unknown";
                const isSystem = createdBy === "System";

                return [
                  /* Main row */
                  <TableRow
                    key={evalItem.id}
                    hover
                    onClick={() => toggleExpand(evalItem.id)}
                    sx={{
                      cursor: "pointer",
                      bgcolor: isExpanded ? "action.selected" : "inherit",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    {/* Multi-select tick — the row still expands on click,
                        so the checkbox stops propagation. */}
                    {multiSelect && (
                      <TableCell sx={{ ...bodyCellSx, width: 44, px: 0.5 }}>
                        <Checkbox
                          size="small"
                          disabled={added}
                          checked={added || !!selectedIds?.has(evalItem.id)}
                          onClick={(e) => e.stopPropagation()}
                          onChange={() => onToggleSelect?.(evalItem)}
                          sx={{ p: 0.5 }}
                        />
                      </TableCell>
                    )}

                    {/* Expand chevron */}
                    <TableCell sx={{ ...bodyCellSx, width: 36, px: 0.5 }}>
                      <IconButton size="small" sx={{ p: 0.25 }}>
                        <Iconify
                          icon={
                            isExpanded
                              ? "solar:alt-arrow-down-bold"
                              : "solar:alt-arrow-right-bold"
                          }
                          width={14}
                          sx={{
                            color: isExpanded
                              ? "primary.main"
                              : "text.disabled",
                          }}
                        />
                      </IconButton>
                    </TableCell>

                    {/* Add button */}
                    <TableCell sx={{ ...bodyCellSx, width: 72, px: 0.5 }}>
                      <Button
                        size="small"
                        variant={added ? "outlined" : "contained"}
                        disabled={added}
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectEval(evalItem);
                        }}
                        sx={{
                          minWidth: 50,
                        }}
                      >
                        {added ? "Added" : "Add"}
                      </Button>
                    </TableCell>

                    {/* Name */}
                    <TableCell sx={bodyCellSx}>
                      <Box
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          gap: 0.75,
                        }}
                      >
                        <Typography
                          variant="body2"
                          noWrap
                          sx={{ fontWeight: 500, fontSize: "13px" }}
                        >
                          {evalItem.name}
                        </Typography>
                        {evalItem.current_version &&
                          !evalItem.is_draft &&
                          evalItem.current_version !== "draft" && (
                            <VersionBadge version={evalItem.current_version} />
                          )}
                      </Box>
                    </TableCell>

                    {/* Type */}
                    <TableCell sx={{ ...bodyCellSx, width: 80 }}>
                      <TypeBadge type={evalItem.template_type} />
                    </TableCell>

                    {/* Eval Type */}
                    <TableCell sx={{ ...bodyCellSx, width: 80 }}>
                      <EvalTypeBadge type={evalItem.eval_type} />
                    </TableCell>

                    {/* Output */}
                    <TableCell sx={{ ...bodyCellSx, width: 100 }}>
                      <Typography
                        variant="body2"
                        noWrap
                        sx={{ fontSize: "12px" }}
                      >
                        {OUTPUT_TYPE_LABELS[evalItem.output_type] ||
                          evalItem.output_type}
                      </Typography>
                    </TableCell>

                    {/* Created By */}
                    <TableCell sx={{ ...bodyCellSx, width: 110 }}>
                      <Tooltip title={createdBy} placement="top" arrow>
                        <Box
                          sx={{
                            display: "flex",
                            alignItems: "center",
                            gap: 0.5,
                          }}
                        >
                          <Avatar
                            sx={{
                              width: 20,
                              height: 20,
                              fontSize: "12px",
                              fontWeight: 600,
                            }}
                          >
                            {isSystem ? (
                              <Iconify
                                icon="solar:shield-check-bold"
                                width={12}
                              />
                            ) : (
                              getInitials(createdBy)
                            )}
                          </Avatar>
                          <Typography
                            variant="body2"
                            noWrap
                            sx={{ fontSize: "12px" }}
                          >
                            {createdBy}
                          </Typography>
                        </Box>
                      </Tooltip>
                    </TableCell>
                  </TableRow>,

                  /* Expanded detail row */
                  isExpanded && (
                    <TableRow key={`${evalItem.id}-detail`}>
                      <TableCell
                        colSpan={multiSelect ? 8 : 7}
                        sx={{
                          p: 0,
                          borderBottom: "1px solid",
                          borderColor: "divider",
                        }}
                      >
                        <EvalDetailPanel evalData={evalItem} />
                      </TableCell>
                    </TableRow>
                  ),
                ];
              })
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* Pagination */}
      <DataTablePagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={(p) => {
          setPage(p);
          setExpandedEvalId(null);
        }}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(0);
          setExpandedEvalId(null);
        }}
      />

      {/* Filter panel */}
      <EvalFilterPanel
        anchorEl={filterAnchorEl}
        open={Boolean(filterAnchorEl)}
        onClose={() => setFilterAnchorEl(null)}
        currentFilters={filters}
        lockedFilters={lockedFilters}
        onApply={(newFilters) => {
          setFilters(newFilters);
          setPage(0);
          setExpandedEvalId(null);
        }}
      />
    </Box>
  );
};

EvalPickerList.propTypes = { onSelectEval: PropTypes.func.isRequired };

export default EvalPickerList;
