import React from "react";
import PropTypes from "prop-types";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";

import { useHubRules } from "../api/queries";

import HubIcon from "./HubIcon";

// The ⓘ beside a count: its definition, read from the rules file (SPEC §6.2,
// §6.8). With no rules loaded it renders nothing rather than invent a text.
export default function CountDefinition({ kind }) {
  const { data: rules } = useHubRules();
  const text = rules?.counts?.[kind];
  if (!text) return null;
  return (
    <Tooltip title={text} arrow enterTouchDelay={0}>
      <IconButton
        size="small"
        aria-label={text}
        data-testid={`hub-count-definition-${kind}`}
        // DS tokens.json icon.default is exposed by dsPalette as action.active.
        sx={{ p: 0.25, color: "action.active" }}
      >
        <HubIcon name="info" size={14} />
      </IconButton>
    </Tooltip>
  );
}

CountDefinition.propTypes = {
  kind: PropTypes.oneOf(["installs", "opens", "likes"]).isRequired,
};
