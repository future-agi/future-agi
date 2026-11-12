import React, { useId } from "react";
import { Alert, Button, Stack } from "@mui/material";
import { CLASSIC_EXPERIMENT_COPY } from "src/launch/classicExperimentCopy";

// J09: the legacy summary was replaced by the unserved DSX Results analysis.
// Keep its destination visible without mounting that analysis or enabling writes.
export default function ClassicSummaryUnavailable() {
  const noticeId = useId();
  return (
    <Stack spacing={1.5} sx={{ p: 2, alignItems: "flex-start" }}>
      <Alert severity="info" id={noticeId}>
        {CLASSIC_EXPERIMENT_COPY.summaryUnavailable}
      </Alert>
      <Button disabled variant="outlined" aria-describedby={noticeId}>
        {CLASSIC_EXPERIMENT_COPY.chooseWinner}
      </Button>
    </Stack>
  );
}
