import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  ButtonBase,
  Collapse,
  Fade,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  useEnvironmentSources,
  usePendingSourceReview,
  usePrepareKbSource,
  usePrepareUploadSource,
  useRestoreSource,
  useSourceCapabilities,
} from "src/api/simulate-environments/sources";
import SectionCard from "../../components/SectionCard";
import ShimmerText from "../../components/ShimmerText";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import usePrefersReducedMotion from "../../components/usePrefersReducedMotion";
import AddSourceMenu from "./AddSourceMenu";
import KnowledgeBasePicker from "./KnowledgeBasePicker";
import ProposalsDrawer from "./ProposalsDrawer";
import RemoveSourceDialog from "./RemoveSourceDialog";
import useSourceParams from "./useSourceParams";
import { PROPOSALS_COPY, mergePrepared } from "./proposals";
import {
  ROLES,
  SOURCES_COPY as COPY,
  VISIBILITY,
  dataImportCommand,
  isSaved,
  overUploadLimit,
  participantFilesOf,
  rejectedRowsOf,
  usageOf,
} from "./sources.constants";

const GRID = "minmax(180px, 2fr) 56px 1fr 1.1fr 1fr 80px 0.9fr 28px";

// Rejected rows show before the file is saved too, not only once it is.
const stateOf = (source, formats) => {
  const rejected = source.status === "not_saved" && rejectedRowsOf(source) > 0;
  const problem = COPY.problem[rejected ? "rows_rejected" : source.status];
  if (problem) return { problem: problem(source, formats) };
  return { label: COPY.state[source.status] };
};

/**
 * World & tools → Sources: every file this world is built from, what it's
 * used as, who reads it in a run, its state and version. A file's name opens
 * it in the file drawer (see useSourceParams). Removed files are flagged in
 * the summary cache as `ui.removed`, so readers of it skip them.
 *
 * A prepare response (upload or Knowledge base) with proposals opens Review
 * proposed changes; closing that, or leaving the tab, keeps the review until
 * it's rejected or added.
 */
export default function SourcesSection({
  envId,
  readOnly = false,
  onReviewRefresh,
  onReviewChanges,
  supplying = false,
  onSupplied,
}) {
  const { data: summary } = useEnvironmentSources(envId);
  const { data: capabilities } = useSourceCapabilities(envId);
  const prepare = usePrepareUploadSource(envId);
  const prepareKb = usePrepareKbSource(envId);
  const restore = useRestoreSource(envId);
  const [anchor, setAnchor] = useState(null);
  const [kbOpen, setKbOpen] = useState(false);
  const [removing, setRemoving] = useState(null);
  // Held apart from the mutations' data: re-picking files already here returns an
  // empty response, which mustn't wipe the review.
  const [review, setReview] = usePendingSourceReview(envId);
  const [reviewOpen, setReviewOpen] = useState(false);
  // The last review stays in its drawer while it slides shut. A review that
  // follows an ended one gets a new `round`, so it starts with fresh picks; a
  // merge into the open one keeps them.
  const [shown, setShown] = useState({ review, round: 0, ended: false });
  if (!review && shown.review && !shown.ended)
    setShown({ ...shown, ended: true });
  if (review && review !== shown.review)
    setShown({
      review,
      round: shown.round + (shown.ended ? 1 : 0),
      ended: false,
    });
  const reduce = usePrefersReducedMotion();
  const timeout = reduce ? 0 : "auto";
  const { openSource } = useSourceParams();
  const addRef = useRef(null);
  // `supplying`, from a file's Supply the records, opens the Add source menu.
  useEffect(() => {
    if (!supplying) return;
    if (addRef.current) {
      addRef.current.scrollIntoView?.({ block: "center" });
      setAnchor(addRef.current);
    }
    onSupplied?.();
  }, [supplying, onSupplied]);

  const all = summary?.sources || [];
  const sources = all.filter((source) => !source.ui?.removed);
  const removed = all.filter((source) => source.ui?.removed);
  const saved = sources.filter(isSaved);
  const notSaved = sources.length - saved.length;
  const files = participantFilesOf(saved);

  const onPrepare = {
    onSuccess: (response) => {
      if (!response.ui.items.length) return;
      // Supplying records (or uploading again) joins the open review.
      setReview((current) =>
        current ? mergePrepared(current, response) : response,
      );
      setReviewOpen(true);
    },
    onError: () => enqueueSnackbar(COPY.readFailed, { variant: "error" }),
  };

  const addFiles = (picked) => {
    const limit = overUploadLimit(picked, capabilities?.upload);
    if (limit) {
      enqueueSnackbar(limit, { variant: "error" });
      return;
    }
    prepare.mutate(
      {
        files: picked,
        versionId: capabilities?.environment_version_id,
        draft: capabilities?.draft,
      },
      onPrepare,
    );
  };

  const addKb = (selection) => {
    setKbOpen(false);
    prepareKb.mutate({ selection }, onPrepare);
  };

  const endReview = () => {
    setReview(null);
    setReviewOpen(false);
    prepare.reset();
    prepareKb.reset();
  };

  let subtitle = COPY.subtitle.empty;
  if (saved.length) subtitle = COPY.subtitle.saved(summary, notSaved);
  else if (sources.length) subtitle = COPY.subtitle.notSaved(sources.length);

  return (
    <SectionCard
      title={COPY.title(sources.length)}
      subtitle={subtitle}
      action={
        <Stack direction="row" spacing={1} alignItems="center">
          <UiOnlyFlag feature="environmentSources" />
          <Fade
            in={!readOnly && Boolean(review) && !reviewOpen}
            timeout={reduce ? 0 : undefined}
            unmountOnExit
          >
            <Button
              size="small"
              variant="outlined"
              onClick={() => setReviewOpen(true)}
            >
              {PROPOSALS_COPY.reopen}
            </Button>
          </Fade>
          {!readOnly && (
            <Button
              ref={addRef}
              size="small"
              variant="outlined"
              onClick={(event) => setAnchor(event.currentTarget)}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            >
              {COPY.add}
            </Button>
          )}
        </Stack>
      }
      sx={{ mb: 2 }}
    >
      <AddSourceMenu
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        onAddFiles={addFiles}
        onAddKb={() => setKbOpen(true)}
        limits={capabilities?.upload}
        command={dataImportCommand(envId, sources[0])}
      />

      <Collapse in={sources.length > 0} timeout={timeout} unmountOnExit>
        <Box>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: GRID,
              gap: 1.5,
              px: 1.5,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            {COPY.columns.map((column) => (
              <Typography
                key={column}
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {column}
              </Typography>
            ))}
          </Box>
          {/* A file added later slides in under the others. */}
          {sources.map((source) => (
            <Collapse
              key={source.source_id}
              in
              appear
              timeout={timeout}
              sx={{
                "& + &": { borderTop: "1px solid", borderColor: "divider" },
              }}
            >
              <SourceRow
                source={source}
                formats={capabilities?.upload?.formats}
                readOnly={readOnly}
                onOpen={openSource}
                onRemove={() => setRemoving(source)}
                onReviewRefresh={onReviewRefresh}
                timeout={timeout}
              />
            </Collapse>
          ))}
          <Collapse in={saved.length > 0} timeout={timeout} unmountOnExit>
            <Box
              sx={{
                px: 1.5,
                py: 1.25,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <CustomTooltip
                show
                arrow
                placement="top-start"
                title={COPY.inRunDetail(files)}
                slotProps={{ tooltip: { sx: { whiteSpace: "pre-line" } } }}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    width: "fit-content",
                  }}
                >
                  {COPY.inRun(files)}
                </Typography>
              </CustomTooltip>
            </Box>
          </Collapse>
        </Box>
      </Collapse>

      <Collapse in={removed.length > 0} timeout={timeout} unmountOnExit>
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", px: 1.5, pt: 1.25 }}
          >
            {COPY.removed(removed.length)}
          </Typography>
          {removed.map((source) => (
            <Collapse key={source.source_id} in appear timeout={timeout}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{ px: 1.5, py: 0.75 }}
              >
                <Typography
                  noWrap
                  sx={{ typography: "s1", flex: 1, minWidth: 0 }}
                >
                  {source.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {COPY.removedNote}
                </Typography>
                {!readOnly && (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => {
                      restore(source.source_id);
                      enqueueSnackbar(COPY.restored(source.name), {
                        variant: "success",
                      });
                    }}
                  >
                    {COPY.restore}
                  </Button>
                )}
              </Stack>
            </Collapse>
          ))}
        </Box>
      </Collapse>

      {!readOnly && (
        <ProposalsDrawer
          envId={envId}
          prepared={shown.review}
          round={shown.round}
          open={Boolean(review) && reviewOpen}
          onClose={() => setReviewOpen(false)}
          onDone={endReview}
          onOpenSource={(id, at) => {
            setReviewOpen(false);
            openSource(id, null, at);
          }}
          onAddFiles={addFiles}
          onAddKb={() => setKbOpen(true)}
          onReviewChanges={onReviewChanges}
        />
      )}

      {!readOnly && (
        <KnowledgeBasePicker
          envId={envId}
          open={kbOpen}
          onClose={() => setKbOpen(false)}
          onPick={addKb}
        />
      )}

      <RemoveSourceDialog
        envId={envId}
        source={removing}
        onClose={() => setRemoving(null)}
      />
    </SectionCard>
  );
}

SourcesSection.propTypes = {
  envId: PropTypes.string,
  readOnly: PropTypes.bool,
  // Gets the saved source whose Knowledge base file changed.
  onReviewRefresh: PropTypes.func,
  // Opens Review changes, from the snackbar once proposals are added.
  onReviewChanges: PropTypes.func,
  // Opens the Add source menu once, then `onSupplied` is called.
  supplying: PropTypes.bool,
  onSupplied: PropTypes.func,
};

function SourceRow({
  source,
  formats,
  readOnly,
  onOpen,
  onRemove,
  onReviewRefresh,
  timeout,
}) {
  const reading = source.status === "reading";
  const { label, problem } = stateOf(source, formats);
  const role = ROLES.find((one) => one.id === source.role);
  const visibility = VISIBILITY.find((one) => one.id === source.visibility);
  const personal =
    !reading &&
    Object.keys(source.ui?.privacy || {}).length > 0 &&
    !source.ui?.personal_choice;
  const saved = isSaved(source);
  // Only a saved copy can have changed since it was added.
  const changed = saved && Boolean(source.ui?.changed_upstream);
  const usedBy = usageOf(source).count;

  return (
    <Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: GRID,
          alignItems: "center",
          gap: 1.5,
          px: 1.5,
          py: 0.875,
        }}
      >
        <ButtonBase
          onClick={() => onOpen(source.source_id)}
          sx={{
            minWidth: 0,
            justifyContent: "flex-start",
            borderRadius: 0.5,
            "&:hover": { textDecoration: "underline" },
            "&.Mui-focusVisible": {
              outline: "2px solid",
              outlineColor: "primary.main",
              outlineOffset: "2px",
            },
          }}
        >
          <Typography noWrap sx={{ typography: "s1", fontWeight: 500 }}>
            {source.name}
          </Typography>
        </ButtonBase>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {source.ext.toUpperCase()}
        </Typography>
        <CustomTooltip show={Boolean(role)} arrow title={role?.note}>
          <Typography noWrap sx={{ typography: "s1" }}>
            {role?.label || "—"}
          </Typography>
        </CustomTooltip>
        <CustomTooltip
          show={Boolean(visibility)}
          arrow
          title={visibility?.note}
        >
          <Typography noWrap sx={{ typography: "s1" }}>
            {visibility?.label || "—"}
          </Typography>
        </CustomTooltip>
        {reading ? (
          <ShimmerText noWrap>{COPY.state.reading}</ShimmerText>
        ) : (
          <CustomTooltip show={Boolean(problem)} arrow title={problem?.hint}>
            <Typography
              noWrap
              sx={{
                typography: "s1",
                color: problem ? "status.warning.fg" : "text.primary",
                ...(problem && {
                  textDecoration: "underline dotted",
                  textUnderlineOffset: "3px",
                }),
              }}
            >
              {problem ? problem.short : label}
            </Typography>
          </CustomTooltip>
        )}
        <Typography
          sx={{ typography: "s1", fontVariantNumeric: "tabular-nums" }}
        >
          {source.origin?.kind === "knowledge_base"
            ? COPY.yourCopy
            : COPY.version(source.version)}
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s1",
            color: usedBy ? "text.primary" : "text.tertiary",
          }}
        >
          {COPY.usedBy(usedBy)}
        </Typography>
        {readOnly ? (
          <span />
        ) : (
          <CustomTooltip show arrow title={COPY.removeHint(saved)}>
            <IconButton
              size="small"
              aria-label={COPY.remove(source.name)}
              onClick={onRemove}
              sx={{ p: 0.25, color: "text.tertiary" }}
            >
              <Iconify icon="solar:trash-bin-minimalistic-linear" width={16} />
            </IconButton>
          </CustomTooltip>
        )}
      </Box>
      <Collapse in={changed} timeout={timeout} unmountOnExit>
        <Box sx={{ px: 1.5, pb: personal ? 0.5 : 1 }}>
          <RowLine
            text={COPY.changedUpstream}
            action={COPY.reviewRefresh}
            onAction={readOnly ? null : () => onReviewRefresh?.(source)}
          />
        </Box>
      </Collapse>
      <Collapse in={personal} timeout={timeout} unmountOnExit>
        <Box sx={{ px: 1.5, pb: 1 }}>
          <RowLine
            warning
            text={COPY.personal}
            action={COPY.choose}
            onAction={
              readOnly ? null : () => onOpen(source.source_id, "privacy")
            }
          />
        </Box>
      </Collapse>
    </Box>
  );
}

SourceRow.propTypes = {
  // The upload route's formats, named in the Not supported hint.
  formats: PropTypes.arrayOf(PropTypes.string),
  source: PropTypes.shape({
    source_id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    ext: PropTypes.string,
    role: PropTypes.string,
    visibility: PropTypes.string,
    status: PropTypes.string.isRequired,
    status_note: PropTypes.string,
    version: PropTypes.number,
    origin: PropTypes.shape({ kind: PropTypes.string }),
    ui: PropTypes.object,
  }).isRequired,
  readOnly: PropTypes.bool,
  onOpen: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onReviewRefresh: PropTypes.func,
  // The row lines' Collapse timeout; 0 under reduced motion.
  timeout: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};

function RowLine({ text, action, onAction, warning = false }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography
        sx={{
          typography: "s3",
          color: warning ? "status.warning.fg" : "text.secondary",
        }}
      >
        {text}
      </Typography>
      {onAction && (
        <Button size="small" variant="outlined" onClick={onAction}>
          {action}
        </Button>
      )}
    </Stack>
  );
}

RowLine.propTypes = {
  text: PropTypes.string.isRequired,
  action: PropTypes.string.isRequired,
  onAction: PropTypes.func,
  warning: PropTypes.bool,
};
