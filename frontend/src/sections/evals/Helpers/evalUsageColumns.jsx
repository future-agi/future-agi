/* eslint-disable react/prop-types */
/* eslint-disable react-refresh/only-export-components */
import { Box, Tooltip, Typography } from "@mui/material";
import { useMemo } from "react";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import CustomTooltip from "src/components/tooltip";
import { PARTIAL_INPUT_WARNING_TYPE } from "src/sections/common/EvalsTasks/PartialInputWarningDetails";

export const ScoreCell = ({ value }) => {
  if (value == null)
    return (
      <Typography variant="s2" color="text.disabled">
        —
      </Typography>
    );
  if (typeof value === "number")
    return (
      <Typography
        variant="s2"
        sx={{
          fontVariantNumeric: "tabular-nums",
          color: "text.primary",
        }}
      >
        {value.toFixed(2)}
      </Typography>
    );
  return (
    <Typography variant="s2" sx={{ color: "text.secondary" }}>
      {String(value)}
    </Typography>
  );
};

export const normalizeRow = (raw) => {
  const out = { id: raw.row_id };
  Object.entries(raw).forEach(([k, v]) => {
    if (k === "row_id") return;
    out[k] =
      v && typeof v === "object" && !Array.isArray(v) && "cell_value" in v
        ? v.cell_value
        : v;
  });
  return out;
};

export const DEFAULT_COLUMN_CONFIG = [
  {
    value: "score",
    label: "Score",
    enabled: true,
    is_visible: true,
    order_index: 0,
  },
  {
    value: "result",
    label: "Result",
    enabled: true,
    is_visible: true,
    order_index: 1,
  },
  {
    value: "input",
    label: "Input",
    enabled: true,
    is_visible: true,
    order_index: 2,
  },
  {
    value: "reason",
    label: "Reason",
    enabled: true,
    is_visible: true,
    order_index: 3,
  },
  {
    value: "source",
    label: "Source",
    enabled: true,
    is_visible: true,
    order_index: 4,
  },
  {
    value: "version",
    label: "Version",
    enabled: true,
    is_visible: true,
    order_index: 5,
  },
  {
    value: "feedback",
    label: "Feedback",
    enabled: true,
    is_visible: true,
    order_index: 6,
  },
  {
    value: "created_at",
    label: "Ran at",
    enabled: true,
    is_visible: true,
    order_index: 7,
  },
];

export const COLUMN_CONFIG_URL_PARAM = "cols";
export const columnConfigStorageKey = (templateId) =>
  `eval-usage-columns:${templateId}`;

export const encodeColumnConfig = (cols) =>
  [...cols]
    .sort((a, b) => (a.order_index ?? 0) - (b.order_index ?? 0))
    .map((c) => (c.enabled && c.is_visible ? c.value : `~${c.value}`))
    .join(",");

export const decodeColumnConfig = (str, base) => {
  if (!str) return null;
  const byValue = new Map(base.map((c) => [c.value, c]));
  const result = [];
  str
    .split(",")
    .filter(Boolean)
    .forEach((token) => {
      const hidden = token.startsWith("~");
      const value = hidden ? token.slice(1) : token;
      const orig = byValue.get(value);
      if (!orig) {
        // Preserve unknown tokens (e.g. input_var_* discovered on other pages)
        result.push({
          value,
          label: value.replace(/^input_var_/, "").replaceAll("_", " "),
          enabled: !hidden,
          is_visible: !hidden,
          order_index: result.length,
        });
        return;
      }
      byValue.delete(value);
      result.push({
        ...orig,
        enabled: !hidden,
        is_visible: !hidden,
        order_index: result.length,
      });
    });
  byValue.forEach((c) => result.push({ ...c, order_index: result.length }));
  return result.length ? result : null;
};

export const StatPill = ({ label, value, color }) => (
  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
    <Typography variant="s3" color="text.secondary">
      {label}:
    </Typography>
    <Typography variant="s2" fontWeight="fontWeightSemiBold" color={color}>
      {value}
    </Typography>
  </Box>
);

export const DATE_OPTION_TO_PERIOD = {
  "30 mins": "30m",
  "1 hr": "1h",
  "6 hrs": "6h",
  Today: "1d",
  Yesterday: "1d",
  "7D": "7d",
  "30D": "30d",
  "3M": "90d",
  "6M": "180d",
  "12M": "365d",
  Custom: "30d",
};

export const periodForRange = (start, end) => {
  const hours =
    (new Date(end).getTime() - new Date(start).getTime()) / 3_600_000;
  if (!(hours > 0)) return "30d";
  if (hours <= 1) return "30m";
  if (hours <= 6) return "6h";
  if (hours <= 24) return "1d";
  if (hours <= 24 * 7) return "7d";
  if (hours <= 24 * 30) return "30d";
  if (hours <= 24 * 90) return "90d";
  if (hours <= 24 * 180) return "180d";
  return "365d";
};

const indicatorColumn = {
  id: "indicator",
  accessorKey: "score",
  header: "",
  size: 4,
  enableSorting: false,
  cell: ({ getValue }) => {
    const v = getValue();
    const color =
      v == null
        ? "transparent"
        : typeof v === "number"
          ? v >= 0.7
            ? "status.success.solid"
            : v >= 0.3
              ? "status.warning.solid"
              : "status.danger.solid"
          : v === 1
            ? "status.success.solid"
            : v === 0
              ? "status.danger.solid"
              : "text.disabled";
    return (
      <Box
        sx={{ width: 3, height: 28, borderRadius: 1, backgroundColor: color }}
      />
    );
  },
};

const renderResult = ({ getValue, row: tableRow }) => {
  const v = getValue();
  const warnings = tableRow.original?.warnings || [];
  const partial = warnings.find?.(
    (w) => w?.type === PARTIAL_INPUT_WARNING_TYPE,
  );
  const partialBadge = partial ? (
    <CustomTooltip
      show
      arrow
      title={
        partial.message ||
        `Eval ran with some inputs empty: ${(partial.empty_keys || []).join(", ")}`
      }
    >
      <Box
        sx={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 18,
          height: 18,
          borderRadius: "50%",
          backgroundColor: "status.warning.bg",
          color: "status.warning.fg",
          cursor: "help",
        }}
        data-testid="usage-partial-input-warning"
      >
        <Iconify
          icon="solar:danger-triangle-linear"
          width="14px"
          height="14px"
        />
      </Box>
    </CustomTooltip>
  ) : null;

  if (!v) {
    if (tableRow.original?.status === "error") {
      return (
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <StatusBadge status="error" label="Error" />
          {partialBadge}
        </Box>
      );
    }
    return partialBadge;
  }
  const isPassed = v === "Passed" || v === "Pass";
  const isFailed = v === "Failed" || v === "Fail";
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
      {isPassed || isFailed ? (
        <StatusBadge status={isPassed ? "passed" : "failed"} label={v} />
      ) : (
        <Typography variant="s2" sx={{ color: "text.secondary" }}>
          {v}
        </Typography>
      )}
      {partialBadge}
    </Box>
  );
};

const renderInput = ({ getValue }) => {
  const v = getValue();
  return (
    <Typography
      variant="s2"
      noWrap
      sx={{
        color: v ? "text.secondary" : "text.disabled",
        fontStyle: v ? "normal" : "italic",
      }}
    >
      {v || "No input"}
    </Typography>
  );
};

const renderReason = ({ getValue }) => (
  <Typography
    variant="s2"
    noWrap
    sx={{ color: "text.secondary", fontStyle: "italic" }}
  >
    {getValue() || "—"}
  </Typography>
);

const renderSource = ({ getValue }) => {
  const v = getValue();
  if (!v) return null;
  const label =
    v === "eval_playground" || v === "composite_eval"
      ? "Playground"
      : v === "dataset_evaluation" || v === "composite_eval_dataset"
        ? "Dataset"
        : v === "tracer_composite"
          ? "Tracer"
          : v;
  return (
    <Typography variant="s3" noWrap sx={{ color: "text.tertiary" }}>
      {label}
    </Typography>
  );
};

const renderVersion = ({ getValue }) => {
  const v = getValue();
  if (v == null || v === "")
    return (
      <Typography variant="s2" color="text.disabled">
        —
      </Typography>
    );
  const label =
    typeof v === "number" || !String(v).startsWith("v") ? `v${v}` : String(v);
  return (
    <Typography
      variant="s3"
      noWrap
      sx={{ color: "text.tertiary", fontVariantNumeric: "tabular-nums" }}
    >
      {label}
    </Typography>
  );
};

const renderFeedback = ({ row: tableRow }) => {
  const original = tableRow.original;
  if (original.composite) {
    const childCount =
      original.detail?.total_children ?? original.detail?.children?.length ?? 0;
    return (
      <Tooltip title={`${childCount} child evaluators`}>
        <Box
          component="span"
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            color: "text.tertiary",
          }}
        >
          <Iconify icon="solar:widget-4-linear" width={12} />
          <Typography
            variant="s3"
            sx={{ fontVariantNumeric: "tabular-nums", color: "inherit" }}
          >
            {childCount}
          </Typography>
        </Box>
      </Tooltip>
    );
  }

  const fb = original.feedback;
  if (!fb) return null;
  return (
    <Tooltip title={`Feedback: ${fb.value}`}>
      <Iconify
        icon={fb.value === "passed" ? "solar:like-bold" : "solar:dislike-bold"}
        width={14}
        sx={{
          color:
            fb.value === "passed" ? "status.success.fg" : "status.danger.fg",
        }}
      />
    </Tooltip>
  );
};

const renderCreatedAt = ({ getValue }) => {
  const v = getValue();
  if (!v) return null;
  const d = new Date(v);
  return (
    <Typography variant="s3" noWrap sx={{ color: "text.disabled" }}>
      {d.toLocaleDateString(undefined, { month: "short", day: "numeric" })},{" "}
      {d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
    </Typography>
  );
};

const renderGeneric = ({ getValue }) => {
  const v = getValue();
  const text =
    v == null || v === ""
      ? "—"
      : typeof v === "object"
        ? JSON.stringify(v)
        : String(v);
  return (
    <Typography
      variant="s2"
      noWrap
      sx={{ color: v == null || v === "" ? "text.disabled" : "text.secondary" }}
    >
      {text}
    </Typography>
  );
};

const COLUMN_DEFS = {
  score: { size: 80, cell: ({ getValue }) => <ScoreCell value={getValue()} /> },
  result: { size: 130, cell: renderResult },
  input: { meta: { flex: 2 }, minSize: 200, cell: renderInput },
  reason: { meta: { flex: 1.5 }, minSize: 150, cell: renderReason },
  source: { size: 100, cell: renderSource },
  version: { size: 80, cell: renderVersion },
  feedback: {
    header: "",
    size: 50,
    enableSorting: false,
    cell: renderFeedback,
  },
  created_at: { size: 140, cell: renderCreatedAt },
};

// ── Build columns dynamically from backend column_config ──
export const useColumns = (columnConfig) =>
  useMemo(() => {
    const source =
      columnConfig && columnConfig.length
        ? columnConfig
        : DEFAULT_COLUMN_CONFIG;
    const dynamic = source
      .filter((c) => c.enabled && c.is_visible)
      .slice()
      .sort((a, b) => (a.order_index ?? 0) - (b.order_index ?? 0))
      .map((c) => {
        const def = COLUMN_DEFS[c.value] || {
          meta: { flex: 1 },
          minSize: 120,
          cell: renderGeneric,
        };
        return {
          id: c.value,
          accessorKey: c.value,
          header: def.header !== undefined ? def.header : c.label,
          ...(def.size != null ? { size: def.size } : {}),
          ...(def.minSize != null ? { minSize: def.minSize } : {}),
          ...(def.meta ? { meta: def.meta } : {}),
          ...(def.enableSorting === false ? { enableSorting: false } : {}),
          cell: def.cell,
        };
      });
    return [indicatorColumn, ...dynamic];
  }, [columnConfig]);
