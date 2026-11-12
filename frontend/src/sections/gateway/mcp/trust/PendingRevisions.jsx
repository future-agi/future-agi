import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import RevisionDiff from "./ToolRevisionDiff";

export default function PendingRevisions({ rows, pins, disabled, onChoose }) {
  const groups = rows.reduce(
    (map, row) =>
      map.set(row.server_id, [...(map.get(row.server_id) || []), row]),
    new Map(),
  );
  return [...groups].map(([server, serverRows]) => (
    <Stack
      component="section"
      aria-label={`${server} revisions`}
      key={server}
      spacing={2}
    >
      <Typography component="h4" variant="subtitle2">
        Server: {server}
      </Typography>
      {serverRows.map((row) => (
        <Stack
          component="article"
          aria-label={`${row.server_id} / ${row.tool} revision`}
          key={row.id}
          spacing={1.5}
          sx={{
            p: 2,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            minWidth: 0,
            overflowWrap: "anywhere",
          }}
        >
          <Typography component="h5" variant="subtitle2">
            {row.tool} ·{" "}
            {row.reason === "new" ? "New tool" : "Changed definition"}
          </Typography>
          <Box>
            <Typography
              variant="caption"
              color="text.secondary"
              display="block"
            >
              Accepted revision: {row.previous_revision || "None"}
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              display="block"
            >
              Proposed revision: {row.revision}
            </Typography>
          </Box>
          <RevisionDiff row={row} />
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            <Button
              variant="contained"
              color="primary"
              disabled={disabled || row.status !== "pending"}
              onClick={() => onChoose("approve", row)}
            >
              Approve
            </Button>
            <Button
              variant="outlined"
              color="error"
              disabled={disabled || row.status !== "pending"}
              onClick={() => onChoose("reject", row)}
            >
              Reject
            </Button>
            {typeof row.previous_revision === "string" &&
              /^[a-f0-9]{64}$/.test(row.previous_revision) &&
              row.previous_definition &&
              pins[`${row.server_id}/${row.tool}`] !==
                row.previous_revision && (
                <Button
                  disabled={disabled}
                  onClick={() => onChoose("pin", row)}
                >
                  Pin accepted revision
                </Button>
              )}
          </Stack>
        </Stack>
      ))}
    </Stack>
  ));
}
PendingRevisions.propTypes = {
  rows: PropTypes.array.isRequired,
  pins: PropTypes.object.isRequired,
  disabled: PropTypes.bool.isRequired,
  onChoose: PropTypes.func.isRequired,
};
