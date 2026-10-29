import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Divider,
  IconButton,
  InputBase,
  ListSubheader,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { DOMAIN_FILTERS, STATUS_FILTERS } from "../_mock/trialFilters";

/* One section of the filter menu: pick one, pick it again to clear. */
function Section({ title, options, value, onPick }) {
  return [
    <ListSubheader
      key={`${title}-h`}
      sx={{ typography: "s3", fontWeight: 600, lineHeight: "28px" }}
    >
      {title}
    </ListSubheader>,
    ...options.map((o) => (
      <MenuItem
        key={`${title}-${o.id}`}
        selected={value === o.id}
        onClick={() => onPick(value === o.id ? null : o.id)}
        sx={{ typography: "s2", gap: 1 }}
      >
        <Box component="span" sx={{ flex: 1 }}>
          {o.label}
        </Box>
        {value === o.id && (
          <Iconify icon="solar:check-read-linear" width={14} />
        )}
      </MenuItem>
    )),
  ];
}

/**
 * The live run's scenario filters: search by scenario, then status, failure
 * domain and actor from one menu. The list under it shows what matches.
 */
export default function TrialFilters({
  value,
  onChange,
  actors,
  shown,
  total,
}) {
  const [anchor, setAnchor] = useState(null);
  const picked = ["status", "domain", "actor"].filter((k) => value[k]).length;
  const set = (k) => (v) => onChange({ ...value, [k]: v });
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 1.5,
        height: 36,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Iconify
        icon="solar:magnifer-linear"
        width={14}
        sx={{ color: "text.disabled", flexShrink: 0 }}
      />
      <InputBase
        value={value.q || ""}
        onChange={(e) => onChange({ ...value, q: e.target.value })}
        placeholder={
          shown === total ? "Search scenarios" : `${shown} of ${total} shown`
        }
        inputProps={{ "aria-label": "Search scenarios" }}
        sx={{ typography: "s2", flex: 1, minWidth: 0 }}
      />
      {(picked > 0 || value.q) && (
        <Tooltip arrow title="Clear filters">
          <IconButton
            size="small"
            aria-label="Clear filters"
            onClick={() => onChange({})}
          >
            <Iconify icon="mingcute:close-line" width={14} />
          </IconButton>
        </Tooltip>
      )}
      <Tooltip arrow title="Filter by status, failure domain or actor">
        <IconButton
          size="small"
          aria-label="Filter scenarios"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ color: picked ? "text.primary" : "text.secondary" }}
        >
          <Iconify icon="solar:filter-linear" width={16} />
          {picked > 0 && (
            <Box
              component="span"
              sx={{ typography: "s3", fontWeight: 600, ml: 0.25 }}
            >
              {picked}
            </Box>
          )}
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        MenuListProps={{ dense: true, sx: { minWidth: 220 } }}
      >
        {Section({
          title: "Status",
          options: STATUS_FILTERS,
          value: value.status,
          onPick: set("status"),
        })}
        <Divider />
        {Section({
          title: "Failure domain",
          options: DOMAIN_FILTERS,
          value: value.domain,
          onPick: set("domain"),
        })}
        {actors.length > 0 && <Divider />}
        {actors.length > 0 &&
          Section({
            title: "Actor",
            options: [
              ...actors.map((a) => ({ id: a, label: a })),
              { id: "none", label: "No actor" },
            ],
            value: value.actor,
            onPick: set("actor"),
          })}
      </Menu>
    </Stack>
  );
}
TrialFilters.propTypes = {
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  actors: PropTypes.array.isRequired,
  shown: PropTypes.number,
  total: PropTypes.number,
};
