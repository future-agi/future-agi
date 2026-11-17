import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

import LogViewer from "../components/LogViewer";
import { useLogStream } from "../lib/useLogStream";

/**
 * Runtime: what the serving container prints, followed live (EE reads it
 * from Docker; it is not stored). Build: one revision's build and activation
 * log, replayed and followed while an operation is still acting on it.
 */
export default function LogsTab({ deployment }) {
  const [source, setSource] = useState(
    deployment.liveRevision ? "runtime" : "build",
  );
  const latest =
    deployment.operation?.revision ?? deployment.revisions[0]?.revision;
  const [revision, setRevision] = useState(latest);
  const stream = useLogStream(deployment.id, {
    source,
    revision: source === "build" ? revision : undefined,
  });

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          flexWrap: "wrap",
        }}
      >
        <ToggleButtonGroup
          exclusive
          size="small"
          value={source}
          onChange={(_, value) => value && setSource(value)}
          aria-label="Log source"
          sx={{ "& .MuiToggleButton-root": { px: 1.5, textTransform: "none" } }}
        >
          <ToggleButton value="runtime" data-testid="logs-runtime">
            Runtime
          </ToggleButton>
          <ToggleButton value="build" data-testid="logs-build">
            Build
          </ToggleButton>
        </ToggleButtonGroup>
        {source === "build" && (
          <TextField
            select
            size="small"
            value={revision ?? ""}
            onChange={(event) => setRevision(Number(event.target.value))}
            inputProps={{
              "aria-label": "Revision",
              "data-testid": "logs-revision",
            }}
            sx={{ minWidth: 150 }}
          >
            {deployment.revisions.map((r) => (
              <MenuItem key={r.revision} value={r.revision}>
                Revision {r.revision}
              </MenuItem>
            ))}
          </TextField>
        )}
        <Typography typography="s2" color="text.secondary">
          {source === "runtime"
            ? deployment.liveRevision
              ? `Revision ${deployment.liveRevision}, as it runs. Not stored.`
              : "Nothing is running."
            : "Build output and activation for this revision. Setup details are hidden."}
        </Typography>
      </Box>
      <LogViewer
        key={`${source}-${revision}`}
        stream={stream}
        height={{ xs: 380, sm: 480 }}
        testId={`${source}-log`}
        showRevision={source === "runtime"}
        source={source}
        emptyText={
          source === "runtime"
            ? "The server has printed nothing yet."
            : "This revision has no log lines."
        }
      />
    </Box>
  );
}

LogsTab.propTypes = { deployment: PropTypes.object.isRequired };
