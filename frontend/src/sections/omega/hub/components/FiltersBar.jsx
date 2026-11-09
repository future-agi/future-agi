/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";

import { KINDS, TIERS } from "../constants";
import { sortsFor, useInstallPath } from "../lib/installs";
import { TAB_ROW_PROPS, TAB_ROW_SCROLL_SX } from "./tabRow";

function FilterChip({ active, label, count, onClick, testId }) {
  return (
    <ButtonBase
      onClick={onClick}
      aria-pressed={active}
      data-testid={testId}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.75,
        px: 1.25,
        py: 0.5,
        borderRadius: 4,
        border: "1px solid",
        borderColor: active ? "primary.main" : "divider",
        color: active ? "primary.main" : "text.secondary",
        bgcolor: active ? "action.selected" : "background.paper",
        typography: "s2",
        fontWeight: 500,
        whiteSpace: "nowrap",
      }}
    >
      {label}
      {/* DS tokens.json: fg.secondary on bg.selected over bg.surface (AA). */}
      {typeof count === "number" && (
        <Box
          component="span"
          sx={{
            color: active ? "text.secondary" : "text.tertiary",
            fontWeight: 400,
          }}
        >
          {count}
        </Box>
      )}
    </ButtonBase>
  );
}

FilterChip.propTypes = {
  active: PropTypes.bool,
  label: PropTypes.node,
  count: PropTypes.number,
  onClick: PropTypes.func,
  testId: PropTypes.string,
};

// Kind (with counts from the server's facets), tier, and "No key needed".
// Every filter is URL state, so a filtered view can be shared (SPEC §7.3).
export default function FiltersBar({ params, facets, onChange }) {
  const kindCounts = facets?.kinds || {};
  const set = (key, value) =>
    onChange({ [key]: params[key] === value ? null : value });
  return (
    <Box
      data-testid="hub-filters"
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Box
        sx={{
          display: "flex",
          gap: 0.75,
          overflowX: "auto",
          pb: 0.25,
          "&::-webkit-scrollbar": { display: "none" },
        }}
      >
        <FilterChip
          active={!params.kind}
          label="All"
          onClick={() => onChange({ kind: null })}
          testId="hub-filter-kind-all"
        />
        {KINDS.map((kind) => (
          <FilterChip
            key={kind.value}
            active={params.kind === kind.value}
            label={kind.label}
            count={kindCounts[kind.value] ?? undefined}
            onClick={() => set("kind", kind.value)}
            testId={`hub-filter-kind-${kind.value}`}
          />
        ))}
      </Box>
      <Box
        sx={{
          display: "flex",
          gap: 0.75,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        {Object.entries(TIERS).map(([value, meta]) => (
          <FilterChip
            key={value}
            active={params.tier === value}
            label={meta.label}
            onClick={() => set("tier", value)}
            testId={`hub-filter-tier-${value}`}
          />
        ))}
        <FilterChip
          active={params.noKey === "1"}
          label="No key needed"
          onClick={() => set("noKey", "1")}
          testId="hub-filter-nokey"
        />
        {params.tag && (
          <FilterChip
            active
            label={`Tag: ${params.tag}`}
            onClick={() => onChange({ tag: null })}
          />
        )}
        {params.vendor && (
          <FilterChip
            active
            label={`Vendor: ${params.vendor}`}
            onClick={() => onChange({ vendor: null })}
          />
        )}
      </Box>
    </Box>
  );
}

FiltersBar.propTypes = {
  params: PropTypes.object.isRequired,
  facets: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};

// "Most installed" only once entries can be installed (lib/installs.js).
export function SortTabs({ value, onChange, disabled }) {
  const sorts = sortsFor(useInstallPath());
  // `value` is the order the server applied; until it answers (or for a text
  // search, "relevance") no tab is selected rather than a guessed one.
  const selected = sorts.some((sort) => sort.value === value) ? value : false;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        minWidth: 0,
      }}
    >
      <Tabs
        value={selected}
        onChange={(_, next) => onChange(next)}
        {...TAB_ROW_PROPS}
        data-testid="hub-sort-tabs"
        sx={{
          minHeight: 40,
          minWidth: 0,
          "& .MuiTab-root": { minHeight: 40, typography: "s1" },
          ...TAB_ROW_SCROLL_SX,
        }}
      >
        {sorts.map((sort) => (
          <Tab
            key={sort.value}
            value={sort.value}
            label={sort.label}
            disabled={disabled}
            data-testid={`hub-sort-${sort.value}`}
          />
        ))}
      </Tabs>
      {disabled && (
        <Typography
          typography="s3"
          color="text.tertiary"
          sx={{ ml: "auto", whiteSpace: "nowrap" }}
        >
          Sorted by relevance
        </Typography>
      )}
    </Box>
  );
}

SortTabs.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
