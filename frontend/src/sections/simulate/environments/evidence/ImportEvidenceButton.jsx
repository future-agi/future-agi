import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { paths } from "src/routes/paths";
import { EVIDENCE_COPY } from "./evidence.constants";

// The Scenarios tab's way in: evidence is picked where it lives, in Observe,
// and its import opens back here for review.
export default function ImportEvidenceButton() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        startIcon={<Iconify icon="solar:import-linear" width={16} />}
        onClick={() => setOpen(true)}
      >
        {EVIDENCE_COPY.importButton}
      </Button>
      <ConfirmDialog
        open={open}
        onClose={() => setOpen(false)}
        title={EVIDENCE_COPY.importHintTitle}
        content={EVIDENCE_COPY.importHint}
        action={
          <Button
            variant="contained"
            color="primary"
            onClick={() => navigate(paths.dashboard.observe)}
          >
            {EVIDENCE_COPY.openObserve}
          </Button>
        }
      />
    </>
  );
}
