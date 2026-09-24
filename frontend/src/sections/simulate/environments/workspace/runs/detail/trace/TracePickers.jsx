import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Typography,
  Button,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
  Checkbox,
} from "@mui/material";

import Iconify from "src/components/iconify";
import {
  TRACE_COLUMNS,
  defaultTraceColumns,
  GROUPINGS,
  neutralCheckboxSx,
} from "./traceTable.constants";

const pickerButtonSx = {
  typography: "s2",
  fontWeight: "fontWeightBold",
  textTransform: "none",
  height: 32,
  color: "text.primary",
  borderColor: "divider",
  "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
};

// Group-by axis picker.
export function TraceGroupByPicker({ value, onChange }) {
  const [anchor, setAnchor] = useState(null);
  const current = GROUPINGS.find((g) => g.id === value) || GROUPINGS[0];
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={
          <Iconify
            icon={current.icon}
            width={15}
            sx={{ color: "primary.main" }}
          />
        }
        endIcon={
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={12}
            sx={{ color: "text.subtitle" }}
          />
        }
        sx={pickerButtonSx}
      >
        Group by
        <Box
          component="span"
          sx={{
            mx: 0.5,
            color: "text.subtitle",
            fontWeight: "fontWeightRegular",
          }}
        >
          ·
        </Box>
        <Box component="span" sx={{ color: "primary.main" }}>
          {current.label}
        </Box>
      </Button>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)}>
        {GROUPINGS.map((g) => (
          <MenuItem
            key={g.id}
            selected={g.id === value}
            onClick={() => {
              onChange(g.id);
              setAnchor(null);
            }}
            sx={{ py: 0.75, gap: 0.75 }}
          >
            <Iconify icon={g.icon} width={16} />
            <Typography sx={{ typography: "s2", flex: 1 }}>
              {g.label}
            </Typography>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
TraceGroupByPicker.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

// Column-visibility picker. Bucketed into sections in declaration order.
export function TraceColumnsPicker({ value, onChange }) {
  const [anchor, setAnchor] = useState(null);
  const shownCount = TRACE_COLUMNS.filter((c) => value.has(c.key)).length;
  const toggle = (key) => {
    const next = new Set(value);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(next);
  };
  const sections = TRACE_COLUMNS.reduce((acc, c) => {
    const last = acc[acc.length - 1];
    if (last && last.name === c.group) last.items.push(c);
    else acc.push({ name: c.group, items: [c] });
    return acc;
  }, []);
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={
          <Iconify
            icon="solar:widget-4-linear"
            width={15}
            sx={{ color: "text.subtitle" }}
          />
        }
        endIcon={
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={12}
            sx={{ color: "text.subtitle" }}
          />
        }
        sx={pickerButtonSx}
      >
        Columns
        <Box
          component="span"
          sx={{
            mx: 0.5,
            color: "text.subtitle",
            fontWeight: "fontWeightRegular",
          }}
        >
          ·
        </Box>
        <Box component="span" sx={{ color: "text.subtitle" }}>
          {shownCount}/{TRACE_COLUMNS.length}
        </Box>
      </Button>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { minWidth: 240 } } }}
      >
        {sections.map((section, i) => [
          i > 0 && <Divider key={`div-${section.name}`} sx={{ my: 0.5 }} />,
          <Typography
            key={`h-${section.name}`}
            sx={{
              typography: "s3",
              fontWeight: "fontWeightBold",
              color: "text.subtitle",
              textTransform: "uppercase",
              letterSpacing: 0.4,
              px: 2,
              py: 0.5,
              mt: i === 0 ? 0.5 : 0,
            }}
          >
            {section.name}
          </Typography>,
          ...section.items.map((c) => (
            <MenuItem
              key={c.key}
              onClick={() => toggle(c.key)}
              sx={{ py: 0.5 }}
            >
              <ListItemIcon sx={{ minWidth: 32 }}>
                <Checkbox
                  size="small"
                  checked={value.has(c.key)}
                  sx={{ p: 0, ...neutralCheckboxSx }}
                />
              </ListItemIcon>
              <ListItemText
                primary={c.label}
                primaryTypographyProps={{ typography: "s2" }}
              />
            </MenuItem>
          )),
        ])}
        <Divider sx={{ my: 0.5 }} />
        <MenuItem
          onClick={() => onChange(defaultTraceColumns())}
          sx={{ py: 0.5 }}
        >
          <ListItemIcon sx={{ minWidth: 32 }}>
            <Iconify icon="solar:restart-linear" width={16} />
          </ListItemIcon>
          <ListItemText
            primary="Reset to defaults"
            primaryTypographyProps={{ typography: "s2" }}
          />
        </MenuItem>
      </Menu>
    </>
  );
}
TraceColumnsPicker.propTypes = {
  value: PropTypes.instanceOf(Set).isRequired,
  onChange: PropTypes.func.isRequired,
};
