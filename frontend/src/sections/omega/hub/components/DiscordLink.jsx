import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";

import { DISCORD_URL } from "../constants";

import HubIcon from "./HubIcon";

// The Hub's only community channel (SPEC §2.2): talk happens on Discord, not
// on the page. One server invite for every entry.
export default function DiscordLink({ variant = "text", fullWidth, size = "small" }) {
  return (
    <Button
      component="a"
      href={DISCORD_URL}
      target="_blank"
      rel="noopener noreferrer"
      variant={variant}
      size={size}
      fullWidth={fullWidth}
      color="inherit"
      data-testid="hub-discord-link"
      startIcon={<HubIcon name="discord" size={16} />}
      endIcon={<HubIcon name="external" size={12} />}
      sx={{ justifyContent: fullWidth ? "flex-start" : "center", color: "text.secondary" }}
    >
      Discuss on Discord
    </Button>
  );
}

DiscordLink.propTypes = {
  variant: PropTypes.string,
  fullWidth: PropTypes.bool,
  size: PropTypes.string,
};
