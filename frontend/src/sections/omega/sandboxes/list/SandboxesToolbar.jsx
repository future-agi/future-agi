import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import FormSearchField from "src/components/FormSearchField/FormSearchField";
import ListToolbar from "src/components/list-toolbar";

import SandboxIcon from "../components/SandboxIcon";
import { stateLabel } from "../lib/format";
import {
  SANDBOX_SORT_CHOICES,
  STATE_FILTERS,
  sortFromValue,
  sortValue,
} from "./listView";

const PRESSED_ICON = <SandboxIcon name="check" size={14} />;

const plural = (count) => `${count} sandbox${count === 1 ? "" : "es"}`;

/** The chosen sort as the select reads it inline: "Sort: Name, A to Z". */
function sortText(value) {
  const choice = SANDBOX_SORT_CHOICES.find((item) => item.value === value);
  return `Sort: ${choice ? choice.label : "Default order"}`;
}

/**
 * "9 sandboxes · 4 running + 1 agent run", or "Showing 3 of 9 sandboxes"
 * while filtered. Running agent runs are named apart, as the page's header
 * names them, since the workspace's limit does not count them.
 */
function countLine({ shown, total, running, runningRuns, filtered }) {
  if (filtered) return `Showing ${shown} of ${plural(total)}`;
  const line = `${plural(total)} · ${running} running`;
  if (runningRuns === 0) return line;
  return `${line} + ${runningRuns} agent run${runningRuns === 1 ? "" : "s"}`;
}

/**
 * Search by name, id or image; one chip per state (pressed = shown); Clear
 * all once anything is narrowed; Sort by while the list shows cards, which
 * have no column headings; and how many rows the list shows.
 */
export default function SandboxesToolbar({
  query,
  onQuery,
  states,
  onStates,
  counts,
  shown,
  total,
  running,
  runningRuns = 0,
  sort = null,
  onSort,
  sortMenu = false,
}) {
  const filtered = Boolean(query.trim()) || states.length > 0;
  // A state with no rows gets no chip, unless it is the one filtering.
  const offered = STATE_FILTERS.filter(
    (state) => counts[state] > 0 || states.includes(state),
  );
  const toggle = (state) =>
    onStates(
      states.includes(state)
        ? states.filter((item) => item !== state)
        : [...states, state],
    );
  return (
    <ListToolbar
      useFlexGap
      flexWrap="wrap"
      sx={{ pt: 0, pb: 1.5, rowGap: 1 }}
      data-testid="sandboxes-toolbar"
    >
      <FormSearchField
        searchQuery={query}
        onChange={(event) => onQuery(event.target.value)}
        placeholder="Search by name, id or image"
        sx={{ width: 280 }}
        inputProps={{
          "aria-label": "Search sandboxes by name, id or image",
          "data-testid": "sandboxes-search",
        }}
      />
      <Box
        role="group"
        aria-label="Filter by state"
        sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}
      >
        {offered.map((state) => {
          const pressed = states.includes(state);
          return (
            <Chip
              key={state}
              size="small"
              clickable
              variant={pressed ? "filled" : "outlined"}
              icon={pressed ? PRESSED_ICON : undefined}
              label={`${stateLabel(state)} ${counts[state] ?? 0}`}
              onClick={() => toggle(state)}
              aria-pressed={pressed}
              data-testid={`sandboxes-filter-${state}`}
            />
          );
        })}
      </Box>
      {filtered && (
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            onQuery("");
            onStates([]);
          }}
          data-testid="sandboxes-clear-filters"
        >
          Clear all
        </Button>
      )}
      {/* A toolbar select: 32px tall like the search beside it, its value
          inline and no label above it. 260px fits the longest choice. */}
      {sortMenu && (
        <TextField
          select
          size="small"
          id="sandboxes-sort"
          value={sortValue(sort)}
          onChange={(event) => onSort(sortFromValue(event.target.value))}
          SelectProps={{
            displayEmpty: true,
            renderValue: sortText,
            SelectDisplayProps: { "aria-labelledby": undefined },
          }}
          inputProps={{
            "aria-label": "Sort by",
            "data-testid": "sandboxes-sort",
          }}
          sx={{ width: 260 }}
        >
          <MenuItem value="">Default order</MenuItem>
          {SANDBOX_SORT_CHOICES.map((choice) => (
            <MenuItem key={choice.value} value={choice.value}>
              {choice.label}
            </MenuItem>
          ))}
        </TextField>
      )}
      <Box sx={{ flex: 1 }} />
      <Typography
        typography="s2"
        color="text.secondary"
        aria-live="polite"
        data-testid="sandboxes-count"
      >
        {countLine({ shown, total, running, runningRuns, filtered })}
      </Typography>
    </ListToolbar>
  );
}

SandboxesToolbar.propTypes = {
  query: PropTypes.string.isRequired,
  onQuery: PropTypes.func.isRequired,
  states: PropTypes.arrayOf(PropTypes.string).isRequired,
  onStates: PropTypes.func.isRequired,
  /** Rows per state, before any filter. */
  counts: PropTypes.object.isRequired,
  shown: PropTypes.number.isRequired,
  total: PropTypes.number.isRequired,
  /** Running sandboxes of the workspace's own (the limit counts these). */
  running: PropTypes.number.isRequired,
  /** Running agent runs, which the limit does not count. */
  runningRuns: PropTypes.number,
  sort: PropTypes.shape({ key: PropTypes.string, direction: PropTypes.string }),
  /** Called with the chosen sort, or null for the API's own order. */
  onSort: PropTypes.func.isRequired,
  /** Offer Sort by: the cards have no column headings to sort with. */
  sortMenu: PropTypes.bool,
};
