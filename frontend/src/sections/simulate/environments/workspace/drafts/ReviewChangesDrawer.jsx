import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Collapse,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import { visuallyHidden } from "@mui/utils";
import { enqueueSnackbar } from "notistack";
import ContentSkeleton from "src/components/content-skeleton";
import { ConfirmDialog } from "src/components/custom-dialog";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import {
  useDecideChanges,
  useEnvironmentVersions,
} from "src/api/simulate-environments/versions";
import { focusBuilderChat } from "../../buildEnvironment/console/builderChatFocusBus";
import { refusalText } from "../evals/refusalText";
import SideDrawer from "../../components/SideDrawer";
import { DRAFTS_COPY, REVIEW_COPY as COPY } from "../workspace.constants";
import ChangeRow from "./ChangeRow";
import HeldButton from "./HeldButton";
import RecheckCard from "./RecheckCard";
import useDraftSaveFlow from "./useDraftSaveFlow";
import {
  NAME_LIMIT,
  groupChanges,
  keptChanges,
  pieceOf,
  recheckedKeys,
  reviewCounts,
  saveLine,
  suggestedName,
} from "./reviewView";

const cardSx = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
};

function GroupHeading({ children }) {
  return (
    <Typography
      component="h3"
      sx={{ typography: "m3", fontWeight: "fontWeightMedium", mb: 0.75 }}
    >
      {children}
    </Typography>
  );
}
GroupHeading.propTypes = { children: PropTypes.node };

/**
 * Every unsaved change, grouped by what it changes: where it came from, its before and after, and its Accept or
 * Reject. A rejected change stays listed, to restore until the save. Once every change is decided, Validate proves
 * the accepted ones in a sandbox; then they save as a named version.
 */
export default function ReviewChangesDrawer({
  open,
  env,
  canEdit = true,
  busy = false,
  rebuilding = false,
  onClose,
  onSaved,
  onOpenTab,
}) {
  const flow = useDraftSaveFlow(env, { reviewEnabled: open });
  const reviewQuery = flow.reviewQuery;
  const review = flow.review;
  const { data: versions } = useEnvironmentVersions(env?.id, {
    enabled: open && Boolean(env?.versioning?.enabled),
  });
  const decide = useDecideChanges(env?.id);
  // The name as the person typed it; until they do, the field follows the suggestion.
  const [typedName, setTypedName] = useState(null);
  const [discarding, setDiscarding] = useState(false);
  const [announced, setAnnounced] = useState("");
  const saveRef = useRef(null);
  const focusSave = useRef(false);

  const changes = review?.changes || [];
  const name = typedName ?? suggestedName(changes);
  const kept = keptChanges(changes);
  const nameTooLong = name.trim().length > NAME_LIMIT;
  const names = recheckedKeys(changes);
  const line = saveLine(changes);
  const counts = reviewCounts(changes);
  const piece = pieceOf(changes);
  const savedLabel = versions?.defaults?.[piece]?.label || "";
  // Where the server doesn't ask for Validate (checks can't run here), the reviewed changes save as they are.
  const direct = !flow.needsValidate;
  const ready = direct || Boolean(flow.canSaveValidated);

  // Accept all goes away once it's done; what's left to do is Validate or Save.
  useEffect(() => {
    if (!focusSave.current || counts.undecided) return;
    focusSave.current = false;
    saveRef.current?.focus();
  }, [counts.undecided]);
  // Saved or discarded, here or elsewhere: the next set of changes gets its own name.
  useEffect(() => {
    if (!flow.open) setTypedName(null);
  }, [flow.open]);

  const sendDecisions = (decisions) => {
    flow.clearRefusals();
    decide.mutate(
      {
        expectedRevisions: Object.fromEntries(
          Object.entries(review?.drafts ?? {}).map(([one, draft]) => [
            one,
            draft.revision,
          ]),
        ),
        decisions,
      },
      {
        onError: (error) => {
          enqueueSnackbar(
            DRAFTS_COPY.errors[error?.error] ||
              refusalText(error, DRAFTS_COPY.saveError),
            {
              variant: "error",
            },
          );
          reviewQuery?.refetch();
        },
      },
    );
  };

  const decideChange = (change, decision) => {
    const restored = change.decision === "rejected" && decision === "accepted";
    const said = restored ? COPY.announced.restored : COPY.announced[decision];
    setAnnounced(said(change.summary));
    sendDecisions([{ id: change.id, decision }]);
  };
  const acceptRemaining = () => {
    focusSave.current = Boolean(document.activeElement?.closest("button"));
    setAnnounced(COPY.allAccepted);
    sendDecisions([{ remaining: true, decision: "accepted" }]);
  };

  // The builder working or a rebuild running holds Validate, Save and Discard, as the bar does.
  const hold = busy
    ? DRAFTS_COPY.busy
    : rebuilding
      ? DRAFTS_COPY.errors.rebuild_running
      : null;
  let heldReason = null;
  if (hold) heldReason = hold;
  else if (counts.undecided) heldReason = COPY.saveWaiting(counts.undecided);
  else if (!counts.accepted) heldReason = COPY.saveAllRejected;
  else if (flow.stale) heldReason = flow.stale;
  else if (ready && flow.scenariosValidating)
    heldReason = COPY.scenariosValidating;
  else if (ready && nameTooLong) heldReason = COPY.nameTooLong(NAME_LIMIT);
  else if (ready && !direct && !name.trim()) heldReason = COPY.nameNeeded;

  // Before Validate, say what it proves; after, the state it reached.
  const note =
    !ready && !flow.validating && !heldReason && !flow.validationState
      ? COPY.validateFirst(names.length)
      : flow.validationNote;

  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const coversChat = useMediaQuery(useTheme().breakpoints.down("lg"));
  const openLink = (link, tab) => {
    if (link.kind !== "chat") {
      goTab(tab);
      return;
    }
    if (coversChat) onClose?.();
    focusBuilderChat(link.messageId);
  };
  const goTab = (tab) => {
    onClose?.();
    onOpenTab?.(tab);
  };
  const save = () =>
    (direct ? flow.onSave : flow.onSaveValidated)(name, {
      onSuccess: (data) => {
        // The server lists every scenario not Active; one kept as written runs, and has no reason.
        const held = (data?.not_active ?? []).filter((one) => one.reason);
        const notActive = held.length;
        enqueueSnackbar(notActive ? COPY.notActive(notActive) : COPY.saved, {
          variant: notActive ? "warning" : "success",
        });
        setTypedName(null);
        onSaved?.({ name: name.trim() });
        onClose?.();
      },
    });
  const discard = () => {
    setDiscarding(false);
    flow.onDiscard({ onSuccess: () => onClose?.() });
  };

  let primary;
  if (flow.validating) {
    primary = (
      <Button
        size="small"
        variant="contained"
        disabled
        startIcon={<CircularProgress size={12} color="inherit" />}
      >
        {COPY.validating}
      </Button>
    );
  } else if (ready) {
    primary = (
      <HeldButton
        ref={saveRef}
        size="small"
        variant="contained"
        color="primary"
        reason={heldReason}
        disabled={flow.pending}
        onClick={save}
      >
        {COPY.save}
      </HeldButton>
    );
  } else {
    primary = (
      <HeldButton
        ref={saveRef}
        size="small"
        variant="contained"
        color="primary"
        reason={heldReason}
        disabled={flow.pending}
        onClick={flow.onValidate}
      >
        {COPY.validate}
      </HeldButton>
    );
  }

  const body = () => {
    if (reviewQuery?.isLoading) return <ContentSkeleton variant="rows" />;
    if (reviewQuery?.isError && !review) {
      return (
        <ErrorState
          title={COPY.loadError}
          detail={COPY.loadErrorBody}
          onRetry={() => reviewQuery.refetch()}
        />
      );
    }
    if (!changes.length) {
      return (
        <EmptyState
          variant="no-results"
          title={COPY.emptyTitle}
          description={COPY.emptyBody}
        />
      );
    }
    return (
      <Stack spacing={2}>
        {groupChanges(changes).map((group) => (
          <Box key={group.id} component="section" aria-label={group.label}>
            <GroupHeading>
              {COPY.groupTitle(group.label, group.changes.length)}
            </GroupHeading>
            <Box sx={cardSx}>
              {group.changes.map((change) => (
                <ChangeRow
                  key={change.id}
                  change={change}
                  changes={changes}
                  canEdit={canEdit && !flow.validating}
                  onDecide={(decision) => decideChange(change, decision)}
                  onOpen={(link) => openLink(link, group.tab)}
                />
              ))}
            </Box>
          </Box>
        ))}
        {names.length > 0 && <RecheckCard names={names} />}
      </Stack>
    );
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={640} beside>
      <Stack
        role="dialog"
        aria-label={COPY.title}
        sx={{ height: "100%", minHeight: 0 }}
      >
        <Box
          sx={{
            px: 2,
            py: 1.5,
            pr: 6,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Typography
            component="h2"
            sx={{ typography: "m1", fontWeight: "fontWeightSemiBold" }}
          >
            {COPY.title}
          </Typography>
          {changes.length > 0 && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {COPY.subtitle(kept.length, counts.rejected, piece, savedLabel)}
            </Typography>
          )}
        </Box>

        <Box role="status" sx={visuallyHidden}>
          {announced}
        </Box>
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 2 }}>
          {body()}
        </Box>

        {canEdit && changes.length > 0 && (
          <Stack
            spacing={1}
            sx={{
              px: 2,
              py: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Box>
              <Collapse
                in={ready}
                timeout={reduceMotion ? 0 : undefined}
                unmountOnExit
              >
                <Box sx={{ pb: 1 }}>
                  <TextField
                    size="small"
                    fullWidth
                    label={COPY.nameLabel(direct)}
                    placeholder={COPY.namePlaceholder}
                    value={name}
                    onChange={(event) => setTypedName(event.target.value)}
                    error={nameTooLong}
                    helperText={
                      nameTooLong ? COPY.nameTooLong(NAME_LIMIT) : undefined
                    }
                    inputProps={{ maxLength: NAME_LIMIT + 1 }}
                  />
                </Box>
              </Collapse>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography
                  sx={{
                    typography: "s3",
                    color: counts.undecided
                      ? "text.secondary"
                      : "text.tertiary",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {COPY.progress(counts)}
                </Typography>
                {counts.undecided > 0 && canEdit && (
                  <Button
                    size="small"
                    color="inherit"
                    onClick={acceptRemaining}
                    sx={{ color: "text.secondary" }}
                  >
                    {counts.undecided === counts.total
                      ? COPY.acceptAll
                      : COPY.acceptRemaining}
                  </Button>
                )}
              </Stack>
            </Box>
            {(note || flow.error) && (
              <Typography
                role={flow.error ? "alert" : "status"}
                sx={{
                  typography: "s3",
                  color: flow.error ? "status.danger.fg" : "text.secondary",
                }}
              >
                {flow.error || note}
                {flow.failedScenarios?.length > 0 &&
                  `: ${flow.failedScenarios.join(", ")}`}
              </Typography>
            )}
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Button
                size="small"
                color="inherit"
                disabled={flow.pending || Boolean(hold)}
                onClick={() => setDiscarding(true)}
              >
                {COPY.discardAll}
              </Button>
              <OverflowTooltip title={COPY.saveLine(line)}>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {kept.length > 0 && COPY.saveLine(line)}
                </Typography>
              </OverflowTooltip>
              {primary}
            </Stack>
          </Stack>
        )}
      </Stack>
      <ConfirmDialog
        open={discarding}
        onClose={() => setDiscarding(false)}
        title={COPY.discardTitle(changes.length)}
        content={COPY.discardBody(piece, savedLabel)}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={discard}
          >
            {COPY.discard}
          </Button>
        }
      />
    </SideDrawer>
  );
}

ReviewChangesDrawer.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
  }),
  canEdit: PropTypes.bool,
  // The builder is working or still saving its last turn; holds Validate, Save and Discard.
  busy: PropTypes.bool,
  // A queued or running rebuild holds them too.
  rebuilding: PropTypes.bool,
  onClose: PropTypes.func,
  // ({ name }): the accepted changes were saved as a version.
  onSaved: PropTypes.func,
  // (tab id): show the tab that holds a change.
  onOpenTab: PropTypes.func,
};
