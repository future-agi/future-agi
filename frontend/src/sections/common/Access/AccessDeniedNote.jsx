// A server denial on screen (W4-5): the sentence names the missing
// permission and who can grant it, with "Request <perm> access" when the
// permission is one a person can ask for. Fed the 403 body the guard sends.
import { Box, Button, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useRequestDatasetAccess } from "src/api/develop/dataset-access";
import {
  PERM_ORDER,
  REQUESTED_LINE,
  denialLine,
  requestLabel,
} from "./accessModel";

export default function AccessDeniedNote({ denial }) {
  const ask = useRequestDatasetAccess(denial?.dataset_id);
  const line = denialLine(denial);
  if (!line) return null;
  const perm = denial.missing_permission;
  const askable = Boolean(denial.dataset_id) && PERM_ORDER.includes(perm);
  return (
    <Box
      role="status"
      data-testid="access-denied-note"
      sx={{
        p: 1.25,
        borderRadius: 1,
        bgcolor: "action.hover",
        display: "flex",
        gap: 1,
        alignItems: "center",
      }}
    >
      <Typography variant="s2" sx={{ flex: 1 }}>
        {line}
      </Typography>
      {askable &&
        (ask.isSuccess || denial.requested ? (
          <Typography variant="s3" color="text.secondary">
            {REQUESTED_LINE}
          </Typography>
        ) : (
          <Button
            size="small"
            variant="outlined"
            disabled={ask.isPending}
            onClick={() => ask.mutate(perm)}
          >
            {requestLabel(perm)}
          </Button>
        ))}
      {ask.isError && (
        <Typography variant="s3" color="error.main" role="alert">
          {ask.error?.error ||
            ask.error?.result ||
            "Couldn't send the request."}
        </Typography>
      )}
    </Box>
  );
}

AccessDeniedNote.propTypes = {
  denial: PropTypes.shape({
    dataset_id: PropTypes.string,
    missing_permission: PropTypes.string,
    error: PropTypes.string,
    requested: PropTypes.bool,
    ask: PropTypes.array,
  }),
};
