/*
  Keyboard access for anything that isn't a real button but acts like one — a
  clickable row, a header pill, a text link. Spread it on the element:

    <Stack {...clickable(open)} />
    <Box {...clickable(() => toggle(id), { role: "checkbox", checked: on })} />
    <Typography {...clickable(go, { role: "link", label: "Open run" })} />

  It adds the role, puts the element in the tab order, and runs the same
  handler on Enter and Space (Space only for buttons, checkboxes and radios,
  the way native controls behave). With no handler it returns nothing, so an
  inert element stays out of the tab order and isn't announced as a control.
*/
/* A visible focus ring for those elements — keyboard focus only, so a mouse
   click doesn't leave an outline behind. Spread into sx. */
export const focusRing = {
  "&:focus-visible": {
    outline: "2px solid",
    outlineColor: "primary.main",
    outlineOffset: "2px",
    borderRadius: "4px",
  },
};

export const clickable = (
  onClick,
  { role = "button", label, checked, pressed, expanded, disabled } = {},
) => {
  if (!onClick || disabled) return disabled ? { "aria-disabled": true } : {};
  const space = role !== "link";
  return {
    role,
    tabIndex: 0,
    onClick,
    onKeyDown: (e) => {
      if (e.target !== e.currentTarget) return;
      if (e.key === "Enter" || (space && e.key === " ")) {
        e.preventDefault();
        onClick(e);
      }
    },
    ...(label && { "aria-label": label }),
    ...(checked !== undefined && { "aria-checked": !!checked }),
    ...(pressed !== undefined && { "aria-pressed": !!pressed }),
    ...(expanded !== undefined && { "aria-expanded": !!expanded }),
  };
};
