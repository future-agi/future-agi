import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";

import { GatewayFilterBar } from "../../components/frame";
import { CATEGORIES, categoryLabel } from "../catalog-model";

const ANY_AUTH = "any";

// The rows above the apps, on the gateway filter bar every gateway table
// uses: search, All or Connected and the auth type, then the five categories
// (plus an open category chosen from an app's detail). Both rows wrap at
// narrow widths.
export default function IntegrationsFilterBar({
  filters,
  onChange,
  connectedCount,
  authOptions,
}) {
  const set = (patch) => onChange({ ...filters, ...patch });
  const openCategory =
    filters.category && !CATEGORIES.some((c) => c.id === filters.category)
      ? filters.category
      : null;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <GatewayFilterBar
        search={filters.search}
        onSearchChange={(search) => set({ search })}
        searchPlaceholder="Search apps"
      >
        <ToggleButtonGroup
          exclusive
          size="small"
          value={filters.view}
          onChange={(_, view) => view && set({ view })}
          aria-label="Show"
          sx={{
            // The theme's 2 px track padding sits outside the row's height,
            // so the group is no taller than the search box and the Auth
            // select, and its buttons stay centred on them (the bar puts the
            // search box on its bottom edge).
            my: "-2px",
            "& .MuiToggleButton-root": {
              height: 30,
              px: 1.5,
              textTransform: "none",
              typography: "s2",
            },
          }}
        >
          <ToggleButton value="all">All</ToggleButton>
          <ToggleButton value="connected">
            Connected ({connectedCount})
          </ToggleButton>
        </ToggleButtonGroup>
        {authOptions.length > 1 && (
          // Named inside the field ("Auth: Any"), not by a floating label,
          // so it is as tall as the search box and the view switch.
          <TextField
            select
            size="small"
            SelectProps={{
              renderValue: (value) =>
                value === ANY_AUTH
                  ? "Auth: Any"
                  : `Auth: ${authOptions.find((o) => o.id === value)?.label ?? ""}`,
              SelectDisplayProps: { "aria-label": "Auth" },
            }}
            value={filters.auth || ANY_AUTH}
            onChange={(event) =>
              set({
                auth:
                  event.target.value === ANY_AUTH ? null : event.target.value,
              })
            }
            sx={{ minWidth: 140, "& .MuiInputBase-root": { height: 30 } }}
          >
            <MenuItem value={ANY_AUTH}>Any</MenuItem>
            {authOptions.map((option) => (
              <MenuItem key={option.id} value={option.id}>
                {option.label}
              </MenuItem>
            ))}
          </TextField>
        )}
      </GatewayFilterBar>
      <Box
        role="group"
        aria-label="Category"
        sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}
      >
        {CATEGORIES.map((category) => {
          const selected = filters.category === category.id;
          return (
            <Chip
              key={category.id}
              label={category.label}
              size="small"
              clickable
              color={selected ? "primary" : "default"}
              variant={selected ? "filled" : "outlined"}
              aria-pressed={selected}
              onClick={() => set({ category: selected ? null : category.id })}
              component="button"
              sx={{ typography: "s2" }}
            />
          );
        })}
        {openCategory && (
          <Chip
            label={categoryLabel(openCategory)}
            size="small"
            color="primary"
            variant="filled"
            aria-pressed
            component="button"
            onClick={() => set({ category: null })}
            onDelete={() => set({ category: null })}
            sx={{ typography: "s2" }}
          />
        )}
      </Box>
    </Box>
  );
}

IntegrationsFilterBar.propTypes = {
  filters: PropTypes.shape({
    view: PropTypes.oneOf(["all", "connected"]).isRequired,
    search: PropTypes.string.isRequired,
    category: PropTypes.string,
    auth: PropTypes.string,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  connectedCount: PropTypes.number.isRequired,
  authOptions: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, label: PropTypes.string }),
  ).isRequired,
};
