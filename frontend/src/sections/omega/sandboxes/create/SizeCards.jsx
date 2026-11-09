import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Typography from "@mui/material/Typography";
import { alpha } from "@mui/material/styles";

import { sizeSummary } from "../lib/createForm";
import { sizeReason } from "../lib/offerCopy";

/**
 * The four sandbox sizes as a radio group of cards: what each gives (vCPU,
 * memory) and what it is good for. A size this workspace is not offered stays
 * visible, dimmed, with EE's own reason (the binding's limit, the host's
 * capacity, or a setting), so nobody wonders where XL went. The size the
 * chosen template suggests carries a "Suggested" mark.
 */
export default function SizeCards({ sizes, value, suggested, onChange }) {
  const offered = sizes.filter((size) => size.available);
  const move = (event, index) => {
    const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    if (!(event.key in keys)) return;
    event.preventDefault();
    const at = offered.findIndex((size) => size.id === sizes[index].id);
    const next =
      offered[(at + keys[event.key] + offered.length) % offered.length];
    if (next) onChange(next.id);
  };
  return (
    <Box
      role="radiogroup"
      aria-label="Size"
      data-testid="create-sizes"
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(4, 1fr)" },
        gap: 1,
      }}
    >
      {sizes.map((size, index) => {
        const selected = size.id === value;
        return (
          <ButtonBase
            key={size.id}
            role="radio"
            aria-checked={selected}
            aria-disabled={!size.available}
            disabled={!size.available}
            tabIndex={selected || (!value && index === 0) ? 0 : -1}
            onClick={() => onChange(size.id)}
            onKeyDown={(event) => move(event, index)}
            data-testid={`create-size-${size.id}`}
            title={size.available ? size.goodFor : sizeReason(size)}
            sx={(theme) => ({
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              justifyContent: "flex-start",
              textAlign: "left",
              gap: 0.25,
              p: 1.25,
              minHeight: 96,
              borderRadius: 1,
              border: "1px solid",
              borderColor: selected ? "primary.main" : "divider",
              bgcolor: selected
                ? alpha(theme.palette.primary.main, 0.06)
                : "background.paper",
              boxShadow: selected
                ? `inset 0 0 0 1px ${theme.palette.primary.main}`
                : "none",
              opacity: size.available ? 1 : 0.55,
              transition: theme.transitions.create(
                ["border-color", "background-color"],
                { duration: theme.transitions.duration.shortest },
              ),
              "&:hover": size.available
                ? { borderColor: selected ? "primary.main" : "text.disabled" }
                : undefined,
              "&.Mui-focusVisible": {
                outline: `2px solid ${theme.palette.primary.main}`,
                outlineOffset: 2,
              },
            })}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                gap: 0.5,
                width: "100%",
                flexWrap: "wrap",
              }}
            >
              <Typography
                typography="s1"
                fontWeight="fontWeightMedium"
                color="text.primary"
              >
                {size.label}
              </Typography>
              {size.available && size.id === suggested && (
                <Typography
                  component="span"
                  typography="s3"
                  color="primary.main"
                  data-testid="create-size-suggested"
                >
                  Suggested
                </Typography>
              )}
            </Box>
            <Typography
              typography="s2"
              color="text.primary"
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {sizeSummary(size)}
            </Typography>
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ lineHeight: 1.35 }}
            >
              {size.available ? size.goodFor : sizeReason(size)}
            </Typography>
          </ButtonBase>
        );
      })}
    </Box>
  );
}

SizeCards.propTypes = {
  sizes: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      cpus: PropTypes.number.isRequired,
      memoryMb: PropTypes.number.isRequired,
      goodFor: PropTypes.string,
      available: PropTypes.bool.isRequired,
      reason: PropTypes.string,
    }),
  ).isRequired,
  value: PropTypes.string,
  suggested: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};
