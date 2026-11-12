import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { alpha } from "@mui/material/styles";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";
import {
  choiceValue,
  reviewContextButtons,
  valueChoice,
} from "src/sections/develop-detail/Review/reviewModel";

/**
 * The three pairwise buttons for a categorical label with
 * `settings.kind === "pairwise"` (PRODUCT-SPEC §3.12). Sides follow the
 * item's `review_context.left`: key 1 = left response better, 2 = tie,
 * 3 = right. The stored value is `{selected: ["A" | "Tie" | "B"]}`.
 */
export default function PairwiseChoice({
  label,
  value,
  onChange,
  reviewContext,
  hasError,
  disabled,
}) {
  const current = valueChoice(value);
  const buttons = reviewContextButtons(reviewContext);
  return (
    <Box
      sx={(t) => ({
        borderRadius: 0.5,
        border: "1px solid",
        borderColor: hasError
          ? alpha(t.palette.error.main, 0.55)
          : alpha(t.palette.text.primary, 0.08),
        bgcolor: alpha(
          t.palette.primary.main,
          t.palette.mode === "dark" ? 0.06 : 0.035,
        ),
        px: 1.5,
        py: 1.25,
      })}
    >
      <Stack direction="row" alignItems="center" gap={0.75} sx={{ mb: 0.25 }}>
        <Box
          sx={{
            width: 3,
            height: 14,
            borderRadius: 0.5,
            bgcolor: hasError ? "error.main" : "primary.main",
          }}
        />
        <Typography variant="body2" fontWeight={600} sx={{ flex: 1 }}>
          {label.name}
          <Box component="span" sx={{ color: "error.main" }}>
            {" *"}
          </Box>
        </Typography>
      </Stack>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 1 }}
      >
        {label.description || "Which response is better for this row?"}
      </Typography>
      <Stack
        spacing={0.75}
        role="radiogroup"
        aria-label={label.name}
        data-pairwise-choices
        onKeyDown={(e) => {
          // Arrow keys move between the three choices (RW#42).
          if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
          const list = [
            ...e.currentTarget.querySelectorAll("[role=radio]:not([disabled])"),
          ];
          const at = list.indexOf(document.activeElement);
          if (at < 0) return;
          e.preventDefault();
          e.stopPropagation();
          const next =
            list[
              (at + (e.key === "ArrowDown" ? 1 : list.length - 1)) % list.length
            ];
          next?.focus();
        }}
      >
        {buttons.map((b, i) => {
          const active = current === b.choice;
          return (
            <CustomTooltip
              key={b.key}
              show
              size="small"
              placement="left"
              title={`${b.label} (${b.key}). Then Enter to submit.`}
            >
              <span>
                <Button
                  fullWidth
                  disabled={disabled}
                  variant={active ? "contained" : "outlined"}
                  color={active ? "primary" : "inherit"}
                  role="radio"
                  aria-checked={active}
                  tabIndex={active || (!current && i === 0) ? 0 : -1}
                  data-pairwise-choice={b.key}
                  onClick={() =>
                    onChange(active ? { selected: [] } : choiceValue(b.choice))
                  }
                  sx={(t) => ({
                    justifyContent: "flex-start",
                    gap: 1.25,
                    minHeight: 40,
                    py: 0.75,
                    borderColor: active ? undefined : "divider",
                    color: active ? undefined : "text.primary",
                    bgcolor: active ? undefined : "background.paper",
                    "&:hover": active
                      ? undefined
                      : {
                          bgcolor: "action.hover",
                          borderColor: "text.disabled",
                        },
                    "&.Mui-focusVisible": {
                      outline: `2px solid ${t.palette.primary.main}`,
                      outlineOffset: 2,
                    },
                  })}
                >
                  <KeyCap>{b.key}</KeyCap>
                  <Typography
                    variant="body2"
                    fontWeight={500}
                    sx={{ flex: 1, textAlign: "left" }}
                  >
                    {b.label}
                  </Typography>
                  {active && (
                    <Iconify icon="mdi:check-circle" width={18} aria-hidden />
                  )}
                </Button>
              </span>
            </CustomTooltip>
          );
        })}
      </Stack>
    </Box>
  );
}

PairwiseChoice.propTypes = {
  label: PropTypes.object.isRequired,
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  reviewContext: PropTypes.object,
  hasError: PropTypes.bool,
  disabled: PropTypes.bool,
};
