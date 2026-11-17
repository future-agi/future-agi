import React from "react";
import { Alert, Button, Stack, Typography } from "@mui/material";
import { PROPOSED_HF_IMPORT_COPY } from "src/sections/develop/importAvailabilityCopy";

// Explain the missing dependency without mounting the catalog or import form.
export default function HuggingFaceUnavailable() {
  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      <Typography component="h1" variant="h4">
        {PROPOSED_HF_IMPORT_COPY.title}
      </Typography>
      <Alert severity="warning" id="hf-import-unavailable">
        {PROPOSED_HF_IMPORT_COPY.notice}
      </Alert>
      <Stack direction="row" spacing={2}>
        <Button
          disabled
          variant="contained"
          aria-describedby="hf-import-unavailable"
        >
          {PROPOSED_HF_IMPORT_COPY.createAction}
        </Button>
        <Button
          disabled
          variant="outlined"
          aria-describedby="hf-import-unavailable"
        >
          {PROPOSED_HF_IMPORT_COPY.addAction}
        </Button>
      </Stack>
    </Stack>
  );
}
