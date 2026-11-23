import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import {
  useAcceptSource,
  useEnvironmentSources,
  useSetPersonalData,
  useSourceCapabilities,
} from "src/api/simulate-environments/sources";
import ShimmerText from "../../components/ShimmerText";
import SideDrawer from "../../components/SideDrawer";
import usePrefersReducedMotion from "../../components/usePrefersReducedMotion";
import AddSourceMenu from "./AddSourceMenu";
import ProposalItem from "./ProposalItem";
import { applyFileChoice, columnsOf } from "./personalData";
import {
  PROPOSALS_COPY as COPY,
  batchesOf,
  groupProposals,
  isPickable,
  readProgress,
  reviewDecisions,
  selectionResult,
} from "./proposals";
import { buildSourceReview } from "./sourceReview";
import { dataImportCommand } from "./sources.constants";

const PreparedShape = PropTypes.shape({
  import_ref: PropTypes.string.isRequired,
  draft_digest: PropTypes.string.isRequired,
  draft: PropTypes.object.isRequired,
  capabilities: PropTypes.object.isRequired,
  ui: PropTypes.shape({
    sources: PropTypes.array.isRequired,
    items: PropTypes.array.isRequired,
  }).isRequired,
});

/**
 * Review proposed changes from one prepare response, as a side drawer.
 * Closing keeps the review; `onDone` ends it (rejected or added). Mounted
 * before the first review and after the last, so it slides both ways.
 */
export default function ProposalsDrawer({
  envId,
  prepared = null,
  round = 0,
  open,
  onClose,
  onDone,
  onOpenSource,
  onAddFiles,
  onAddKb,
  onReviewChanges,
}) {
  const { data: summary } = useEnvironmentSources(envId);
  const live = new Set(
    (summary?.sources || prepared?.ui.sources || []).map(
      (one) => one.source_id,
    ),
  );
  return (
    // Kept mounted so closing mid-review keeps checks, edits and picks.
    <SideDrawer
      open={open && Boolean(prepared)}
      onClose={onClose}
      width={560}
      keepMounted
    >
      {prepared && (
        <Stack sx={{ height: "100%" }}>
          <Box
            sx={{
              px: 2.5,
              pt: 2,
              pb: 1.25,
              pr: 6,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ typography: "m1" }}>{COPY.title}</Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {COPY.subtitle(
                // A file refused by its extension was never read.
                prepared.ui.sources
                  .filter(
                    (source) =>
                      source.status !== "unsupported" &&
                      live.has(source.source_id),
                  )
                  .map((source) => source.name),
              )}
            </Typography>
          </Box>
          <Box sx={{ flex: 1, minHeight: 0 }}>
            <Proposals
              key={`${round}:${prepared.import_ref}`}
              layout="drawer"
              envId={envId}
              prepared={prepared}
              onDone={onDone}
              onOpenSource={onOpenSource}
              onAddFiles={onAddFiles}
              onAddKb={onAddKb}
              onReviewChanges={onReviewChanges}
            />
          </Box>
        </Stack>
      )}
    </SideDrawer>
  );
}

ProposalsDrawer.propTypes = {
  envId: PropTypes.string.isRequired,
  // The review to show; null before the first one.
  prepared: PreparedShape,
  // Bumped when a review ends, so the next one starts with fresh picks.
  round: PropTypes.number,
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onDone: PropTypes.func.isRequired,
  onOpenSource: PropTypes.func.isRequired,
  onAddFiles: PropTypes.func.isRequired,
  onAddKb: PropTypes.func,
  onReviewChanges: PropTypes.func,
};

/**
 * What the builder proposes from the files in `prepared`: a "Reading" line
 * per file until it's read, then its items by group, and Reject / Add N
 * changes. `layout="card"` sits inline (builder chat); `"drawer"` fills a
 * drawer with the footer pinned.
 */
export function Proposals({
  envId,
  prepared,
  layout = "card",
  onDone,
  onOpenSource,
  onAddFiles,
  onAddKb,
  onReviewChanges,
  nextLabel,
}) {
  const drawer = layout === "drawer";
  const inset = drawer ? 2.5 : 1.5;
  const { data: summary } = useEnvironmentSources(envId);
  const { data: capabilities } = useSourceCapabilities(envId);
  const accept = useAcceptSource(envId);
  const setPersonalData = useSetPersonalData(envId);
  // Batches already accepted, so a retry after a failure part way sends only the rest.
  const done = useRef(new Set());
  // Unchecked rather than checked, so a file read later arrives checked.
  const [unchecked, setUnchecked] = useState(() => new Set());
  const [edits, setEdits] = useState({});
  const [picks, setPicks] = useState({});
  const [leftOut, setLeftOut] = useState(() => new Set());
  const [supplyAnchor, setSupplyAnchor] = useState(null);

  const live = new Map(
    (summary?.sources || []).map((source) => [source.source_id, source]),
  );
  const progress = readProgress(prepared, summary?.sources);
  const readingIds = new Set(progress.reading.map((one) => one.source_id));
  // Left-out items still render, so they can fold away rather than vanish.
  const all = progress.items.map((item) => {
    if (item.kind === "conflict" && picks[item.id])
      return { ...item, blocking: false, pick: picks[item.id] };
    if (item.kind === "personal")
      return {
        ...item,
        choice: live.get(item.sourceId)?.ui?.personal_choice || null,
      };
    if (edits[item.id] != null) return { ...item, edited: edits[item.id] };
    return item;
  });
  const items = all.filter((item) => !leftOut.has(item.id));
  const reduce = usePrefersReducedMotion();
  const timeout = reduce ? 0 : "auto";
  const checked = items
    .filter((item) => !unchecked.has(item.id))
    .map((item) => item.id);
  const { accepted, failed } = selectionResult(items, checked);
  const count = accepted.length;
  const personal = items.filter((item) => item.kind === "personal");
  const undecided = personal.some((item) => !item.choice);
  const onlyOne = items.filter(isPickable).length === 1;
  // One import covers the whole batch, so it's added once every file is read.
  const reading = progress.reading.length > 0;

  const toggle = (id) =>
    setUnchecked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const choose = (sourceId, choice) => {
    const source = live.get(sourceId);
    if (!source) return;
    setPersonalData(sourceId, {
      choice,
      // The same column map the file's Personal data tab writes.
      privacy: Object.fromEntries(
        applyFileChoice(columnsOf(source), choice).map((column) => [
          column.name,
          column.mode,
        ]),
      ),
    });
  };

  const setLeft = (id, value) =>
    setLeftOut((current) => {
      const next = new Set(current);
      if (value) next.add(id);
      else next.delete(id);
      return next;
    });

  const leaveOut = (item) => {
    setLeft(item.id, true);
    enqueueSnackbar(COPY.leftOut, {
      variant: "info",
      action: (key) => (
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            closeSnackbar(key);
            setLeft(item.id, false);
          }}
        >
          {COPY.undo}
        </Button>
      ),
    });
  };

  // One accept per prepare the review covers, in order, each against the
  // draft the one before it left.
  const add = async () => {
    const choices = Object.fromEntries(
      personal.map((item) => [item.sourceId, item.choice]),
    );
    const privacy = Object.fromEntries(
      personal
        .filter((item) => item.choice === "custom")
        .map((item) => [item.sourceId, live.get(item.sourceId)?.ui?.privacy]),
    );
    const batches = batchesOf(prepared);
    let draft = capabilities?.draft ?? batches[0].capabilities.draft;
    try {
      // eslint-disable-next-line no-restricted-syntax -- sequential on purpose: accepts share one draft
      for (const batch of batches.filter(
        (one) =>
          !done.current.has(one.import_ref) &&
          // A batch whose files were all removed has nothing to add.
          one.ui.sources.some((source) => live.has(source.source_id)),
      )) {
        const own = new Set(batch.ui.items.map((item) => item.id));
        const files = new Set(batch.ui.sources.map((one) => one.source_id));
        const mine = (list) => list.filter((item) => own.has(item.id));
        const ofFiles = (map) =>
          Object.fromEntries(
            Object.entries(map).filter(([id]) => files.has(id)),
          );
        // eslint-disable-next-line no-await-in-loop
        const result = await accept.mutateAsync({
          import_ref: batch.import_ref,
          expected_environment_version_id:
            batch.capabilities.environment_version_id,
          expected_draft: draft,
          expected_draft_digest: batch.draft_digest,
          review: buildSourceReview(
            batch.draft,
            reviewDecisions(
              batch.draft,
              mine(accepted),
              ofFiles(choices),
              privacy,
            ),
          ),
          ui: {
            accepted: mine(accepted),
            failed: mine(failed),
            privacy: ofFiles(privacy),
          },
        });
        done.current.add(batch.import_ref);
        draft = result.draft ?? draft;
      }
    } catch {
      enqueueSnackbar(COPY.addFailed, { variant: "error" });
      return;
    }
    enqueueSnackbar(COPY.added(count, failed.length), {
      variant: "success",
      ...(onReviewChanges && {
        action: (key) => (
          <Button
            size="small"
            color="inherit"
            onClick={() => {
              closeSnackbar(key);
              onReviewChanges();
            }}
          >
            {COPY.reviewChanges}
          </Button>
        ),
      }),
    });
    onDone({ accepted, failed });
  };

  let footer = nextLabel ? COPY.footer.savedAs(nextLabel) : COPY.footer.saved;
  if (undecided) footer = COPY.footer.choose;
  else if (failed.length) footer = COPY.footer.blocked;
  // Nothing to decide while every file is still being read.
  const showFooter = !(reading && !items.length);

  return (
    <Box
      sx={
        drawer
          ? {
              display: "flex",
              flexDirection: "column",
              height: "100%",
              minHeight: 0,
            }
          : { border: "1px solid", borderColor: "divider", borderRadius: 1 }
      }
    >
      {!drawer && (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "m3", fontWeight: 600 }}>
            {COPY.cardTitle}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {COPY.cardSubtitle}
          </Typography>
        </Box>
      )}
      <Box
        sx={drawer ? { flex: 1, minHeight: 0, overflowY: "auto" } : undefined}
      >
        {/* A file's Reading line folds away as its proposals slide in. */}
        {prepared.ui.sources.map((source) => (
          <Collapse
            key={source.source_id}
            in={readingIds.has(source.source_id)}
            timeout={timeout}
            unmountOnExit
          >
            <ShimmerText
              sx={{
                px: inset,
                py: 1.25,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              {COPY.reading(source.name)}
            </ShimmerText>
          </Collapse>
        ))}
        {groupProposals(all).map(({ group, items: inGroup }, index) => {
          const shown = inGroup.filter((item) => !leftOut.has(item.id));
          return (
            <Collapse
              key={group}
              in={shown.length > 0}
              appear
              timeout={timeout}
              unmountOnExit
            >
              <Typography
                sx={{
                  typography: "m3",
                  color: "text.primary",
                  px: inset,
                  pt: index ? 2 : 1.5,
                  pb: 0.75,
                  ...(index > 0 && {
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }),
                }}
              >
                {group}
                <Box
                  component="span"
                  sx={{ color: "text.tertiary", fontWeight: 400, ml: 0.75 }}
                >
                  {shown.length}
                </Box>
              </Typography>
              {inGroup.map((item) => (
                <Collapse
                  key={item.id}
                  in={!leftOut.has(item.id)}
                  appear
                  timeout={timeout}
                  unmountOnExit
                >
                  <ProposalItem
                    item={item}
                    inset={inset}
                    checked={checked.includes(item.id)}
                    hideCheckbox={onlyOne}
                    edit={item.edited}
                    onToggle={() => toggle(item.id)}
                    onEdit={(text) =>
                      setEdits((current) => ({ ...current, [item.id]: text }))
                    }
                    onOpenSource={onOpenSource}
                    onPick={(option) =>
                      setPicks((current) => ({ ...current, [item.id]: option }))
                    }
                    onSupply={setSupplyAnchor}
                    onLeaveOut={() => leaveOut(item)}
                    onChoose={(choice) => choose(item.sourceId, choice)}
                    personalReplaced={
                      item.kind === "table" &&
                      live.get(item.cite?.sourceId)?.ui?.personal_choice ===
                        "replace"
                    }
                  />
                </Collapse>
              ))}
            </Collapse>
          );
        })}
      </Box>
      {showFooter && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: inset,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
          >
            {footer}
          </Typography>
          <Button variant="outlined" size="small" onClick={() => onDone(null)}>
            {COPY.reject}
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={
              reading ||
              undecided ||
              accept.isPending ||
              // Done ends a review whose files were all removed.
              (!count && !personal.length && items.length > 0)
            }
            onClick={count ? add : () => onDone(null)}
          >
            {count ? COPY.add(count) : COPY.done}
          </Button>
        </Stack>
      )}
      <AddSourceMenu
        anchorEl={supplyAnchor}
        onClose={() => setSupplyAnchor(null)}
        onAddFiles={onAddFiles}
        onAddKb={onAddKb}
        limits={capabilities?.upload}
        command={dataImportCommand(envId)}
      />
    </Box>
  );
}

Proposals.propTypes = {
  envId: PropTypes.string.isRequired,
  prepared: PreparedShape.isRequired,
  layout: PropTypes.oneOf(["card", "drawer"]),
  // The review is over: `{accepted, failed}` once added, null when rejected.
  onDone: PropTypes.func.isRequired,
  // (sourceId, at): opens the file at the cited spot.
  onOpenSource: PropTypes.func.isRequired,
  // Supply the records: the files picked from the Add source menu.
  onAddFiles: PropTypes.func.isRequired,
  onAddKb: PropTypes.func,
  // The snackbar's Review changes action; left out when not given.
  onReviewChanges: PropTypes.func,
  // The environment version Save makes, like "v4", for the footer.
  nextLabel: PropTypes.string,
};
