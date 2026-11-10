import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import { fieldFixOf } from "./fixKinds";

/*
  A Ready to run fix made in place, under its row: the run plan keeps its
  agent, scenarios and attempts, and the row re-checks where it is.
*/
export default function InlineFix({ kind, row, kit, onClose }) {
  const field = kind === "field" ? fieldFixOf(row, kit) : null;
  const [value, setValue] = useState(field?.value || "");
  const would = row.fix?.would;

  const actions = (label, run, disabled = false) => (
    <Stack direction="row" spacing={1} justifyContent="flex-end">
      <Button size="small" color="inherit" onClick={onClose}>
        Cancel
      </Button>
      <Button
        size="small"
        variant="contained"
        disabled={disabled}
        onClick={() => {
          run();
          onClose();
        }}
      >
        {label}
      </Button>
    </Stack>
  );

  return (
    <Stack
      spacing={1}
      sx={{
        px: 1.5,
        pb: 1.25,
        pt: 0.25,
        pl: 3.5,
        typography: "s3",
        color: "text.secondary",
      }}
    >
      {kind === "field" && (
        <>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <Typography sx={{ typography: "s3", width: 120, flexShrink: 0 }}>
              {field.label}
            </Typography>
            <TextField
              size="small"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              inputProps={{ "aria-label": field.label }}
              sx={{ flex: 1, "& input": { typography: "s2", py: 0.75 } }}
            />
          </Stack>
          {row.suggestion?.why && <Box>{row.suggestion.why}</Box>}
          {would && <Box>{`After the fix: ${would}`}</Box>}
          {actions(
            "Apply and re-check",
            () => kit.applyRepair(row.fixId, value),
            !value.trim(),
          )}
        </>
      )}
      {kind === "tools" && (
        <>
          <Box>
            {`${row.fixLabel || "Point the tools at the test world"}. During runs its calls go to the test world, not production. ${would ? `After the fix: ${would}` : ""}`}
          </Box>
          {actions("Point at the test world and re-check", () =>
            kit.applyRepair(row.fixId),
          )}
        </>
      )}
      {kind === "release" && (
        <>
          <Box>
            {`${row.detail} They run here, on this environment — no cost to you.`}
          </Box>
          {actions(
            kit.releaseRunning ? "Running checks…" : "Run release checks",
            kit.runReleaseChecks,
            kit.releaseRunning,
          )}
        </>
      )}
      {kind === "rebuild" && (
        <>
          <Box>{`${row.detail} ${would || ""}`}</Box>
          {actions("Rebuild environment", kit.rebuild)}
        </>
      )}
    </Stack>
  );
}
InlineFix.propTypes = {
  kind: PropTypes.oneOf(["field", "tools", "release", "rebuild"]).isRequired,
  row: PropTypes.object.isRequired,
  /* { spec, agent, applyRepair(fixId, value), runReleaseChecks,
     releaseRunning, rebuild } */
  kit: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};
