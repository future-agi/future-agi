import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import DeployIcon from "../components/DeployIcon";
import { downloadSample, sampleBundle } from "../lib/sampleProject";

/**
 * For someone with no project yet: the sample support-triage project
 * (lib/sampleProject.js), saved as a .tar.gz that deploys as it is, or used
 * here straight away as the bundle to deploy. Either way the bytes are the
 * same ones the page's own checks read.
 */
export default function SampleProject({ onUse, disabled }) {
  const [working, setWorking] = useState(null);
  const [problem, setProblem] = useState(null);

  const run = async (kind, action) => {
    setWorking(kind);
    setProblem(null);
    try {
      await action();
    } catch {
      setProblem(
        kind === "download"
          ? "The sample could not be saved. Try again."
          : "The sample could not be read. Try again.",
      );
    } finally {
      setWorking(null);
    }
  };

  return (
    <Box
      data-testid="sample-project"
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
        px: 2,
        py: 1.5,
        borderRadius: 1,
        bgcolor: "background.neutral",
      }}
    >
      <Typography typography="s2" color="text.secondary">
        No project yet? Start from the sample: a support-triage workflow in
        TypeScript that needs no model key.
      </Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
        <Button
          size="small"
          variant="text"
          disabled={disabled || working !== null}
          onClick={() => run("download", () => downloadSample())}
          startIcon={<DeployIcon name="download" size={16} />}
          data-testid="sample-download"
        >
          {working === "download" ? "Saving…" : "Download the sample"}
        </Button>
        <Button
          size="small"
          variant="text"
          disabled={disabled || working !== null}
          onClick={() =>
            run("use", async () => {
              onUse(await sampleBundle());
            })
          }
          startIcon={<DeployIcon name="archive" size={16} />}
          data-testid="sample-use"
        >
          {working === "use" ? "Reading…" : "Use the sample here"}
        </Button>
      </Box>
      {problem && (
        <Typography
          typography="s2"
          color="error.main"
          data-testid="sample-problem"
        >
          {problem}
        </Typography>
      )}
    </Box>
  );
}

SampleProject.propTypes = {
  onUse: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
