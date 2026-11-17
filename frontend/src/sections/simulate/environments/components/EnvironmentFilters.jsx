import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  InputAdornment,
  MenuItem,
  Stack,
  TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useDebounce } from "src/hooks/use-debounce";
import { useConnectedAgents } from "src/api/agents/connectedAgents";
import {
  EMPTY_ENV_FILTERS,
  hasActiveFilters,
} from "src/api/simulate-environments/environmentListParams";
import { NOT_PROVIDED } from "../myEnvironments.constants";
import SoonChip from "./SoonChip";

// The list endpoint's own status values; "completed" reads as Ready.
const STATUS_OPTIONS = [
  { value: "building", label: "Building" },
  { value: "running", label: "Running" },
  { value: "completed", label: "Ready" },
  { value: "failed", label: "Failed" },
];

const AGENT_TYPE_OPTIONS = [
  { value: "voice", label: "Voice" },
  { value: "chat", label: "Chat" },
];

const SEARCH_DEBOUNCE_MS = 300;
const MENU_MAX_HEIGHT = 320;
// The Agent menu lists the first page of connected agents.
const AGENT_OPTIONS_LIMIT = 100;

const compactInputSx = {
  "& .MuiInputBase-root": { typography: "s2", height: 34 },
  "& .MuiOutlinedInput-input": { py: 0 },
};
const selectSx = {
  ...compactInputSx,
  minWidth: 170,
  "& .MuiSelect-select": { typography: "s2", py: 0 },
};
const countSx = {
  color: "text.subtitle",
  fontVariantNumeric: "tabular-nums",
  ml: "auto",
  pl: 1.5,
};

const labelOf = (options, all) => (value) =>
  options.find((o) => o.value === value)?.label || all;

// One single-value select, "All …" sending nothing.
function FilterSelect({ label, all, value, options, onChange, count }) {
  return (
    <TextField
      select
      size="small"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      SelectProps={{
        displayEmpty: true,
        renderValue: labelOf(options, all),
        SelectDisplayProps: { "aria-label": label },
        // A long list (dozens of agents) scrolls inside the menu.
        MenuProps: { PaperProps: { sx: { maxHeight: MENU_MAX_HEIGHT } } },
      }}
      sx={selectSx}
    >
      <MenuItem value="" sx={{ typography: "s2" }}>
        <Box component="span">{all}</Box>
        {count?.("")}
      </MenuItem>
      {options.map((o) => (
        <MenuItem key={o.value} value={o.value} sx={{ typography: "s2" }}>
          <Box component="span">{o.label}</Box>
          {count?.(o.value)}
        </MenuItem>
      ))}
    </TextField>
  );
}

FilterSelect.propTypes = {
  label: PropTypes.string.isRequired,
  all: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  options: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.string }),
  ).isRequired,
  onChange: PropTypes.func.isRequired,
  count: PropTypes.func,
};

/**
 * The My Environments filter bar: search plus one-value Status, Agent type and
 * Agent menus. Every filter is server-side and always sent; `onChange` gets
 * the whole next filters object.
 */
export default function EnvironmentFilters({ value, onChange, statusCounts }) {
  const { data: agents } = useConnectedAgents({
    page: 0,
    pageSize: AGENT_OPTIONS_LIMIT,
  });
  const agentOptions = (agents?.items || []).map((a) => ({
    value: a.id,
    label: a.name,
  }));
  // An agent from a shared link that isn't in the menu (still loading, past
  // the first page, or deleted) must not read as "All agents".
  if (value.agentId && !agentOptions.some((o) => o.value === value.agentId)) {
    agentOptions.push({ value: value.agentId, label: "Selected agent" });
  }

  // The box shows what's typed at once; the list hears it once typing stops.
  const [search, setSearch] = useState(value.search);
  const debounced = useDebounce(search, SEARCH_DEBOUNCE_MS);
  const latest = useRef({ value, onChange });
  latest.current = { value, onChange };
  useEffect(() => {
    const { value: current, onChange: emit } = latest.current;
    if (debounced !== current.search) emit({ ...current, search: debounced });
  }, [debounced]);
  // A filter cleared from outside (Clear, or the URL) empties the box too.
  useEffect(() => setSearch(value.search), [value.search]);

  const set = (key) => (next) => onChange({ ...value, [key]: next });

  const statusCount = (status) => {
    if (statusCounts === NOT_PROVIDED) {
      return (
        <Box component="span" sx={{ ml: "auto", pl: 1.5 }}>
          <SoonChip label="Count" what="Status counts" plural />
        </Box>
      );
    }
    if (!statusCounts) return null;
    const n = status
      ? statusCounts[status] ?? 0
      : Object.values(statusCounts).reduce((sum, c) => sum + (c || 0), 0);
    return (
      <Box component="span" sx={countSx}>
        {n}
      </Box>
    );
  };

  const filtering = hasActiveFilters(value);

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      flexWrap="wrap"
      rowGap={1}
      sx={{ flexShrink: 0 }}
    >
      <TextField
        size="small"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search by name, agent or status"
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Iconify
                icon="solar:magnifer-linear"
                width={15}
                sx={{ color: "text.subtitle" }}
              />
            </InputAdornment>
          ),
        }}
        sx={{ width: 280, ...compactInputSx }}
      />
      <FilterSelect
        label="Status"
        all="All statuses"
        value={value.status}
        options={STATUS_OPTIONS}
        onChange={set("status")}
        count={statusCount}
      />
      <FilterSelect
        label="Agent type"
        all="All agent types"
        value={value.agentType}
        options={AGENT_TYPE_OPTIONS}
        onChange={set("agentType")}
      />
      <FilterSelect
        label="Agent"
        all="All agents"
        value={value.agentId}
        options={agentOptions}
        onChange={set("agentId")}
      />
      {filtering && (
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            // Drop half-typed text too, or its pending debounce re-applies it.
            setSearch("");
            onChange(EMPTY_ENV_FILTERS);
          }}
        >
          Clear filters
        </Button>
      )}
    </Stack>
  );
}

EnvironmentFilters.propTypes = {
  value: PropTypes.shape({
    search: PropTypes.string,
    status: PropTypes.string,
    agentType: PropTypes.string,
    agentId: PropTypes.string,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  statusCounts: PropTypes.oneOfType([PropTypes.object, PropTypes.symbol]),
};
