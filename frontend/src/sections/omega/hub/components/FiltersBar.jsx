/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import ButtonBase from "@mui/material/ButtonBase";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { KINDS, TIERS } from "../constants";
import { sortsFor, useInstallPath } from "../lib/installs";

import { hubPageQuery } from "./HubFrame";
import HubIcon from "./HubIcon";

// The kinds are a row of buttons while the Hub page is 1280 px or wider (a
// 1728 window with the sidebar), and a select below that, by the page's own
// width (HubFrame is the container). The buttons with their counts take
// about 590 px, so below 1280 px they would leave the sort on a line of its
// own; at 1440 with the sidebar (about 1150 px) the row holds the select.
const NARROW = hubPageQuery("(max-width: 1279.98px)");

const NO_KEY = "Runs without a key";

const focusRing = (offset) => ({
  "&.Mui-focusVisible": {
    outline: "2px solid",
    outlineColor: (theme) => theme.palette.primary.main,
    outlineOffset: offset,
  },
});

const FOCUS_RING = focusRing(1);

const SELECT_SX = {
  flexShrink: 0,
  "& .MuiSelect-select": { typography: "s1" },
};

const segmentSx = (active) => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 0.75,
  px: 1.25,
  py: 0.5,
  borderRadius: 0.75,
  color: active ? "text.primary" : "text.secondary",
  bgcolor: active ? "action.selected" : "transparent",
  typography: "s1",
  fontWeight: 500,
  whiteSpace: "nowrap",
  flexShrink: 0,
  "&:hover": { bgcolor: active ? "action.selected" : "action.hover" },
  // Inside the button: the kinds' group scrolls when a browser cannot hide
  // it by width, and a ring outside would be cut at the group's edge.
  ...focusRing(-2),
});

const toggleSx = (active) => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 0.5,
  px: 1.25,
  py: 0.75,
  borderRadius: 1,
  border: "1px solid",
  borderColor: active ? "primary.main" : "divider",
  color: active ? "text.primary" : "text.secondary",
  bgcolor: active ? "action.selected" : "background.paper",
  typography: "s1",
  fontWeight: 500,
  whiteSpace: "nowrap",
  flexShrink: 0,
  ...FOCUS_RING,
});

const chipSx = {
  display: "inline-flex",
  alignItems: "center",
  gap: 0.5,
  pl: 1,
  pr: 0.75,
  py: 0.25,
  borderRadius: 4,
  border: "1px solid",
  borderColor: "divider",
  bgcolor: "action.selected",
  color: "text.primary",
  typography: "s2",
  fontWeight: 500,
  whiteSpace: "nowrap",
  maxWidth: "100%",
  "&:hover": { borderColor: "border.hover" },
  ...FOCUS_RING,
};

function KindButton({ active, label, count, onClick, testId }) {
  return (
    <ButtonBase
      onClick={onClick}
      aria-pressed={active}
      data-testid={testId}
      sx={segmentSx(active)}
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

KindButton.propTypes = {
  active: PropTypes.bool,
  label: PropTypes.node,
  count: PropTypes.number,
  onClick: PropTypes.func,
  testId: PropTypes.string,
};

const kindName = (value) =>
  KINDS.find((kind) => kind.value === value)?.label || value;

const kindLabel = (value) => (value ? kindName(value) : "Any kind");

// Below 1280 px the same choice as a select, with the same counts.
function KindSelect({ value, counts, onChange }) {
  return (
    <TextField
      select
      size="small"
      value={value || ""}
      onChange={(event) => onChange(event.target.value || null)}
      SelectProps={{ displayEmpty: true, renderValue: kindLabel }}
      inputProps={{
        "aria-label": "Kind",
        "data-testid": "hub-filter-kind-select",
      }}
      sx={{ ...SELECT_SX, display: "none", [NARROW]: { display: "flex" } }}
    >
      <MenuItem value="">Any kind</MenuItem>
      {KINDS.map((kind) => (
        <MenuItem key={kind.value} value={kind.value}>
          {kind.label}
          {counts[kind.value] > 0 && (
            <Box component="span" sx={{ ml: 0.75, color: "text.tertiary" }}>
              {counts[kind.value]}
            </Box>
          )}
        </MenuItem>
      ))}
    </TextField>
  );
}

KindSelect.propTypes = {
  value: PropTypes.string,
  counts: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};

const tierName = (value) => TIERS[value]?.label || value;

const tierLabel = (value) => (value ? tierName(value) : "Any publisher");

// The trust tier, always in the row with every level, so it can be chosen
// before an entry of that level is listed and it never comes and goes as
// the list is searched or filtered.
function TrustSelect({ value, onChange }) {
  return (
    <TextField
      select
      size="small"
      value={TIERS[value] ? value : ""}
      onChange={(event) => onChange(event.target.value || null)}
      SelectProps={{ displayEmpty: true, renderValue: tierLabel }}
      inputProps={{ "aria-label": "Trust", "data-testid": "hub-filter-tier" }}
      sx={SELECT_SX}
    >
      <MenuItem value="">Any publisher</MenuItem>
      {Object.entries(TIERS).map(([key, meta]) => (
        <MenuItem key={key} value={key} data-testid={`hub-filter-tier-${key}`}>
          {meta.label}
        </MenuItem>
      ))}
    </TextField>
  );
}

TrustSelect.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};

// The Hub home's one control row: the search field (`search`), every kind
// (with the server's count when it is above zero; a kind with nothing listed
// shows its name alone), trust, "Runs without a key", and the sort at the
// row's end (`end`). Every filter is URL state, so a filtered view can be
// shared (SPEC §7.3). On a narrow page the search field takes the first line
// (the page sets that on `search`) and the rest share the next.
export default function FiltersBar({ params, facets, onChange, search, end }) {
  const kindCounts = facets?.kinds || {};
  const set = (key, value) =>
    onChange({ [key]: params[key] === value ? null : value });
  return (
    <Box
      data-testid="hub-filters"
      sx={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 1,
        minWidth: 0,
      }}
    >
      {search}
      <Box
        role="group"
        aria-label="Kind"
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.25,
          p: 0.25,
          minWidth: 0,
          maxWidth: "100%",
          overflowX: "auto",
          scrollbarWidth: "none",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.paper",
          [NARROW]: { display: "none" },
        }}
      >
        <KindButton
          active={!params.kind}
          label="All"
          onClick={() => onChange({ kind: null })}
          testId="hub-filter-kind-all"
        />
        {KINDS.map((kind) => (
          <KindButton
            key={kind.value}
            active={params.kind === kind.value}
            label={kind.label}
            count={kindCounts[kind.value] || undefined}
            onClick={() => set("kind", kind.value)}
            testId={`hub-filter-kind-${kind.value}`}
          />
        ))}
      </Box>
      <KindSelect
        value={params.kind}
        counts={kindCounts}
        onChange={(kind) => onChange({ kind })}
      />
      <TrustSelect
        value={params.tier}
        onChange={(tier) => onChange({ tier })}
      />
      <ButtonBase
        onClick={() => set("noKey", "1")}
        aria-pressed={params.noKey === "1"}
        data-testid="hub-filter-nokey"
        sx={toggleSx(params.noKey === "1")}
      >
        {params.noKey === "1" && <HubIcon name="check" size={13} />}
        {NO_KEY}
      </ButtonBase>
      {end && <Box sx={{ ml: "auto", display: "flex" }}>{end}</Box>}
    </Box>
  );
}

FiltersBar.propTypes = {
  params: PropTypes.object.isRequired,
  facets: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  search: PropTypes.node,
  end: PropTypes.node,
};

const sortLabel = (sorts, selected, relevance) => {
  if (relevance) return "Sorted by relevance";
  const sort = sorts.find((option) => option.value === selected);
  return sort ? `Sort: ${sort.label}` : "Sort";
};

// The sort, as a select at the end of the control row; every order the API
// has is in it ("Most installed" only once entries can be installed, see
// lib/installs.js). `value` is the order the server applied, so a page that
// sent none shows the right one. A text search is sorted by relevance, which
// the select then says, and it waits until the search is cleared.
export function SortSelect({ value, onChange, relevance = false }) {
  const sorts = sortsFor(useInstallPath());
  const known = sorts.some((sort) => sort.value === value);
  return (
    <TextField
      select
      size="small"
      value={!relevance && known ? value : ""}
      disabled={relevance}
      onChange={(event) => onChange(event.target.value)}
      SelectProps={{
        displayEmpty: true,
        renderValue: (selected) => sortLabel(sorts, selected, relevance),
      }}
      inputProps={{ "aria-label": "Sort", "data-testid": "hub-sort" }}
      sx={{ ...SELECT_SX, minWidth: 150 }}
    >
      {sorts.map((sort) => (
        <MenuItem
          key={sort.value}
          value={sort.value}
          data-testid={`hub-sort-${sort.value}`}
        >
          {sort.label}
        </MenuItem>
      ))}
    </TextField>
  );
}

SortSelect.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  relevance: PropTypes.bool,
};

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

function activeFilters(params) {
  const chips = [];
  const add = (key, label) => chips.push({ key, label });
  if (params.q) add("q", `Search: ${params.q}`);
  if (params.kind) add("kind", `Kind: ${kindName(params.kind)}`);
  if (params.tier) add("tier", `Trust: ${tierName(params.tier)}`);
  if (params.noKey) add("noKey", NO_KEY);
  if (params.tag) add("tag", `Tag: ${params.tag}`);
  if (params.vendor) add("vendor", `Vendor: ${params.vendor}`);
  return chips;
}

// While the next answer loads the line says so, and the count waits for it:
// the last answer's number is never read out as the new one's.
function countLine(count, query, filtering, pending) {
  if (pending) return query ? "Searching…" : "Loading entries…";
  if (typeof count !== "number") return "";
  if (!filtering) return plural(count, "entry", "entries");
  const found = plural(count, "result", "results");
  return query ? `${found} for “${query}”` : found;
}

// Under the control row: how many entries there are, or how many match
// ("2 results for “sql”", announced once the answer is in; `pending` while
// it loads), then each active filter as a chip that removes it, and Clear
// all. `onDone` puts focus back in the search field after a chip that had
// focus is gone.
export function ActiveFilters({
  params,
  count,
  pending = false,
  onChange,
  onClearAll,
  onDone,
}) {
  const chips = activeFilters(params);
  const remove = (key) => {
    onChange({ [key]: null });
    onDone?.();
  };
  return (
    <Box
      data-testid="hub-results-summary"
      sx={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 1,
        minHeight: 28,
      }}
    >
      <Typography
        role="status"
        aria-live="polite"
        typography="s1"
        color="text.secondary"
        data-testid="hub-result-count"
      >
        {countLine(count, params.q, chips.length > 0, pending)}
      </Typography>
      {chips.map((chip) => (
        <ButtonBase
          key={chip.key}
          onClick={() => remove(chip.key)}
          aria-label={`Remove filter ${chip.label}`}
          sx={chipSx}
        >
          <Box
            component="span"
            sx={{ overflow: "hidden", textOverflow: "ellipsis" }}
          >
            {chip.label}
          </Box>
          <HubIcon name="close" size={12} />
        </ButtonBase>
      ))}
      {chips.length > 0 && (
        <Button
          size="small"
          variant="text"
          color="inherit"
          onClick={() => {
            onClearAll();
            onDone?.();
          }}
          sx={{ minWidth: 0, px: 0.75, color: "text.secondary" }}
        >
          Clear all
        </Button>
      )}
    </Box>
  );
}

ActiveFilters.propTypes = {
  params: PropTypes.object.isRequired,
  count: PropTypes.number,
  pending: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
  onClearAll: PropTypes.func.isRequired,
  onDone: PropTypes.func,
};
