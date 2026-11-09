import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  MenuItem,
  Popover,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { toneSx } from "../toneSx";

/**
 * Version picker of a run card. Same trigger as SearchableSelectControl;
 * drafts are listed but disabled ("Save it in Prompts to use it").
 * options: [{ value, label, hint, chip, disabled, disabledHint }]
 */
const VersionMenuSelect = ({
  value,
  options,
  onChange,
  isPending,
  placeholder,
}) => {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const selected = options.find((o) => o.value === value);

  return (
    <>
      <Box
        ref={anchorRef}
        role="button"
        aria-label="Version"
        onClick={() => setOpen(true)}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          height: 22,
          px: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          cursor: "pointer",
          bgcolor: "background.paper",
          "&:hover": { borderColor: "text.disabled" },
        }}
      >
        <Typography
          sx={{ fontSize: 12, fontWeight: 500, lineHeight: 1 }}
          noWrap
        >
          {selected?.label ?? placeholder}
        </Typography>
        {isPending ? (
          <CircularProgress size={10} sx={{ color: "text.disabled" }} />
        ) : (
          <Iconify
            icon="eva:chevron-down-fill"
            width={14}
            sx={{ color: "text.secondary" }}
          />
        )}
      </Box>
      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        PaperProps={{
          elevation: 4,
          sx: { mt: 0.5, borderRadius: 1, minWidth: 220, maxWidth: 300 },
        }}
      >
        <Box sx={{ maxHeight: 260, overflowY: "auto", py: 0.5 }}>
          {options.map((o) => (
            <MenuItem
              key={o.value}
              dense
              disabled={o.disabled}
              selected={o.value === value}
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
              sx={{
                px: 1.5,
                py: 0.5,
                display: "block",
                "&.Mui-disabled": { opacity: 1 },
              }}
            >
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  color={o.disabled ? "text.disabled" : "text.primary"}
                >
                  {o.label}
                </Typography>
                {o.chip ? (
                  <Box
                    component="span"
                    sx={{
                      typography: "s3",
                      px: 0.75,
                      borderRadius: 0.5,
                      ...toneSx(o.disabled ? "neutral" : "pass"),
                    }}
                  >
                    {o.chip}
                  </Box>
                ) : null}
              </Box>
              {o.disabled && o.disabledHint ? (
                <Typography typography="s3" color="text.secondary">
                  {o.disabledHint}
                </Typography>
              ) : o.hint ? (
                <Typography typography="s3" color="text.secondary" noWrap>
                  {o.hint}
                </Typography>
              ) : null}
            </MenuItem>
          ))}
        </Box>
      </Popover>
    </>
  );
};

VersionMenuSelect.propTypes = {
  value: PropTypes.string,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      hint: PropTypes.string,
      chip: PropTypes.string,
      disabled: PropTypes.bool,
      disabledHint: PropTypes.string,
    }),
  ).isRequired,
  onChange: PropTypes.func.isRequired,
  isPending: PropTypes.bool,
  placeholder: PropTypes.string,
};

export default VersionMenuSelect;
