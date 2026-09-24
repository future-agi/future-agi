import PropTypes from "prop-types";
import { useMemo, useState, useEffect } from "react";
import {
  Box, Button, Checkbox, Chip, ClickAwayListener, Divider, InputAdornment,
  Paper, Popper, Stack, TextField, Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { METRIC_TYPE_ICONS } from "src/sections/dashboards/widgetEditorParts";

/**
 * The shared metric / filter / breakdown picker — same two-column popper as
 * the dashboards widget editor (search on top, categories on the left, items
 * on the right), reading the simulation catalogue instead of the trace one.
 */
export default function SimQueryPicker({ open, anchorEl, mode, categories, items, onSelect, onClose }) {
  const theme = useTheme();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  useEffect(() => { if (open) { setSearch(""); setCategory("all"); } }, [open, mode]);

  const filtered = useMemo(() => items.filter((it) => (category === "all" || it.category === category)
    && (!search.trim() || it.name.toLowerCase().includes(search.trim().toLowerCase()))), [items, category, search]);
  const countOf = (key) => (key === "all" ? items.length : items.filter((it) => it.category === key).length);

  return (
    <Popper open={open} anchorEl={anchorEl} placement="bottom-start" sx={{ zIndex: 1300 }}>
      <ClickAwayListener onClickAway={onClose}>
        <Paper
          elevation={8}
          sx={{
            width: 600,
            maxHeight: 440,
            display: "flex",
            flexDirection: "column",
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: 2,
          }}
        >
          <Box sx={{ p: 1.5 }}>
            <TextField
              size="small"
              fullWidth
              autoFocus
              placeholder={`Search ${mode === "metric" ? "metrics" : mode === "breakdown" ? "breakdown attributes" : "filter attributes"}...`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon="eva:search-fill" width={18} sx={{ color: "text.disabled" }} />
                  </InputAdornment>
                ),
                endAdornment: (
                  <InputAdornment position="end">
                    <Typography variant="caption" sx={{ color: "text.disabled", fontSize: 11 }}>
                      {filtered.length} results
                    </Typography>
                  </InputAdornment>
                ),
              }}
            />
          </Box>
          <Divider />
          <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
            <Box sx={{ width: 160, borderRight: `1px solid ${theme.palette.divider}`, overflow: "auto", py: 0.5 }}>
              {categories.map((cat) => (
                <Box
                  key={cat.key}
                  onClick={() => setCategory(cat.key)}
                  sx={{
                    display: "flex", alignItems: "center", gap: 1, px: 1.5, py: 0.75,
                    cursor: "pointer", borderRadius: 1, mx: 0.5,
                    bgcolor: category === cat.key ? "action.selected" : "transparent",
                    "&:hover": { bgcolor: category === cat.key ? "action.selected" : "action.hover" },
                  }}
                >
                  <Iconify icon={cat.icon} width={16} sx={{ color: category === cat.key ? "primary.main" : "text.secondary" }} />
                  <Typography
                    variant="body2"
                    sx={{
                      fontSize: "12px", flex: 1,
                      fontWeight: category === cat.key ? 600 : 400,
                      color: category === cat.key ? "text.primary" : "text.secondary",
                    }}
                  >
                    {cat.label}
                  </Typography>
                  <Typography variant="caption" sx={{ color: "text.disabled", fontSize: 10 }}>
                    {countOf(cat.key)}
                  </Typography>
                </Box>
              ))}
            </Box>
            <Box sx={{ flex: 1, overflow: "auto", maxHeight: 340 }}>
              {filtered.map((opt) => (
                <Box
                  key={opt.id}
                  onClick={opt.disabled ? undefined : () => onSelect(opt)}
                  sx={{
                    display: "flex", alignItems: "center", gap: 1, px: 1.5, py: 0.75,
                    cursor: opt.disabled ? "default" : "pointer",
                    opacity: opt.disabled ? 0.4 : 1,
                    "&:hover": { bgcolor: opt.disabled ? "transparent" : "action.hover" },
                  }}
                >
                  <Iconify icon={METRIC_TYPE_ICONS[opt.type] || "mdi:cog-outline"} width={15} sx={{ color: "text.disabled", flexShrink: 0 }} />
                  <Typography
                    variant="body2"
                    title={opt.name}
                    sx={{ fontSize: "13px", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  >
                    {opt.name}
                  </Typography>
                  {opt.outputType && (
                    <Chip size="small" variant="outlined" label={opt.outputType === "SCORE" ? "score" : opt.outputType} sx={{ height: 18, fontSize: 10, flexShrink: 0 }} />
                  )}
                  {opt.unit && (
                    <Chip size="small" variant="outlined" label={opt.unit} sx={{ height: 18, fontSize: 10, flexShrink: 0 }} />
                  )}
                  <Chip size="small" label="Sim" color="secondary" variant="outlined" sx={{ height: 18, fontSize: 10, flexShrink: 0 }} />
                </Box>
              ))}
              {filtered.length === 0 && (
                <Box sx={{ p: 3, textAlign: "center" }}>
                  <Typography variant="body2" color="text.disabled">No attributes found</Typography>
                </Box>
              )}
            </Box>
          </Box>
        </Paper>
      </ClickAwayListener>
    </Popper>
  );
}

SimQueryPicker.propTypes = {
  open: PropTypes.bool,
  anchorEl: PropTypes.any,
  mode: PropTypes.string,
  categories: PropTypes.array.isRequired,
  items: PropTypes.array.isRequired,
  onSelect: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

/**
 * Value picker for a filter — search + checkbox list + Apply, the same job
 * as the dashboards' FilterValuePickerPopup but fed from the runs in memory.
 */
export function SimFilterValuePicker({ anchorEl, values, selected, onApply, onClose }) {
  const theme = useTheme();
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState(() => new Set(selected || []));
  useEffect(() => { setPicked(new Set(selected || [])); setSearch(""); }, [anchorEl, selected]);
  const shown = values.filter((v) => !search.trim() || v.toLowerCase().includes(search.trim().toLowerCase()));
  const toggle = (v) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(v)) next.delete(v); else next.add(v);
    return next;
  });
  return (
    <Popper open={!!anchorEl} anchorEl={anchorEl} placement="bottom-start" sx={{ zIndex: 1300 }}>
      <ClickAwayListener onClickAway={onClose}>
        <Paper elevation={8} sx={{ width: 300, border: `1px solid ${theme.palette.divider}`, borderRadius: 2 }}>
          <Box sx={{ p: 1.5 }}>
            <TextField
              size="small" fullWidth autoFocus placeholder="Search values..."
              value={search} onChange={(e) => setSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon="eva:search-fill" width={16} sx={{ color: "text.disabled" }} />
                  </InputAdornment>
                ),
              }}
            />
          </Box>
          <Divider />
          <Box sx={{ maxHeight: 260, overflow: "auto", py: 0.5 }}>
            {shown.map((v) => (
              <Stack
                key={v} direction="row" alignItems="center" gap={1}
                onClick={() => toggle(v)}
                sx={{ px: 1.5, py: 0.5, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
              >
                <Checkbox size="small" checked={picked.has(v)} sx={{ p: 0 }} tabIndex={-1} />
                <Typography variant="body2" noWrap sx={{ fontSize: "13px" }}>{v}</Typography>
              </Stack>
            ))}
            {!shown.length && (
              <Typography variant="body2" color="text.disabled" sx={{ p: 2, textAlign: "center" }}>No values</Typography>
            )}
          </Box>
          <Divider />
          <Stack direction="row" justifyContent="flex-end" gap={1} sx={{ p: 1 }}>
            <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>Cancel</Button>
            <Button size="small" variant="contained" onClick={() => onApply([...picked])}>Apply</Button>
          </Stack>
        </Paper>
      </ClickAwayListener>
    </Popper>
  );
}

SimFilterValuePicker.propTypes = {
  anchorEl: PropTypes.any,
  values: PropTypes.array.isRequired,
  selected: PropTypes.array,
  onApply: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
