import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, ClickAwayListener, Tooltip } from "@mui/material";
import { formatCompactMoney, formatExactMoney } from "./money";

/** Exact per-item money; compact aggregates always expose their exact value. */
export default function MoneyValue({
  value,
  aggregate = false,
  grouping = false,
}) {
  const [open, setOpen] = useState(false);
  const exact = formatExactMoney(value, { grouping });
  useEffect(() => setOpen(false), [value, aggregate, grouping]);

  if (!aggregate || exact === "—") {
    return (
      <Box
        component="span"
        sx={{ fontVariantNumeric: "tabular-nums", overflowWrap: "anywhere" }}
      >
        {exact}
      </Box>
    );
  }

  return (
    <ClickAwayListener onClickAway={() => setOpen(false)}>
      <span>
        <Tooltip
          describeChild
          open={open}
          disableHoverListener
          disableTouchListener
          disableFocusListener
          title={<span>Exact: {exact}</span>}
          componentsProps={{
            tooltip: {
              sx: {
                maxWidth: "min(28rem, calc(100vw - 32px))",
                overflowWrap: "anywhere",
              },
            },
          }}
        >
          <ButtonBase
            type="button"
            aria-label="Show exact amount"
            onFocus={() => setOpen(true)}
            onBlur={() => setOpen(false)}
            onPointerUp={() => setOpen(true)}
            onClick={(event) => {
              event.stopPropagation();
              setOpen(true);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                setOpen(false);
              }
            }}
            sx={{
              font: "inherit",
              color: "inherit",
              fontVariantNumeric: "tabular-nums",
              borderRadius: 0.5,
              minWidth: 0,
              "&.Mui-focusVisible": {
                outline: "2px solid",
                outlineColor: "primary.main",
                outlineOffset: 2,
              },
            }}
          >
            {formatCompactMoney(value)}
          </ButtonBase>
        </Tooltip>
      </span>
    </ClickAwayListener>
  );
}

MoneyValue.propTypes = {
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  aggregate: PropTypes.bool,
  grouping: PropTypes.bool,
};
