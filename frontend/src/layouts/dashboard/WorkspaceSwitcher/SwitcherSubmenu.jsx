import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Popper } from "@mui/material";

// Keep submenus inside the parent popover's focus trap. Hover still previews
// choices; explicit activation moves focus in and closing returns to its row.
export default function SwitcherSubmenu({ open, anchorEl, setOpen, autoFocus, label, children }) {
  const panel = useRef(null);
  useEffect(() => {
    if (open && autoFocus) {
      const target = panel.current?.querySelector("button:not(:disabled)") || panel.current;
      target?.focus();
    }
  }, [open, autoFocus]);

  const changeOpen = (value) => {
    if (!value && panel.current?.contains(document.activeElement)) anchorEl?.focus();
    setOpen(value);
  };

  return (
    <Popper
      open={open}
      anchorEl={anchorEl}
      disablePortal
      placement="right-start"
      modifiers={[{ name: "offset", options: { offset: [0, 8] } }]}
      style={{ zIndex: 1301 }}
    >
      <Box
        ref={panel}
        role="group"
        aria-label={label}
        tabIndex={-1}
        onBlur={(event) => {
          if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) setOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" || event.key === "ArrowLeft") {
            event.preventDefault();
            event.stopPropagation();
            anchorEl?.focus();
            setOpen(false);
          } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            const buttons = Array.from(event.currentTarget.querySelectorAll("button:not(:disabled)"));
            const index = buttons.indexOf(document.activeElement);
            const step = event.key === "ArrowDown" ? 1 : -1;
            buttons[(index + step + buttons.length) % buttons.length]?.focus();
          }
        }}
      >
        {children(changeOpen)}
      </Box>
    </Popper>
  );
}

SwitcherSubmenu.propTypes = {
  open: PropTypes.bool,
  anchorEl: PropTypes.object,
  setOpen: PropTypes.func.isRequired,
  autoFocus: PropTypes.bool,
  label: PropTypes.string.isRequired,
  children: PropTypes.func.isRequired,
};
