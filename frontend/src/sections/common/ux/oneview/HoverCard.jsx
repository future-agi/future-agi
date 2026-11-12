import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ClickAwayListener,
  Paper,
  Popper,
  Stack,
  Typography,
} from "@mui/material";

const CLOSE_DELAY = 150;

/** Focus from the keyboard (a click opens through onClick instead). */
const isKeyboardFocus = (el) => {
  try {
    return el.matches(":focus-visible");
  } catch {
    return true;
  }
};

/**
 * HoverCard — a card one hover away (ONE-VIEW-SPEC §3.5; Term's mechanics
 * generalised). Opens on hover after `delay` ms or on keyboard focus; click
 * (or Enter / Space) pins it; Esc closes and returns focus to the trigger;
 * a 150 ms close delay lets the pointer travel into the card.
 *
 * Props:
 * @param {React.ReactNode} props.trigger  what the user points at
 * @param {React.ReactNode} [props.title]
 * @param {React.ReactNode} [props.children]  body (s2, text.secondary)
 * @param {{label: string, value: React.ReactNode}[]} [props.stats]  a
 *   two-column label/value list (numbers right-aligned, tabular)
 * @param {React.ReactNode} [props.actions]  links / buttons row at the end
 * @param {string} [props.placement="bottom-start"]
 * @param {number} [props.delay=300]
 * @param {number} [props.maxWidth=320]
 * @param {"span"|"div"} [props.component="span"]  trigger element
 * @param {object} [props.triggerSx]  trigger styles (Term's underline)
 * @param {object} [props.triggerProps]  extra trigger attributes
 *   (`data-term`, `aria-label`)
 * @param {object} [props.cardProps]  extra card attributes
 * @param {boolean} [props.disabled]  render the trigger only
 */
export default function HoverCard({
  trigger,
  title,
  children,
  stats,
  actions,
  placement = "bottom-start",
  delay = 300,
  maxWidth = 320,
  component = "span",
  triggerSx,
  triggerProps,
  cardProps,
  disabled = false,
}) {
  const [anchor, setAnchor] = useState(null);
  const [pinned, setPinned] = useState(false);
  const openTimer = useRef(null);
  const closeTimer = useRef(null);
  const triggerRef = useRef(null);
  const cardRef = useRef(null);
  const id = useId();
  const open = Boolean(anchor);

  const clear = () => {
    clearTimeout(openTimer.current);
    clearTimeout(closeTimer.current);
  };
  useEffect(() => clear, []);

  const show = useCallback(() => {
    clear();
    setAnchor(triggerRef.current);
  }, []);
  const hide = useCallback(() => {
    clear();
    setAnchor(null);
    setPinned(false);
  }, []);

  if (disabled) return <>{trigger}</>;

  const onMouseEnter = () => {
    clearTimeout(closeTimer.current);
    if (!open) openTimer.current = setTimeout(show, delay);
  };
  const onMouseLeave = () => {
    clearTimeout(openTimer.current);
    if (!pinned) closeTimer.current = setTimeout(hide, CLOSE_DELAY);
  };
  const togglePin = () => {
    if (open && pinned) hide();
    else {
      show();
      setPinned(true);
    }
  };
  const onKeyDown = (e) => {
    if (e.key === "Escape" && open) {
      e.stopPropagation();
      e.preventDefault();
      hide();
      triggerRef.current?.focus();
    } else if (
      (e.key === "Enter" || e.key === " ") &&
      e.currentTarget === triggerRef.current
    ) {
      e.preventDefault();
      togglePin();
    }
  };

  return (
    <>
      <Box
        component={component}
        ref={triggerRef}
        role="button"
        tabIndex={0}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
        onFocus={(e) => {
          if (e.target === triggerRef.current && isKeyboardFocus(e.target))
            show();
        }}
        onBlur={(e) => {
          if (!pinned && !cardRef.current?.contains(e.relatedTarget)) hide();
        }}
        onClick={(e) => {
          e.stopPropagation();
          togglePin();
        }}
        onKeyDown={onKeyDown}
        {...triggerProps}
        sx={{
          borderRadius: 0.25,
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 1,
          },
          ...triggerSx,
        }}
      >
        {trigger}
      </Box>
      <Popper
        open={open}
        anchorEl={anchor}
        placement={placement}
        sx={{ zIndex: 1600 }}
        modifiers={[
          { name: "flip", enabled: true },
          { name: "preventOverflow", options: { padding: 8 } },
          { name: "offset", options: { offset: [0, 6] } },
        ]}
      >
        <ClickAwayListener onClickAway={() => pinned && hide()}>
          <Paper
            ref={cardRef}
            id={id}
            role="tooltip"
            elevation={6}
            onMouseEnter={() => clearTimeout(closeTimer.current)}
            onMouseLeave={onMouseLeave}
            onKeyDown={onKeyDown}
            {...cardProps}
            sx={{
              p: 1.5,
              maxWidth,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            {title && (
              <Typography
                typography="s2"
                fontWeight="fontWeightSemiBold"
                color="text.primary"
                sx={{ mb: 0.5 }}
              >
                {title}
              </Typography>
            )}
            {children &&
              (typeof children === "string" ? (
                <Typography typography="s2" color="text.secondary">
                  {children}
                </Typography>
              ) : (
                children
              ))}
            {stats?.length > 0 && (
              <Box
                component="dl"
                sx={{
                  display: "grid",
                  gridTemplateColumns: "auto auto",
                  columnGap: 2,
                  rowGap: 0.5,
                  m: 0,
                  mt: title || children ? 1 : 0,
                }}
              >
                {stats.map((s) => (
                  <React.Fragment key={s.label}>
                    <Typography
                      component="dt"
                      typography="s2"
                      color="text.secondary"
                    >
                      {s.label}
                    </Typography>
                    <Typography
                      component="dd"
                      typography="s2"
                      color="text.primary"
                      sx={{
                        m: 0,
                        textAlign: "right",
                        fontVariantNumeric: "tabular-nums",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {s.value}
                    </Typography>
                  </React.Fragment>
                ))}
              </Box>
            )}
            {actions && (
              <Stack
                direction="row"
                gap={1.5}
                alignItems="center"
                sx={{ mt: 1 }}
              >
                {actions}
              </Stack>
            )}
          </Paper>
        </ClickAwayListener>
      </Popper>
    </>
  );
}

HoverCard.propTypes = {
  trigger: PropTypes.node,
  title: PropTypes.node,
  children: PropTypes.node,
  stats: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.string, value: PropTypes.node }),
  ),
  actions: PropTypes.node,
  placement: PropTypes.string,
  delay: PropTypes.number,
  maxWidth: PropTypes.number,
  component: PropTypes.oneOf(["span", "div"]),
  triggerSx: PropTypes.object,
  triggerProps: PropTypes.object,
  cardProps: PropTypes.object,
  disabled: PropTypes.bool,
};

export { HoverCard };
