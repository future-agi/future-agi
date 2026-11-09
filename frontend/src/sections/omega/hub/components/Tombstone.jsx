import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { HUB_ROOT } from "../constants";
import { formatDate } from "../lib/format";

import HubIcon from "./HubIcon";

const CATEGORY_TEXT = {
  "security issue": "Removed by Future AGI: security issue.",
  "licence issue": "Removed by Future AGI: licence issue.",
  "breaks Hub rules": "Removed by Future AGI: it breaks the Hub rules.",
  "publisher's request": "Removed at the publisher's request.",
};

// A removed or unlisted entry keeps a page that says what happened (SPEC
// §3.3), so people holding a lock can find out. Nothing else is shown. The
// line about installing, and its `omega hub outdated` hint, show only once a
// customer can install a CLI that has it (OMEGA_PART HUB_CLI): until then no
// version was ever installable, so the line would claim a loss nobody had.
export default function Tombstone({ tombstone }) {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  const text =
    CATEGORY_TEXT[tombstone?.category] || "This entry is no longer on the Hub.";
  return (
    <Box
      data-testid="hub-tombstone"
      sx={{
        maxWidth: 560,
        mx: "auto",
        my: { xs: 4, md: 8 },
        p: 3,
        textAlign: "center",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        display: "flex",
        flexDirection: "column",
        gap: 1.25,
        alignItems: "center",
      }}
    >
      <Box sx={{ color: "text.disabled" }}>
        <HubIcon name="ban" size={28} />
      </Box>
      <Typography typography="m3" fontWeight={600}>
        {tombstone?.ref || "Removed entry"}
      </Typography>
      <Typography typography="s1" color="text.secondary">
        {text}
      </Typography>
      {tombstone?.at && (
        <Typography typography="s2" color="text.tertiary">
          {formatDate(tombstone.at)}
        </Typography>
      )}
      {cliReleased && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="hub-tombstone-installs"
        >
          Its versions can no longer be installed.{" "}
          <code>omega hub outdated</code> reports it in projects that use it.
        </Typography>
      )}
      <Button
        component={RouterLink}
        href={HUB_ROOT}
        variant="outlined"
        size="small"
      >
        Back to the Hub
      </Button>
    </Box>
  );
}

Tombstone.propTypes = { tombstone: PropTypes.object };
