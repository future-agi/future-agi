import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Alert, Box, Button, Divider, Stack, Typography } from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import { useSnackbar } from "notistack";
import SideDrawer from "../components/SideDrawer";
import EvidenceCaseCard from "./EvidenceCaseCard";
import { EVIDENCE_COPY, errorText } from "./evidence.constants";
import {
  isEvidenceCaseDrafted,
  isEvidenceCaseWaiting,
  useAddEvidenceCases,
  useDraftEvidenceCases,
  useEvidenceImport,
} from "src/api/simulate-environments/evidenceImports";
import { useDrafts } from "src/api/simulate-environments/versions";

function Section({ title, children }) {
  return (
    <Box sx={{ mt: 2.5 }}>
      <Typography variant="overline" color="text.secondary">
        {title}
      </Typography>
      <Stack spacing={1.25} sx={{ mt: 0.75 }}>
        {children}
      </Stack>
    </Box>
  );
}

Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

// The import review: what each case's source holds and lacks and what
// redaction replaces; the starting state no source records; the builder's
// drafts; and adding them to the suite. Nothing runs from here.
export default function EvidenceReviewDrawer({
  envId,
  importId,
  open,
  onClose,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const { data, error, isLoading } = useEvidenceImport(envId, importId);
  const { data: drafts } = useDrafts(envId, { enabled: open });
  const draft = useDraftEvidenceCases(envId, importId);
  const add = useAddEvidenceCases(envId, importId);
  const [shows, setShows] = useState({});
  // Cases this review added beside other unsaved edits: nothing is live until the
  // draft bar's Save, so the notice stays after the add.
  const [addedBeside, setAddedBeside] = useState(0);

  const cases = useMemo(() => data?.cases ?? [], [data]);
  const waiting = cases.filter(isEvidenceCaseWaiting);
  const drafted = cases.filter(isEvidenceCaseDrafted);
  const beside = Boolean(drafts?.any);
  const kind = data?.source_kind ?? "session";
  const transcript = kind === "transcript";

  const onDraft = () =>
    draft.mutate({
      caseIds: waiting.map((one) => one.id),
      shows: Object.fromEntries(
        waiting.map((one) => [one.id, shows[one.id] || "failure"]),
      ),
    });

  const onAdd = () =>
    add.mutate(
      { caseIds: drafted.map((one) => one.id), drafts },
      {
        onSuccess: (answer) => {
          if (!answer.saved) setAddedBeside(drafted.length);
          enqueueSnackbar(
            answer.saved
              ? EVIDENCE_COPY.added_(drafted.length)
              : EVIDENCE_COPY.addedToDraft(drafted.length),
            { variant: "success" },
          );
        },
      },
    );

  return (
    <SideDrawer open={open} onClose={onClose} width={720}>
      <Box sx={{ p: 3, pr: 6 }} data-testid="evidence-review">
        <Typography variant="h6">{EVIDENCE_COPY.title}</Typography>
        {data ? (
          <Typography variant="body2" color="text.secondary">
            {EVIDENCE_COPY.subtitle(cases.length, kind)}
          </Typography>
        ) : null}

        {isLoading ? (
          <Typography variant="body2" sx={{ mt: 2 }}>
            …
          </Typography>
        ) : null}
        {error ? (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error?.statusCode === 404
              ? EVIDENCE_COPY.notFound
              : EVIDENCE_COPY.loadError}
          </Alert>
        ) : null}

        {data ? (
          <>
            <Section title={EVIDENCE_COPY.stepEvidence}>
              {data.redaction?.method === "basic" ? (
                <Alert severity="warning">{data.redaction.reason}</Alert>
              ) : (
                <Alert severity="info">
                  {(transcript
                    ? EVIDENCE_COPY.redactionStored
                    : EVIDENCE_COPY.redactionReady)(data.redaction?.total ?? 0)}
                </Alert>
              )}
              {data.simulated ? (
                <Alert severity="info" data-testid="evidence-simulated">
                  {EVIDENCE_COPY.simulatedNotice(kind)}
                </Alert>
              ) : null}
              {transcript ? (
                <Alert severity="info" data-testid="evidence-transcript">
                  {EVIDENCE_COPY.transcriptNotice(Boolean(data.production))}
                </Alert>
              ) : null}
              {cases.map((one) => (
                <EvidenceCaseCard
                  key={one.id}
                  item={one}
                  kind={kind}
                  simulated={Boolean(data.simulated)}
                  production={Boolean(data.production)}
                  shows={shows[one.id] || "failure"}
                  onShowsChange={
                    data.drafting
                      ? undefined
                      : (caseId, value) =>
                          setShows((current) => ({
                            ...current,
                            [caseId]: value,
                          }))
                  }
                />
              ))}
            </Section>

            <Section title={EVIDENCE_COPY.stepState}>
              <Alert severity="info">
                {transcript
                  ? EVIDENCE_COPY.startingStateTranscript
                  : EVIDENCE_COPY.startingState}
              </Alert>
            </Section>

            <Section title={EVIDENCE_COPY.stepDraft}>
              {data.drafting ? (
                <Alert severity="info" data-testid="evidence-drafting">
                  <strong>{EVIDENCE_COPY.drafting(waiting.length)}</strong>
                  <br />
                  {EVIDENCE_COPY.draftingBody}
                </Alert>
              ) : waiting.length ? (
                <Box>
                  <LoadingButton
                    variant="contained"
                    loading={draft.isPending}
                    onClick={onDraft}
                  >
                    {EVIDENCE_COPY.draft(waiting.length)}
                  </LoadingButton>
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  {EVIDENCE_COPY.noneWaiting}
                </Typography>
              )}
              {draft.error ? (
                <Alert severity="error">{errorText(draft.error)}</Alert>
              ) : null}
            </Section>

            <Section title={EVIDENCE_COPY.stepAdd}>
              {beside && (drafted.length || addedBeside) ? (
                <Alert
                  severity="warning"
                  data-testid="evidence-beside"
                  action={
                    addedBeside ? (
                      // The draft bar sits on the workspace this drawer covers.
                      <Button color="inherit" size="small" onClick={onClose}>
                        {EVIDENCE_COPY.goToSave}
                      </Button>
                    ) : null
                  }
                >
                  {addedBeside
                    ? EVIDENCE_COPY.besideAdded(addedBeside)
                    : EVIDENCE_COPY.besideNotice}
                </Alert>
              ) : null}
              <Divider />
              <Box>
                <LoadingButton
                  variant="contained"
                  color="primary"
                  disabled={!drafted.length || data.drafting}
                  loading={add.isPending}
                  onClick={onAdd}
                >
                  {beside
                    ? EVIDENCE_COPY.addBeside(drafted.length)
                    : EVIDENCE_COPY.addClean(drafted.length)}
                </LoadingButton>
              </Box>
              {add.error ? (
                <Alert severity="error">{errorText(add.error)}</Alert>
              ) : null}
            </Section>
          </>
        ) : null}
      </Box>
    </SideDrawer>
  );
}

EvidenceReviewDrawer.propTypes = {
  envId: PropTypes.string.isRequired,
  importId: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
