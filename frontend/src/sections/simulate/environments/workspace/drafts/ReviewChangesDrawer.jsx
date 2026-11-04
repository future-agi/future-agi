import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import ContentSkeleton from "src/components/content-skeleton";
import { ConfirmDialog } from "src/components/custom-dialog";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import {
  useDecideChanges,
  useDismissFailedChange,
  useDraftReview,
} from "src/api/simulate-environments/draftReview";
import { useEnvironmentVersions } from "src/api/simulate-environments/versions";
import SideDrawer from "../../components/SideDrawer";
import MockBadge from "../../components/MockBadge";
import { REVIEW_COPY as COPY } from "../workspace.constants";
import ChangeRow from "./ChangeRow";
import FailedChange, { DismissedChanges } from "./FailedChange";
import HeldButton from "./HeldButton";
import RecheckCard from "./RecheckCard";
import {
  comparabilityLine,
  groupChanges,
  keptChanges,
  pieceOf,
  recheckedKeys,
  reviewCounts,
  saveLine,
} from "./reviewView";

const NAME_LIMIT = 80;
const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: -1,
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};
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
 * Every unsaved change, grouped by what it changes: where it came from, its
 * before and after, and its Accept or Reject. A rejected change stays listed, to
 * restore until the save. Changes that couldn't apply say why. Save waits until
 * every change is decided, then makes the accepted ones a named version.
 */
export default function ReviewChangesDrawer({
  open,
  env,
  canEdit = true,
  onClose,
  onSaved,
  onOpenTab,
}) {
  const {
    data: review,
    isLoading,
    isError,
    refetch,
  } = useDraftReview(env?.id, {
    enabled: open,
  });
  const { data: versions } = useEnvironmentVersions(env?.id, {
    enabled: open && Boolean(env?.versioning?.enabled),
  });
  const decide = useDecideChanges(env?.id);
  const dismiss = useDismissFailedChange(env?.id);
  const [name, setName] = useState("");
  const [discarding, setDiscarding] = useState(false);
  const [announced, setAnnounced] = useState("");
  const bodyRef = useRef(null);
  const titleRef = useRef(null);
  const saveRef = useRef(null);
  const focusAfterRemoval = useRef(null);
  const focusSave = useRef(false);

  const changes = review?.changes || [];
  const allFailed = review?.failed || [];
  const failed = allFailed.filter((one) => !one.dismissed);
  const dismissed = allFailed.filter((one) => one.dismissed);
  const kept = keptChanges(changes);
  const nameTooLong = name.trim().length > NAME_LIMIT;
  const names = recheckedKeys(changes);
  const line = saveLine(changes, versions);
  const counts = reviewCounts(changes);
  const piece = pieceOf(changes);
  const savedLabel = versions?.defaults?.[piece]?.label || "";
  const compared = comparabilityLine(
    names.length,
    review?.sample_scenario_total ?? names.length,
  );

  const focusTargets = () =>
    Array.from(
      bodyRef.current?.querySelectorAll(
        '[data-review-focus]:not([aria-disabled="true"])',
      ) || [],
    );
  // The row holding the focused button is about to leave; only a button that
  // had focus hands it on, so a click elsewhere doesn't move anything.
  const beforeRemoval = (message) => {
    const at = focusTargets().indexOf(document.activeElement);
    focusAfterRemoval.current = at < 0 ? null : at;
    setAnnounced(message);
  };
  useEffect(() => {
    const at = focusAfterRemoval.current;
    if (at === null) return;
    focusAfterRemoval.current = null;
    const targets = focusTargets();
    (targets[Math.min(at, targets.length - 1)] || titleRef.current)?.focus();
  }, [failed.length]);
  // Accept all goes away once it's done; what's left to do is Save.
  useEffect(() => {
    if (!focusSave.current || counts.undecided) return;
    focusSave.current = false;
    saveRef.current?.focus();
  }, [counts.undecided]);

  const decideChange = (change, decision) => {
    let said = COPY.announced.undecided;
    if (decision) said = COPY.announced[decision];
    else if (change.decision === "rejected") said = COPY.announced.restored;
    setAnnounced(said(change.summary));
    decide.mutate([{ id: change.id, decision }]);
  };
  const acceptRemaining = () => {
    focusSave.current = Boolean(document.activeElement?.closest("button"));
    setAnnounced(COPY.allAccepted);
    decide.mutate([{ remaining: true, decision: "accepted" }]);
  };

  let saveReason = null;
  if (nameTooLong) saveReason = COPY.nameTooLong(NAME_LIMIT);
  else if (counts.undecided) saveReason = COPY.saveWaiting(counts.undecided);
  else if (!counts.accepted) saveReason = COPY.saveAllRejected;

  const goTab = (tab) => {
    onClose?.();
    onOpenTab?.(tab);
  };
  const save = () => {
    if (review?.is_sample) {
      enqueueSnackbar(COPY.sampleSaved, { variant: "info" });
    }
    onSaved?.({ name: name.trim() });
  };
  const discard = () => {
    setDiscarding(false);
    if (review?.is_sample) {
      enqueueSnackbar(COPY.sampleDiscarded, { variant: "info" });
    }
    onClose?.();
  };

  const body = () => {
    if (isLoading) return <ContentSkeleton variant="rows" />;
    if (isError && !review) {
      return (
        <ErrorState
          title={COPY.loadError}
          detail={COPY.loadErrorBody}
          onRetry={() => refetch()}
        />
      );
    }
    if (!changes.length && !failed.length) {
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
        {failed.length > 0 && (
          <Box>
            <GroupHeading>
              {COPY.groupTitle(COPY.failed, failed.length)}
            </GroupHeading>
            <Box sx={cardSx}>
              {failed.map((one) => (
                <FailedChange
                  key={one.id}
                  failed={one}
                  canEdit={canEdit}
                  onFix={() => goTab("contract")}
                  onDismiss={() => {
                    beforeRemoval(COPY.dismissed(one.detail));
                    dismiss(one.id);
                  }}
                />
              ))}
            </Box>
          </Box>
        )}
        {dismissed.length > 0 && (
          <DismissedChanges
            dismissed={dismissed}
            canEdit={canEdit}
            onBringBack={(id) => dismiss(id, false)}
          />
        )}
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
                  canEdit={canEdit}
                  onDecide={(decision) => decideChange(change, decision)}
                  onOpen={(link) =>
                    link.kind === "chat" ? onClose?.() : goTab(group.tab)
                  }
                />
              ))}
            </Box>
          </Box>
        ))}
        {names.length > 0 && <RecheckCard names={names} />}
        {kept.length > 0 && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {COPY.comparability(compared)}
          </Typography>
        )}
      </Stack>
    );
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
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
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              ref={titleRef}
              tabIndex={-1}
              component="h2"
              sx={{
                typography: "m1",
                fontWeight: "fontWeightSemiBold",
                outline: "none",
              }}
            >
              {COPY.title}
            </Typography>
            {review?.is_sample && <MockBadge />}
          </Stack>
          {changes.length > 0 && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {COPY.subtitle(kept.length, counts.rejected, piece, savedLabel)}
            </Typography>
          )}
        </Box>

        <Box role="status" sx={visuallyHidden}>
          {announced}
        </Box>
        <Box
          ref={bodyRef}
          sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 2 }}
        >
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
            <TextField
              size="small"
              fullWidth
              label={COPY.nameLabel}
              placeholder={COPY.namePlaceholder}
              value={name}
              onChange={(event) => setName(event.target.value)}
              error={nameTooLong}
              helperText={
                nameTooLong ? COPY.nameTooLong(NAME_LIMIT) : undefined
              }
              inputProps={{ maxLength: NAME_LIMIT + 1 }}
            />
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                sx={{
                  typography: "s3",
                  color: counts.undecided ? "text.secondary" : "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {COPY.progress(counts)}
              </Typography>
              {counts.undecided > 0 && (
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
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Button
                size="small"
                color="inherit"
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
              <HeldButton
                ref={saveRef}
                size="small"
                variant="contained"
                color="primary"
                reason={saveReason}
                onClick={save}
              >
                {COPY.save(kept.length ? line.next : null)}
              </HeldButton>
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
  onClose: PropTypes.func,
  // ({ name }): the kept changes are saved.
  onSaved: PropTypes.func,
  // (tab id): show the tab that holds a change.
  onOpenTab: PropTypes.func,
};
