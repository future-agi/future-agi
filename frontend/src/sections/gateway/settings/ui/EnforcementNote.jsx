import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { GatewayStatusChip } from "../../components/frame";
import {
  NOT_ENFORCED_LABEL,
  enforcementFor,
  hasEnforcement,
} from "../enforcement";

// One line per setting this gateway stores but does not apply: the neutral
// "Not enforced on this gateway" chip and why.
function Note({ id }) {
  const entry = enforcementFor(id);
  return (
    <Box
      data-testid={`enforcement-${id}`}
      data-enforcement-kind={entry.kind}
      sx={{
        display: "flex",
        flexDirection: { xs: "column", sm: "row" },
        alignItems: { xs: "flex-start", sm: "center" },
        gap: 1,
        minWidth: 0,
      }}
    >
      <Box sx={{ flexShrink: 0 }}>
        <GatewayStatusChip tone="neutral" label={NOT_ENFORCED_LABEL} />
      </Box>
      <Typography typography="s2" color="text.secondary" sx={{ minWidth: 0 }}>
        {entry.reason}
      </Typography>
    </Box>
  );
}

Note.propTypes = { id: PropTypes.string.isRequired };

// An id with no entry in enforcement.js is a setting the gateway applies, so
// it gets no note.
export default function EnforcementNote({ id, ids, sx }) {
  const list = (ids || (id ? [id] : [])).filter(hasEnforcement);
  if (!list.length) return null;
  return (
    <Stack spacing={1} sx={sx}>
      {list.map((one) => (
        <Note key={one} id={one} />
      ))}
    </Stack>
  );
}

EnforcementNote.propTypes = {
  id: PropTypes.string,
  ids: PropTypes.arrayOf(PropTypes.string),
  sx: PropTypes.object,
};
