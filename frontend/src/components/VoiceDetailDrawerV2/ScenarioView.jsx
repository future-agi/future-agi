import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import _ from "lodash";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PersonaComponent from "src/components/persona/personaComponent";
import { isEmptyPersona, parsePersona } from "./persona.utils";

/**
 * Clean scenario view — replaces the old horizontally-scrolling
 * TestDetailDrawerScenarioTable which squeezed every field into a fixed-
 * height card requiring a "View full details" toggle.
 *
 * Layout:
 *   ┌─ Scenario heading ────────────┐
 *   │ description text              │
 *   ├─ Persona (if present) ────────┤
 *   │ <PersonaComponent />          │
 *   ├─ All other fields ────────────┤
 *   │ LABEL    value                │
 *   │ LABEL    JSON pretty-printed   │
 *   │ LABEL    long text wraps      │
 *   └───────────────────────────────┘
 *
 * Each row is a compact key/value pair — 120px label column + flexible
 * value column. Long values wrap, JSON values render in a mono block,
 * nothing gets truncated or requires horizontal scroll.
 */

const isObjectLike = (v) =>
  v != null && typeof v === "object" && !Array.isArray(v);

const displayValue = (value) => {
  if (value == null || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (Array.isArray(value)) {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  if (isObjectLike(value)) {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  return String(value);
};

const isJsonValue = (value) => {
  if (value == null) return false;
  if (typeof value !== "string")
    return isObjectLike(value) || Array.isArray(value);
  // Heuristic: string starts with { or [ — try to parse
  const trimmed = value.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return false;
  try {
    const parsed = JSON.parse(trimmed);
    return isObjectLike(parsed) || Array.isArray(parsed);
  } catch {
    return false;
  }
};

const prettyJson = (value) => {
  try {
    if (typeof value === "string") {
      return JSON.stringify(JSON.parse(value), null, 2);
    }
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const SectionLabel = ({ children }) => (
  <Typography
    sx={{
      fontSize: 10,
      fontWeight: 600,
      color: "text.secondary",
      textTransform: "uppercase",
      letterSpacing: "0.04em",
      lineHeight: 1.4,
    }}
  >
    {children}
  </Typography>
);

SectionLabel.propTypes = { children: PropTypes.node };

const KeyValueRow = ({ label, value }) => {
  const isJson = isJsonValue(value);
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      sx={{
        gap: { xs: 0.25, sm: 1.5 },
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        "&:last-child": { borderBottom: "none" },
      }}
    >
      <Box
        sx={{
          flex: "0 0 140px",
          minWidth: 0,
          pt: 0.25,
        }}
      >
        <Typography
          sx={{
            fontSize: 11,
            fontWeight: 600,
            color: "text.secondary",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            wordBreak: "break-word",
          }}
        >
          {label}
        </Typography>
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {isJson ? (
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1,
              fontSize: 11,
              lineHeight: 1.5,
              fontFamily: "'JetBrains Mono', 'SF Mono', Consolas, monospace",
              color: "text.primary",
              bgcolor: "background.neutral",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "3px",
              overflow: "auto",
              maxHeight: 280,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {prettyJson(value)}
          </Box>
        ) : (
          <Typography
            sx={{
              fontSize: 12.5,
              lineHeight: 1.5,
              color: value == null ? "text.disabled" : "text.primary",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {displayValue(value)}
          </Typography>
        )}
      </Box>
    </Stack>
  );
};

KeyValueRow.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.any,
};

// A persona with nothing to show: missing, blank, an empty object, or an object
// whose fields are all empty (`persona_details` sends nulls and [] when unset).
const ScenarioView = ({ data }) => {
  const [query, setQuery] = useState("");

  const columns = useMemo(
    () => Object.entries(data?.scenario_columns || {}),
    [data],
  );

  // Sort so Persona shows up separately, then all other fields alphabetically
  // with scenario_flow pushed to the end (since it's usually large JSON).
  const { personaEntry, otherEntries } = useMemo(() => {
    let persona = null;
    const others = [];
    columns.forEach(([key, col]) => {
      const colName = col?.column_name || "";
      if (colName.toLowerCase() === "persona") {
        persona = { key, col };
      } else {
        others.push({ key, col });
      }
    });
    others.sort((a, b) => {
      // scenario_flow last
      if (a.col?.column_name === "scenario_flow") return 1;
      if (b.col?.column_name === "scenario_flow") return -1;
      return (a.col?.column_name || "").localeCompare(b.col?.column_name || "");
    });
    return { personaEntry: persona, otherEntries: others };
  }, [columns]);

  // Environment (harness) calls carry an empty dataset persona column and send
  // the persona as `persona_details`, so fall back to that when the column has
  // nothing to show.
  const columnPersona = parsePersona(personaEntry?.col?.value);
  const personaValue = !isEmptyPersona(columnPersona)
    ? columnPersona
    : data?.persona_details;
  const hasPersona = !isEmptyPersona(personaValue);

  const scenarioText = data?.scenario;

  // Filter sections based on the search query. Matches against the scenario
  // text, persona value, and every other column's name + stringified value.
  const q = query.trim().toLowerCase();
  const matches = (text) =>
    !q || (text != null && String(text).toLowerCase().includes(q));

  const matchedScenario = !q || matches(scenarioText);

  const matchedPersona =
    hasPersona &&
    (!q ||
      matches(personaEntry?.col?.column_name || "persona") ||
      matches(
        typeof personaValue === "object"
          ? JSON.stringify(personaValue)
          : String(personaValue),
      ));

  const filteredOthers = useMemo(() => {
    if (!q) return otherEntries;
    return otherEntries.filter(({ col }) => {
      const label = col?.column_name || "";
      const valueString =
        typeof col?.value === "object" && col?.value != null
          ? JSON.stringify(col.value)
          : String(col?.value ?? "");
      return matches(label) || matches(valueString);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [otherEntries, q]);

  const nothingMatched =
    q && !matchedScenario && !matchedPersona && filteredOthers.length === 0;

  if (!scenarioText && columns.length === 0 && !hasPersona) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 160,
        }}
      >
        <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
          No scenario data available
        </Typography>
      </Box>
    );
  }

  return (
    <Stack gap={1.5} sx={{ pb: 2 }}>
      {/* Search bar — filters scenario text, persona, and all other fields
          by matching against column name + stringified value. */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          px: 1,
          py: 0.25,
          bgcolor: "background.paper",
          flexShrink: 0,
        }}
      >
        <Iconify icon="mdi:magnify" width={13} color="text.disabled" />
        <Box
          component="input"
          placeholder="Search scenario"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
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
        {query && (
          <Iconify
            icon="mdi:close"
            width={12}
            onClick={() => setQuery("")}
            sx={{
              cursor: "pointer",
              color: "text.disabled",
              "&:hover": { color: "text.primary" },
            }}
          />
        )}
      </Box>

      {nothingMatched && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            minHeight: 120,
          }}
        >
          <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
            No matching scenario fields
          </Typography>
        </Box>
      )}

      {/* Scenario heading */}
      {scenarioText && matchedScenario && (
        <Stack
          gap={0.5}
          sx={{
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            bgcolor: "background.default",
          }}
        >
          <Stack direction="row" alignItems="center" gap={0.5}>
            <Iconify
              icon="mdi:format-list-numbered"
              width={13}
              sx={{ color: "text.disabled" }}
            />
            <SectionLabel>Scenario</SectionLabel>
          </Stack>
          <Typography
            sx={{
              fontSize: 13,
              lineHeight: 1.55,
              color: "text.primary",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {scenarioText}
          </Typography>
        </Stack>
      )}

      {/* Persona section — uses the existing PersonaComponent for its
          structured rendering (icon + label). */}
      {matchedPersona && (
        <Stack
          gap={0.75}
          sx={{
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
          }}
        >
          <Stack direction="row" alignItems="center" gap={0.5}>
            <Iconify
              icon="mdi:account-outline"
              width={13}
              sx={{ color: "text.disabled" }}
            />
            <SectionLabel>Persona</SectionLabel>
          </Stack>
          <PersonaComponent formattedValue={personaValue} />
        </Stack>
      )}

      {/* All other fields — compact key/value rows inside one bordered card */}
      {filteredOthers.length > 0 && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            px: 1.5,
            py: 0.5,
          }}
        >
          {filteredOthers.map(({ key, col }) => (
            <KeyValueRow
              key={key}
              label={_.startCase(_.toLower(col?.column_name || key))}
              value={col?.value}
            />
          ))}
        </Box>
      )}
    </Stack>
  );
};

ScenarioView.propTypes = {
  data: PropTypes.object,
};

export default ScenarioView;
