import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Link, Stack, Tooltip, Typography } from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import { useSnackbar } from "notistack";
import { ConfirmDialog } from "src/components/custom-dialog";
import { fToNow } from "src/utils/format-time";
import {
  useDiscardEvidenceImport,
  useEvidenceImports,
} from "src/api/simulate-environments/evidenceImports";
import { EVIDENCE_COPY, errorText } from "./evidence.constants";

// The environment's imports a person can still finish, kept to pick up later:
// Resume opens one's review where it stopped, Discard drops what was never
// added. An import with blocked cases stays until discarded, so its reasons
// stay readable. Shown only while there is one.
export default function DraftImports({ envId, onResume }) {
  const { data } = useEvidenceImports(envId);
  const { enqueueSnackbar } = useSnackbar();
  const discard = useDiscardEvidenceImport(envId);
  const [asked, setAsked] = useState(null);
  const items = data ?? [];
  if (!items.length) return null;

  // A refused discard says why in a toast; the confirm stays open to retry.
  const onDiscard = () =>
    discard.mutate(asked.id, {
      onSuccess: () => setAsked(null),
      onError: (error) =>
        enqueueSnackbar(errorText(error), { variant: "error" }),
    });

  return (
    <Box
      sx={{ px: 2, py: 1, borderBottom: 1, borderColor: "divider" }}
      data-testid="draft-imports"
    >
      <Typography sx={{ typography: "s1", fontWeight: 500 }}>
        {EVIDENCE_COPY.draftImports}
      </Typography>
      {items.map((item) => (
        <Stack
          key={item.id}
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ py: 0.25 }}
          data-testid={`draft-import-${item.id}`}
        >
          <Typography
            noWrap
            sx={{
              typography: "s1",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {[
              EVIDENCE_COPY.importSource(item),
              EVIDENCE_COPY.importStatus[item.status],
              EVIDENCE_COPY.imported(fToNow(item.created_at)),
            ].join(" · ")}
          </Typography>
          <Link
            component="button"
            sx={{ typography: "s1" }}
            onClick={() => onResume(item.id)}
          >
            {EVIDENCE_COPY.resume}
          </Link>
          <Tooltip title={item.can_discard ? "" : EVIDENCE_COPY.discardBusy}>
            <span>
              {/* Plain: red appears only on the confirm's button. */}
              <Link
                component="button"
                sx={{ typography: "s1" }}
                color="text.secondary"
                disabled={!item.can_discard}
                onClick={() => {
                  discard.reset();
                  setAsked(item);
                }}
              >
                {EVIDENCE_COPY.discard}
              </Link>
            </span>
          </Tooltip>
        </Stack>
      ))}
      {asked ? (
        <ConfirmDialog
          open
          onClose={() => setAsked(null)}
          title={EVIDENCE_COPY.discardTitle(asked)}
          content={EVIDENCE_COPY.discardBody(asked)}
          action={
            <LoadingButton
              variant="contained"
              color="error"
              loading={discard.isPending}
              onClick={onDiscard}
            >
              {EVIDENCE_COPY.discard}
            </LoadingButton>
          }
        />
      ) : null}
    </Box>
  );
}

DraftImports.propTypes = {
  envId: PropTypes.string.isRequired,
  onResume: PropTypes.func.isRequired,
};
