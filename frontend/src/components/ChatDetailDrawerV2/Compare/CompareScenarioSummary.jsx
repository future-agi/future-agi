import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import _ from "lodash";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PersonaComponent from "src/components/persona/personaComponent";

// Scenario summary for the chat compare view, with a "Show details" toggle
// between dense pills and a full definition list.

const COMPACT_VALUE_MAX_CHARS = 30;

const formatValue = (raw) => {
  if (raw == null) return "—";
  if (typeof raw === "object") {
    try {
      return JSON.stringify(raw);
    } catch {
      return "[unserializable]";
    }
  }
  return String(raw);
};

const personaName = (raw) => {
  if (!raw) return null;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return parsed?.name || parsed?.persona_name || null;
    } catch {
      return raw.length <= 40 ? raw : null;
    }
  }
  if (typeof raw === "object") {
    return raw?.name || null;
  }
  return null;
};

// ─────────────────────────────────────────────────────────────────────────────
// Compact: pill row
// ─────────────────────────────────────────────────────────────────────────────

const Pill = ({ label, value }) => (
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
      minWidth: 0,
      maxWidth: 280,
      fontSize: 11,
      color: "text.primary",
      lineHeight: "16px",
    }}
  >
    <Box
      component="span"
      sx={{
        flexShrink: 0,
        whiteSpace: "nowrap",
        color: "text.secondary",
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        fontSize: 10,
      }}
    >
      {label}
    </Box>
    <Box
      component="span"
      sx={{
        minWidth: 0,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {value}
    </Box>
  </Box>
);
Pill.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node.isRequired,
};

// ─────────────────────────────────────────────────────────────────────────────
// Expanded: definition list row
// ─────────────────────────────────────────────────────────────────────────────

const DetailRow = ({ label, value, isPersona, raw }) => (
  <Box
    sx={{
      display: "grid",
      gridTemplateColumns: "minmax(120px, 160px) 1fr",
      columnGap: 1.5,
      rowGap: 0.25,
      alignItems: "start",
      py: 0.75,
      borderBottom: "1px solid",
      borderColor: "divider",
      "&:last-of-type": { borderBottom: "none" },
    }}
  >
    <Typography
      sx={{
        fontSize: 10,
        fontWeight: 600,
        color: "text.secondary",
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        pt: 0.25,
      }}
    >
      {label}
    </Typography>
    {isPersona ? (
      <Box sx={{ minWidth: 0 }}>
        <PersonaComponent formattedValue={raw} />
      </Box>
    ) : (
      <Typography
        component="div"
        sx={{
          fontSize: 12,
          color: "text.primary",
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {value}
      </Typography>
    )}
  </Box>
);
DetailRow.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
  isPersona: PropTypes.bool,
  raw: PropTypes.any,
};

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

const CompareScenarioSummary = ({ data }) => {
  const [expanded, setExpanded] = useState(false);

  const items = useMemo(() => {
    const out = [];
    Object.entries(data?.scenario_columns || {}).forEach(([key, value]) => {
      const rawLabel = value?.column_name ?? key;
      const label = _.startCase(_.toLower(String(rawLabel)));
      const raw = value?.value;
      const isPersona = label === "Persona";

      const full = formatValue(raw);
      const compactValue = isPersona
        ? personaName(raw) || "Persona"
        : full.length > COMPACT_VALUE_MAX_CHARS
          ? `${full.slice(0, COMPACT_VALUE_MAX_CHARS).trimEnd()}…`
          : full;

      out.push({
        key,
        label,
        compactValue,
        fullValue: full,
        isPersona,
        raw,
      });
    });
    return out;
  }, [data]);

  const scenarioName = data?.scenario;

  if (!scenarioName && items.length === 0) return null;

  const hasDetails = items.length > 0;

  return (
    <Stack gap={0.75}>
      {/* Header row — SCENARIO label + scenario name + Show/Hide
          details toggle. The toggle button is plainly visible (no
          icon-only / hover-to-discover trick). */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ minWidth: 0 }}
      >
        <Typography
          sx={{
            fontSize: 10,
            fontWeight: 600,
            color: "text.secondary",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            flexShrink: 0,
          }}
        >
          Scenario
        </Typography>
        {scenarioName && (
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: "text.primary",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              minWidth: 0,
              flex: 1,
            }}
          >
            {scenarioName}
          </Typography>
        )}
        {hasDetails && (
          <ButtonBase
            onClick={() => setExpanded((v) => !v)}
            sx={{
              flexShrink: 0,
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              px: 1,
              py: 0.25,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "2px",
              bgcolor: "background.paper",
              fontSize: 11,
              fontWeight: 500,
              color: "text.primary",
              lineHeight: "16px",
              cursor: "pointer",
              "&:hover": {
                bgcolor: "action.hover",
                borderColor: "text.disabled",
              },
            }}
          >
            <span>{expanded ? "Hide full details" : "Show full details"}</span>
            <Iconify
              icon={expanded ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={12}
            />
          </ButtonBase>
        )}
      </Stack>

      {hasDetails && !expanded && (
        // Compact mode — wrapping pill row.
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{
            flexWrap: "wrap",
            minWidth: 0,
            width: "100%",
          }}
        >
          {items.map((p) => (
            <Pill key={p.key} label={p.label} value={p.compactValue} />
          ))}
        </Stack>
      )}

      {hasDetails && expanded && (
        // Expanded mode — definition list inside a single bordered
        // block so it visually nests under the header.
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            bgcolor: "background.paper",
            px: 1.25,
          }}
        >
          {items.map((p) => (
            <DetailRow
              key={p.key}
              label={p.label}
              value={p.fullValue}
              isPersona={p.isPersona}
              raw={p.raw}
            />
          ))}
        </Box>
      )}
    </Stack>
  );
};

CompareScenarioSummary.propTypes = {
  data: PropTypes.object,
};

export default CompareScenarioSummary;
