import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { paths } from "src/routes/paths";
import { EVIDENCE_COPY } from "./evidence.constants";
import ImportTranscriptDialog from "./ImportTranscriptDialog";

// The Scenarios tab's way in: Observe evidence and dataset rows are picked
// where they live, and their import opens back here for review. A transcript
// the person has is added here, and its review opens at once.
export default function ImportEvidenceButton({ envId, onImported }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
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
          <>
            <Button
              size="small"
              variant="outlined"
              onClick={() => {
                setOpen(false);
                setAdding(true);
              }}
            >
              {EVIDENCE_COPY.addTranscript}
            </Button>
            <Button
              size="small"
              variant="outlined"
              onClick={() => navigate(paths.dashboard.develop)}
            >
              {EVIDENCE_COPY.openDataset}
            </Button>
            <Button
              variant="contained"
              color="primary"
              onClick={() => navigate(paths.dashboard.observe)}
            >
              {EVIDENCE_COPY.openObserve}
            </Button>
          </>
        }
      />
      {adding ? (
        <ImportTranscriptDialog
          open
          onClose={() => setAdding(false)}
          envId={envId}
          onImported={onImported}
        />
      ) : null}
    </>
  );
}

ImportEvidenceButton.propTypes = {
  envId: PropTypes.string.isRequired,
  onImported: PropTypes.func.isRequired,
};
