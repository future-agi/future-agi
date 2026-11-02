import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Chip,
  ClickAwayListener,
  Divider,
  InputAdornment,
  Paper,
  Popper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { METRIC_TYPE_ICONS } from "src/sections/dashboards/widgetEditorConstants";

const PICKER_LABEL = {
  metric: "Pick a metric",
  breakdown: "Pick a breakdown",
  filter: "Pick a filter",
  metric_filter: "Pick a filter",
};
const SEARCH_LABEL = {
  metric: "metrics",
  breakdown: "breakdown attributes",
  filter: "filter attributes",
  metric_filter: "filter attributes",
};

const rowSx = {
  width: "100%",
  justifyContent: "flex-start",
  textAlign: "left",
  display: "flex",
  alignItems: "center",
  gap: 1,
  px: 1.5,
  py: 0.75,
  "&:hover": { bgcolor: "action.hover" },
  "&.Mui-focusVisible": { bgcolor: "action.focus" },
};

const searchAdornment = (
  <InputAdornment position="start">
    <Iconify
      icon="solar:magnifier-linear"
      width={16}
      sx={{ color: "text.disabled" }}
    />
  </InputAdornment>
);

/**
 * The metric, filter and breakdown picker: the dashboards widget editor's
 * two-column popper (search on top, categories left, items right), reading
 * the run's catalogue instead of the trace one.
 */
export default function RunQueryPicker({
  open,
  anchorEl,
  mode,
  categories,
  items,
  onSelect,
  onClose,
}) {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  useEffect(() => {
    if (open) {
      setSearch("");
      setCategory("all");
    }
  }, [open, mode]);

  const filtered = useMemo(
    () =>
      items.filter(
        (item) =>
          (category === "all" || item.category === category) &&
          (!search.trim() ||
            item.name.toLowerCase().includes(search.trim().toLowerCase())),
      ),
    [items, category, search],
  );
  const countOf = (key) =>
    key === "all"
      ? items.length
      : items.filter((item) => item.category === key).length;

  return (
    <Popper
      open={open}
      anchorEl={anchorEl}
      placement="bottom-start"
      sx={{ zIndex: 1300 }}
    >
      <ClickAwayListener onClickAway={onClose}>
        <Paper
          role="dialog"
          aria-label={PICKER_LABEL[mode] || "Pick"}
          elevation={8}
          sx={{
            width: 600,
            maxHeight: 440,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Box sx={{ p: 1.5 }}>
            <TextField
              size="small"
              fullWidth
              autoFocus
              placeholder={`Search ${SEARCH_LABEL[mode] || "attributes"}...`}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") onClose();
              }}
              InputProps={{
                startAdornment: searchAdornment,
                endAdornment: (
                  <InputAdornment position="end">
                    <Typography typography="s3" sx={{ color: "text.disabled" }}>
                      {filtered.length} results
                    </Typography>
                  </InputAdornment>
                ),
              }}
            />
          </Box>
          <Divider />
          <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
            <Box
              sx={{
                width: 160,
                borderRight: "1px solid",
                borderColor: "divider",
                overflow: "auto",
                py: 0.5,
              }}
            >
              {categories.map((cat) => {
                const active = category === cat.key;
                return (
                  <ButtonBase
                    key={cat.key}
                    aria-pressed={active}
                    onClick={() => setCategory(cat.key)}
                    sx={{
                      ...rowSx,
                      width: "calc(100% - 8px)",
                      mx: 0.5,
                      borderRadius: 1,
                      bgcolor: active ? "action.selected" : "transparent",
                    }}
                  >
                    <Iconify
                      icon={cat.icon}
                      width={16}
                      sx={{
                        color: active ? "primary.main" : "text.secondary",
                      }}
                    />
                    <Typography
                      typography="s2"
                      sx={{
                        flex: 1,
                        fontWeight: active ? 600 : 400,
                        color: active ? "text.primary" : "text.secondary",
                      }}
                    >
                      {cat.label}
                    </Typography>
                    <Typography typography="s3" sx={{ color: "text.disabled" }}>
                      {countOf(cat.key)}
                    </Typography>
                  </ButtonBase>
                );
              })}
            </Box>
            <Box sx={{ flex: 1, overflow: "auto", maxHeight: 340 }}>
              {filtered.map((item) => (
                <ButtonBase
                  key={item.id}
                  disabled={item.disabled}
                  onClick={() => onSelect(item)}
                  sx={{ ...rowSx, opacity: item.disabled ? 0.4 : 1 }}
                >
                  <Iconify
                    icon={
                      METRIC_TYPE_ICONS[item.type] || "solar:settings-linear"
                    }
                    width={16}
                    sx={{ color: "text.disabled", flexShrink: 0 }}
                  />
                  <Typography
                    typography="s1"
                    noWrap
                    sx={{ flex: 1, minWidth: 0 }}
                  >
                    {item.name}
                  </Typography>
                  {item.outputType && (
                    <Chip
                      size="small"
                      variant="outlined"
                      label={
                        item.outputType === "SCORE" ? "score" : item.outputType
                      }
                      sx={{ flexShrink: 0 }}
                    />
                  )}
                  {item.unit && (
                    <Chip
                      size="small"
                      variant="outlined"
                      label={item.unit}
                      sx={{ flexShrink: 0 }}
                    />
                  )}
                </ButtonBase>
              ))}
              {filtered.length === 0 && (
                <Box sx={{ p: 3, textAlign: "center" }}>
                  <Typography typography="s1" sx={{ color: "text.disabled" }}>
                    No attributes found
                  </Typography>
                </Box>
              )}
            </Box>
          </Box>
        </Paper>
      </ClickAwayListener>
    </Popper>
  );
}

RunQueryPicker.propTypes = {
  open: PropTypes.bool,
  anchorEl: PropTypes.any,
  mode: PropTypes.string,
  categories: PropTypes.array.isRequired,
  items: PropTypes.array.isRequired,
  onSelect: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

/**
 * A filter's value picker: search, a checkbox list and Apply, the job the
 * dashboards' FilterValuePickerPopup does, fed from the run's calls.
 */
export function RunFilterValuePicker({
  anchorEl,
  values,
  selected,
  onApply,
  onClose,
}) {
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState(() => new Set(selected || []));
  useEffect(() => {
    setPicked(new Set(selected || []));
    setSearch("");
  }, [anchorEl, selected]);
  const shown = values.filter(
    (value) =>
      !search.trim() ||
      value.toLowerCase().includes(search.trim().toLowerCase()),
  );
  const toggle = (value) =>
    setPicked((previous) => {
      const next = new Set(previous);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  return (
    <Popper
      open={!!anchorEl}
      anchorEl={anchorEl}
      placement="bottom-start"
      sx={{ zIndex: 1300 }}
    >
      <ClickAwayListener onClickAway={onClose}>
        <Paper
          role="dialog"
          aria-label="Pick values"
          elevation={8}
          sx={{ width: 300 }}
        >
          <Box sx={{ p: 1.5 }}>
            <TextField
              size="small"
              fullWidth
              autoFocus
              placeholder="Search values..."
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              InputProps={{ startAdornment: searchAdornment }}
            />
          </Box>
          <Divider />
          <Box sx={{ maxHeight: 260, overflow: "auto", py: 0.5 }}>
            {shown.map((value) => (
              <Stack
                key={value}
                component="label"
                direction="row"
                alignItems="center"
                gap={1}
                sx={{
                  px: 1.5,
                  py: 0.5,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Checkbox
                  size="small"
                  checked={picked.has(value)}
                  onChange={() => toggle(value)}
                  sx={{ p: 0 }}
                />
                <Typography typography="s1" noWrap>
                  {value}
                </Typography>
              </Stack>
            ))}
            {!shown.length && (
              <Typography
                typography="s1"
                sx={{ p: 2, textAlign: "center", color: "text.disabled" }}
              >
                No values
              </Typography>
            )}
          </Box>
          <Divider />
          <Stack
            direction="row"
            justifyContent="flex-end"
            gap={1}
            sx={{ p: 1 }}
          >
            <Button
              size="small"
              onClick={onClose}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              onClick={() => onApply([...picked])}
            >
              Apply
            </Button>
          </Stack>
        </Paper>
      </ClickAwayListener>
    </Popper>
  );
}

RunFilterValuePicker.propTypes = {
  anchorEl: PropTypes.any,
  values: PropTypes.array.isRequired,
  selected: PropTypes.array,
  onApply: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
