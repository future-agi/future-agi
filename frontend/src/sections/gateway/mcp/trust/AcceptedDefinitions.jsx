import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { GATEWAY_MONO } from "../../utils/fonts";

export default function AcceptedDefinitions({ rows, pins, disabled, onPin }) {
  const accepted = rows.filter((row) => row.status === "approved");
  return (
    <Stack spacing={2}>
      <Typography component="h3" variant="subtitle2">
        Reviewed definitions
      </Typography>
      <Typography variant="body2" color="text.secondary">
        Re-pin a previously accepted definition. Pins without a recorded
        definition can be approved through pending review instead.
      </Typography>
      {accepted.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No recorded accepted definitions.
        </Typography>
      )}
      {accepted.map((row) => (
        <Stack
          component="article"
          aria-label={`${row.server_id} / ${row.tool} accepted definition`}
          key={row.id}
          spacing={1}
          sx={{
            p: 2,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            minWidth: 0,
            overflowWrap: "anywhere",
          }}
        >
          <Typography variant="subtitle2">
            {row.server_id} / {row.tool}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Accepted revision: {row.revision}
          </Typography>
          <Box component="details">
            <Typography component="summary" variant="body2">
              Accepted definition
            </Typography>
            <Typography
              component="pre"
              variant="body2"
              sx={{
                fontFamily: GATEWAY_MONO,
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
              }}
            >
              {JSON.stringify(row.definition, null, 2)}
            </Typography>
          </Box>
          <Box>
            <Button
              disabled={
                disabled ||
                pins[`${row.server_id}/${row.tool}`] === row.revision ||
                !/^[a-f0-9]{64}$/.test(row.revision)
              }
              onClick={() => onPin({ ...row, previous_revision: row.revision })}
            >
              Pin
            </Button>
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}
AcceptedDefinitions.propTypes = {
  rows: PropTypes.array.isRequired,
  pins: PropTypes.object.isRequired,
  disabled: PropTypes.bool.isRequired,
  onPin: PropTypes.func.isRequired,
};
