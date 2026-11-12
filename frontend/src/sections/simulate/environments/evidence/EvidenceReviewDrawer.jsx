import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Alert, Box, Button, Divider, Stack, Typography } from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import { useSnackbar } from "notistack";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import SharedSection from "src/components/section";
import SideDrawer from "../components/SideDrawer";
import EvidenceCaseCard from "./EvidenceCaseCard";
import {
  EVIDENCE_COPY,
  caseShowsDefault,
  errorText,
} from "./evidence.constants";
import {
  isEvidenceCaseDrafted,
  isEvidenceCaseWaiting,
  useAddEvidenceCases,
  useDraftEvidenceCases,
  useEvidenceImport,
} from "src/api/simulate-environments/evidenceImports";
import { useDrafts } from "src/api/simulate-environments/versions";

// One step of the review, as the design system's section.
function Section({ title, children }) {
  return (
    <SharedSection title={title} sx={{ mt: 3 }}>
      <Stack spacing={1.25}>{children}</Stack>
    </SharedSection>
  );
}

Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

// The import review: what each case's source holds and lacks and what
// redaction replaces; the starting state Observe never records; the builder's
// drafts; and adding them to the suite. Nothing runs from here.
export default function EvidenceReviewDrawer({
  envId,
  importId,
  open,
  onClose,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const { data, error, isLoading, refetch } = useEvidenceImport(
    envId,
    importId,
  );
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

  // A case left unset sends no choice: the builder judges from the evidence.
  const onDraft = () =>
    draft.mutate({
      caseIds: waiting.map((one) => one.id),
      shows: Object.fromEntries(
        waiting
          .map((one) => [one.id, shows[one.id] ?? caseShowsDefault(one)])
          .filter(([, value]) => value),
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
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Box sx={{ p: 3, pr: 6 }} data-testid="evidence-review">
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>
          {EVIDENCE_COPY.title}
        </Typography>
        {data ? (
          <Typography sx={{ typography: "s1", color: "text.tertiary" }}>
            {EVIDENCE_COPY.subtitle(cases.length, kind)}
          </Typography>
        ) : null}

        {isLoading ? (
          <ContentSkeleton
            variant="rows"
            rows={3}
            label={EVIDENCE_COPY.loading}
            sx={{ mt: 2 }}
          />
        ) : null}
        {error ? (
          // A gone import cannot come back; any other failure can be retried.
          <ErrorState
            title={
              error?.statusCode === 404
                ? EVIDENCE_COPY.notFound
                : EVIDENCE_COPY.loadError
            }
            onRetry={error?.statusCode === 404 ? undefined : () => refetch()}
            sx={{ mt: 2 }}
          />
        ) : null}

        {data ? (
          <>
            <Section title={EVIDENCE_COPY.stepEvidence}>
              {data.redaction?.method === "basic" ? (
                <Alert severity="warning">{data.redaction.reason}</Alert>
              ) : (
                <Alert severity="info">
                  {EVIDENCE_COPY.redactionReady(data.redaction?.total ?? 0)}
                </Alert>
              )}
              {data.simulated ? (
                <Alert severity="info" data-testid="evidence-simulated">
                  {EVIDENCE_COPY.simulatedNotice(kind)}
                </Alert>
              ) : null}
              {cases.map((one) => (
                <EvidenceCaseCard
                  key={one.id}
                  item={one}
                  kind={kind}
                  simulated={Boolean(data.simulated)}
                  shows={shows[one.id]}
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
              <Alert severity="info">{EVIDENCE_COPY.startingState}</Alert>
            </Section>

            <Section title={EVIDENCE_COPY.stepDraft}>
              {data.drafting ? (
                <Alert severity="info" data-testid="evidence-drafting">
                  <Box component="span" sx={{ fontWeight: 500 }}>
                    {EVIDENCE_COPY.drafting(waiting.length)}
                  </Box>
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
                <Typography sx={{ typography: "s1", color: "text.secondary" }}>
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
              {!drafted.length && !data.drafting ? (
                <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                  {EVIDENCE_COPY.noneDrafted}
                </Typography>
              ) : null}
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
