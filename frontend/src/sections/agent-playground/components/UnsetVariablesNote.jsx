import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";

function joinNames(names) {
  return names.map((name, index) => {
    let separator = "";
    if (index > 0) separator = index === names.length - 1 ? " and " : ", ";
    return (
      <React.Fragment key={name}>
        {separator}
        <code>{`{{${name}}}`}</code>
      </React.Fragment>
    );
  });
}

/**
 * Says in words which prompt variables have no value yet. Saving the prompt
 * makes each one an input of this agent, and Run asks for its value (the
 * Variables drawer) unless a connected node supplies it. The editor leaves
 * these names plain, so this text carries the state, not colour.
 */
export default function UnsetVariablesNote({ names }) {
  if (!names?.length) return null;
  const many = names.length > 1;

  return (
    <Typography
      variant="caption"
      component="p"
      role="status"
      color="text.secondary"
      sx={{ m: 0, "& code": { fontSize: "0.75rem", color: "text.primary" } }}
    >
      {joinNames(names)}
      {many
        ? " are inputs to this agent. You'll be asked for their values when you run."
        : " is an input to this agent. You'll be asked for its value when you run."}{" "}
      To use another node&apos;s output instead, connect that node to this one.
    </Typography>
  );
}

UnsetVariablesNote.propTypes = {
  names: PropTypes.arrayOf(PropTypes.string),
};
