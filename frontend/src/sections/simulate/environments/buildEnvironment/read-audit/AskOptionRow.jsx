import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

import { BUILD_TONES } from "../buildTones";

// One selectable answer in the open-questions stepper. The designer's row is a
// click target only; here it also carries keyboard semantics (role="button",
// Enter/Space → onSelect) and aria-pressed so it reads as a toggle. The keydown
// guard ignores events bubbling up from the "Other" text field so typing a
// space there never triggers a select.
export default function AskOptionRow({
  label,
  description,
  selected = false,
  index,
  onSelect,
  bottomChildren,
}) {
  const handleKeyDown = (e) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect?.();
    }
  };

  return (
    <Box
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={() => onSelect?.()}
      onKeyDown={handleKeyDown}
      sx={{
        p: 1.25, borderRadius: 1.25, cursor: "pointer",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.03),
        "&:hover": {
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
        },
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.25}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.primary" }}>
            {label}
          </Typography>
          {description && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
              {description}
            </Typography>
          )}
        </Box>
        <Box
          sx={{
            minWidth: 22, height: 20, px: 0.75, borderRadius: 0.75, flexShrink: 0, mt: "1px",
            display: "grid", placeItems: "center",
            border: "1px solid",
            borderColor: selected ? BUILD_TONES.accent : (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.12),
            bgcolor: selected ? BUILD_TONES.accent : "transparent",
            color: selected ? "common.white" : "text.subtitle",
            typography: "s3", fontWeight: "fontWeightBold", fontVariantNumeric: "tabular-nums",
          }}
        >
          {index}
        </Box>
      </Stack>
      {bottomChildren}
    </Box>
  );
}

AskOptionRow.propTypes = {
  label: PropTypes.string,
  description: PropTypes.string,
  selected: PropTypes.bool,
  index: PropTypes.number,
  onSelect: PropTypes.func,
  bottomChildren: PropTypes.node,
};
