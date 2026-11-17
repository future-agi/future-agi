import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import PropTypes from "prop-types";
import React from "react";
import EmptyContent from "src/components/empty-content";

// Called as a function by AG Grid overlay callers: NoRowsOverlay("No x found").
// An optional description and actions ({ label, onClick } or { label, href })
// say why the result is empty and offer a next step. An optional wrap(state)
// gets that state and returns the element to show, for a caller that can say
// more after a read of its own (the Observe list's out-of-window state).
const NoRowsOverlay = (title, { description, actions, wrap } = {}) => {
  const action = actions?.length ? (
    // AG Grid's overlay wrapper is pointer-events: none; the buttons need
    // their clicks back.
    <Stack
      direction="row"
      spacing={1}
      justifyContent="center"
      style={{ pointerEvents: "auto" }}
    >
      {actions.map(({ label, onClick, href }) => (
        <Button
          key={label}
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onClick}
          {...(href
            ? { href, target: "_blank", rel: "noopener noreferrer" }
            : {})}
        >
          {label}
        </Button>
      ))}
    </Stack>
  ) : undefined;
  const state = (
    <EmptyContent title={title} description={description} action={action} />
  );
  return wrap ? wrap(state) : state;
};

NoRowsOverlay.propTypes = {
  title: PropTypes.node,
};

export default NoRowsOverlay;
