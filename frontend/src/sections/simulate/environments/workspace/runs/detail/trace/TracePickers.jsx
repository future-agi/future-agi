import PropTypes from "prop-types";
import { useLayoutEffect, useRef, useState } from "react";
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
import CustomTooltip from "src/components/tooltip";
import {
  TRACE_COLUMNS,
  defaultTraceColumns,
  GROUPINGS,
  neutralCheckboxSx,
  toolbarButtonSx,
} from "./traceTable.constants";

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
        sx={toolbarButtonSx}
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

// The theme's icon slot (36px wide plus a 16px margin) leaves each label far
// from its checkbox; a checkbox-wide slot keeps the labels lined up.
const columnItemSx = {
  py: 0.5,
  "& .MuiListItemIcon-root": { minWidth: 28, mr: 1 },
};

// A column name on one line, cut off with an ellipsis; the full name shows on
// hover, only when it was cut.
function ColumnLabel({ label }) {
  const ref = useRef(null);
  const [cut, setCut] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el) setCut(el.scrollWidth > el.clientWidth);
  }, [label]);
  return (
    <CustomTooltip show={cut} title={label} placement="left" arrow>
      <ListItemText
        primary={label}
        primaryTypographyProps={{ typography: "s2", noWrap: true, ref }}
      />
    </CustomTooltip>
  );
}
ColumnLabel.propTypes = {
  label: PropTypes.string.isRequired,
};

const toggled = (set, key) => {
  const next = new Set(set);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
};

// Column-visibility picker, bucketed into sections in declaration order: the
// fixed columns, then one entry per evaluation the run has. Those come from the
// calls API, and an eval is shown unless the user turned it off.
export function TraceColumnsPicker({
  value,
  onChange,
  hidden,
  evals = [],
  hiddenEvals = new Set(),
  onHiddenEvalsChange,
}) {
  const offered = hidden
    ? TRACE_COLUMNS.filter((c) => !hidden.has(c.key))
    : TRACE_COLUMNS;
  const [anchor, setAnchor] = useState(null);
  const shownCount =
    offered.filter((c) => value.has(c.key)).length +
    evals.filter((e) => !hiddenEvals.has(e.id)).length;
  const sections = offered
    .map((c) => ({
      key: c.key,
      label: c.label,
      group: c.group,
      checked: value.has(c.key),
      onToggle: () => onChange(toggled(value, c.key)),
    }))
    .concat(
      evals.map((e) => ({
        key: `eval:${e.id}`,
        label: e.name,
        group: "Evaluations",
        checked: !hiddenEvals.has(e.id),
        onToggle: () => onHiddenEvalsChange?.(toggled(hiddenEvals, e.id)),
      })),
    )
    .reduce((acc, c) => {
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
        sx={toolbarButtonSx}
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
          {shownCount}/{offered.length + evals.length}
        </Box>
      </Button>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        // A run can carry many evaluations, some with long names: the list
        // scrolls rather than running the height of the window, and a long
        // name is cut off rather than stretching the menu across the table.
        slotProps={{
          paper: { sx: { minWidth: 240, maxWidth: 320, maxHeight: 420 } },
        }}
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
            <MenuItem key={c.key} onClick={c.onToggle} sx={columnItemSx}>
              <ListItemIcon>
                <Checkbox
                  size="small"
                  checked={c.checked}
                  sx={{ p: 0, ...neutralCheckboxSx }}
                />
              </ListItemIcon>
              <ColumnLabel label={c.label} />
            </MenuItem>
          )),
        ])}
        <Divider sx={{ my: 0.5 }} />
        <MenuItem
          onClick={() => {
            onChange(defaultTraceColumns());
            onHiddenEvalsChange?.(new Set());
          }}
          sx={columnItemSx}
        >
          <ListItemIcon>
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
  hidden: PropTypes.instanceOf(Set),
  evals: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  ),
  hiddenEvals: PropTypes.instanceOf(Set),
  onHiddenEvalsChange: PropTypes.func,
};
