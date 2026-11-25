import PropTypes from "prop-types";
import { useState } from "react";
import { Box, ButtonBase, Collapse, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

export default function ReviewSection({
  label,
  count,
  countText = null,
  source = null,
  children,
}) {
  const [open, setOpen] = useState(true);
  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <ButtonBase
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        sx={{
          width: "100%",
          justifyContent: "flex-start",
          gap: 1,
          py: 1.25,
          px: 0.5,
          borderRadius: 0.5,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon="solar:alt-arrow-right-linear"
          width={16}
          sx={{
            color: "text.tertiary",
            transition: "transform .15s ease",
            transform: open ? "rotate(90deg)" : "none",
          }}
        />
        <Typography sx={{ typography: "m3", fontWeight: 500 }}>
          {label}
        </Typography>
        <Typography
          sx={{
            typography: "m3",
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {countText ?? count}
        </Typography>
        {source && (
          <Typography
            component="span"
            sx={{ typography: "s3", color: "text.tertiary", ml: 1 }}
          >
            {source}
          </Typography>
        )}
      </ButtonBase>
      <Collapse in={open} unmountOnExit>
        <Box
          sx={{
            mb: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
            "& > *": { borderBottom: "1px solid", borderColor: "divider" },
            "& > *:last-child": { borderBottom: 0 },
          }}
        >
          {children}
        </Box>
      </Collapse>
    </Box>
  );
}

ReviewSection.propTypes = {
  label: PropTypes.string.isRequired,
  count: PropTypes.number,
  countText: PropTypes.string,
  source: PropTypes.string,
  children: PropTypes.node,
};
