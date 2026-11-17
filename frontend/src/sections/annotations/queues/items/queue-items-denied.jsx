// W4 §9 (M6, the Annotations queue integration): when a queue's items read is
// refused because the caller can't view a dataset the queue holds ("No
// access"), the page says so with who can grant it and Request view access,
// instead of "No items in this queue". Renders nothing for other outcomes.
import PropTypes from "prop-types";
import React from "react";
import { Box } from "@mui/material";
import AccessDeniedNote from "src/sections/common/Access/AccessDeniedNote";
import { viewDenialOf } from "src/sections/common/Access/accessModel";

export default function QueueItemsDenied({ error }) {
  const denial = viewDenialOf(error);
  if (!denial) return null;
  return (
    <Box sx={{ p: 2, maxWidth: 720 }} data-testid="queue-items-denied">
      <AccessDeniedNote denial={denial} />
    </Box>
  );
}

QueueItemsDenied.propTypes = {
  error: PropTypes.object,
};
