import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import ModalWrapper from "src/components/ModalWrapper";
import ContentSkeleton from "src/components/content-skeleton";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import {
  useRenewalPreview,
  useRenewReleaseRequest,
} from "src/api/skills/review-production";
import { OperationFeedback, useReviewedOperation } from "./ReleaseRequestPanel";

// Intent: an assigned reviewer sees exactly what changed after a race and
// creates the next immutable packet revision. Nothing is refreshed silently;
// the renewed revision starts pending and needs a new decision.
const show = (value) =>
  value === null || value === undefined ? "None" : JSON.stringify(value);

const REASONS = {
  evidence_expired: "Detailed evidence expired",
  resource_not_found: "Evidence is no longer available",
};

export default function ReleaseRenewalDialog({ open, requestId, onClose }) {
  const preview = useRenewalPreview(requestId, open);
  const renew = useRenewReleaseRequest();
  const operation = useReviewedOperation();
  const [inspected, setInspected] = useState(null);
  const value = preview.data;
  const current =
    preview.isError && preview.error?.code === "review_current";
  const acknowledged = Boolean(value) && inspected === value.fingerprint;
  const close = () => {
    if (operation.busy) return;
    setInspected(null);
    onClose();
  };
  return (
    <ModalWrapper
      open={open}
      onClose={close}
      title="Renew release review"
      subTitle="Inspect every change since the reviewed revision. The previous approval does not carry forward."
      actionBtnTitle="Create renewed revision"
      isValid={acknowledged && !operation.busy && !operation.uncertain}
      isLoading={operation.busy}
      onSubmit={async () => {
        const result = await operation.run(
          (input) => renew.mutateAsync(input),
          {
            requestId,
            revision: value.revision,
            expectedFingerprint: value.fingerprint,
          },
        );
        if (result) close();
      }}
      onCancelBtn={close}
      modalWidth="720px"
    >
      <Stack spacing={1.5} sx={{ minWidth: 0 }}>
        {preview.isLoading && (
          <ContentSkeleton label="Loading changes" rows={3} />
        )}
        {current && (
          <Alert severity="info">
            This revision is still current. Nothing needs renewal.
          </Alert>
        )}
        {preview.isError && !current && (
          <Alert severity="error">
            Could not load the changes. No revision was created. Close and
            check current access.
          </Alert>
        )}
        {value && (
          <>
            <Typography variant="s1">
              Revision {value.revision} becomes revision {value.revision + 1}{" "}
              for the same candidate, transition and target.
            </Typography>
            <Table size="small" aria-label="Changed preconditions">
              <TableHead>
                <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                  <TableCell>Changed item</TableCell>
                  <TableCell>Reviewed</TableCell>
                  <TableCell>Current</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {value.changes.map((row) => (
                  <TableRow
                    key={row.item}
                    hover
                    sx={{ height: ROW_HEIGHT.default }}
                  >
                    <TableCell sx={{ overflowWrap: "anywhere" }}>
                      {row.item}
                    </TableCell>
                    <TableCell sx={{ overflowWrap: "anywhere" }}>
                      {show(row.before)}
                    </TableCell>
                    <TableCell sx={{ overflowWrap: "anywhere" }}>
                      {show(row.after)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {value.removedEvidence.map((row) => (
              <Alert severity="warning" key={`${row.source}:${row.sourceId}`}>
                {REASONS[row.reason] || "Evidence unavailable"}: {row.sourceId}{" "}
                is removed from the renewed packet. It is not replaced.
              </Alert>
            ))}
            <FormControlLabel
              control={
                <Checkbox
                  checked={acknowledged}
                  disabled={operation.busy}
                  onChange={(_, checked) =>
                    setInspected(checked ? value.fingerprint : null)
                  }
                />
              }
              label="I inspected every change. Approval is required again."
            />
          </>
        )}
        <OperationFeedback operation={operation} />
      </Stack>
    </ModalWrapper>
  );
}
ReleaseRenewalDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  requestId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};
