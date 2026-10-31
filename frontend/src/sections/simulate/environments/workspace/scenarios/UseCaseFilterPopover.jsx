import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box, Stack, Typography, Button, Popover, Checkbox, IconButton, InputBase,
} from "@mui/material";

import Iconify from "src/components/iconify";
import { ROW_HEIGHT } from "src/theme/sizes";
import { SCENARIOS_COPY } from "./scenarios.constants";
import { USE_CASE_SHAPE } from "./scenarios.shapes";

const onKeyActivate = (fn) => (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fn();
  }
};

// Use-case filter popover. A small custom popover — fixed width, wrapping
// labels, an inline search for long lists, and a clear header + footer so the
// frame reads as a real filter panel rather than a menu.
export default function UseCaseFilterPopover({ anchorEl, onClose, allUseCases, countBy, selected, onChange }) {
  const [q, setQ] = useState("");

  const handleClose = () => { setQ(""); onClose(); };

  const filtered = q.trim()
    ? allUseCases.filter((uc) => uc.label.toLowerCase().includes(q.trim().toLowerCase()))
    : allUseCases;

  const toggle = (id) => onChange(selected.includes(id)
    ? selected.filter((v) => v !== id)
    : [...selected, id]);

  return (
    <Popover
      open={!!anchorEl}
      anchorEl={anchorEl}
      onClose={handleClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{
        paper: {
          sx: {
            width: 420, mt: 0.75, borderRadius: "8px",
            border: "1px solid", borderColor: "divider",
            overflow: "hidden",
          },
        },
      }}
    >
      <Stack
        direction="row" alignItems="center"
        sx={{ px: 1.5, height: 36, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Iconify icon="mage:filter" width={14} sx={{ color: "text.secondary", mr: 0.75 }} />
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium", flex: 1 }}>
          {SCENARIOS_COPY.filterTitle}
        </Typography>
        {selected.length > 0 && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mr: 0.75 }}>
            {selected.length} selected
          </Typography>
        )}
      </Stack>

      {allUseCases.length > 6 && (
        <Box sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider" }}>
          <Box
            sx={{
              display: "flex", alignItems: "center", gap: 0.75,
              px: 1.25, height: 32, borderRadius: 1,
              border: "1px solid", borderColor: "divider",
              transition: "border-color .12s ease",
              "&:focus-within": { borderColor: "primary.main" },
            }}
          >
            <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            <InputBase
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search use cases…"
              autoFocus
              sx={{ typography: "m3", flex: 1, color: "text.primary" }}
            />
            {q && (
              <IconButton size="small" onClick={() => setQ("")} sx={{ p: 0.25 }} aria-label="Clear use-case search">
                <Iconify icon="solar:close-circle-linear" width={14} sx={{ color: "text.subtitle" }} />
              </IconButton>
            )}
          </Box>
        </Box>
      )}

      <Box sx={{ maxHeight: 340, overflowY: "auto", py: 0.5 }}>
        {filtered.length === 0 ? (
          <Box sx={{ px: 2, py: 4, textAlign: "center" }}>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              No use cases match &ldquo;{q}&rdquo;.
            </Typography>
          </Box>
        ) : (
          filtered.map((uc) => {
            const on = selected.includes(uc.id);
            const count = countBy(uc.id);
            return (
              <Stack
                key={uc.id}
                direction="row" alignItems="flex-start" spacing={1.25}
                role="button" tabIndex={0}
                onClick={() => toggle(uc.id)}
                onKeyDown={onKeyActivate(() => toggle(uc.id))}
                sx={{ px: 1.5, py: 1, minHeight: ROW_HEIGHT.default, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
              >
                <Checkbox
                  size="small" checked={on} disableRipple tabIndex={-1}
                  sx={{ p: 0, mt: "1px", flexShrink: 0 }}
                />
                <Typography
                  sx={{
                    typography: "s1", color: "text.primary",
                    flex: 1, minWidth: 0,
                    whiteSpace: "normal",
                  }}
                >
                  {uc.label}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3", color: "text.subtitle",
                    flexShrink: 0, mt: "1px",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {count}
                </Typography>
              </Stack>
            );
          })
        )}
      </Box>

      <Stack
        direction="row" alignItems="center" justifyContent="space-between"
        sx={{ px: 1.5, py: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button
          size="small"
          color="inherit"
          onClick={() => onChange([])}
          disabled={selected.length === 0}
        >
          Clear all
        </Button>
        <Button
          size="small" variant="contained"
          onClick={handleClose}
        >
          Done
        </Button>
      </Stack>
    </Popover>
  );
}

UseCaseFilterPopover.propTypes = {
  anchorEl: PropTypes.instanceOf(Element),
  onClose: PropTypes.func,
  allUseCases: PropTypes.arrayOf(USE_CASE_SHAPE),
  countBy: PropTypes.func,
  selected: PropTypes.arrayOf(PropTypes.string),
  onChange: PropTypes.func,
};
