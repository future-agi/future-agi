import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Box,
  Button,
  Fade,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import CustomTooltip from "src/components/tooltip";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";
import {
  useApproveDesign,
  useDesignBuild,
} from "src/api/simulate-environments/designBuild";
import ChatSplitPane from "../components/ChatSplitPane";
import BuilderConsole from "../buildEnvironment/console/BuilderConsole";
import usePrefersReducedMotion from "../components/usePrefersReducedMotion";
import RenameEnvironmentDialog from "../workspace/RenameEnvironmentDialog";
import { designSummary } from "./design";
import { turnsOf } from "./designTurns";
import DesignReview from "./DesignReview";
import { useDesignDraftStore } from "./useDesignDraftStore";

const CHAT_FROZEN = "UI only: replying to the builder isn't wired yet";

// The header pill once approved: [status, label] by build status.
const BUILD_PILL = {
  building: ["building", "Building"],
  validating: ["building", "Validating"],
  needs_repair: ["needs_changes", "Needs repair"],
};

/* The bar above the chat and the design: back, the draft's name, where it
   stands, and its overflow menu. */
function DesignHeader({ draft, blocked, build, suggestedName }) {
  const navigate = useNavigate();
  const discard = useDesignDraftStore((s) => s.discardDesignDraft);
  const renameDraft = useDesignDraftStore((s) => s.renameDraft);
  const reduce = usePrefersReducedMotion();
  const [anchor, setAnchor] = useState(null);
  const [confirm, setConfirm] = useState(false);
  const [renaming, setRenaming] = useState(false);
  // A typed name is never overwritten; the suggestion is only offered.
  const offer =
    draft.nameTyped && suggestedName && suggestedName !== draft.name
      ? suggestedName
      : null;
  const lastOffer = useRef(offer);
  if (offer) lastOffer.current = offer;
  const root = paths.dashboard.simulate.environments.root;
  const [status, label] =
    build?.status === "saved"
      ? ["passed", `Saved v${build.release?.version}`]
      : BUILD_PILL[build?.status] || [
          "needs_review",
          blocked ? "Needs detail" : "Awaiting approval",
        ];
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 3,
        py: 1.5,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      <CustomTooltip show title="All environments" size="small" arrow>
        <IconButton
          aria-label="All environments"
          onClick={() => navigate(root)}
          sx={{ color: "text.subtitle" }}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={16} />
        </IconButton>
      </CustomTooltip>
      <Typography
        noWrap
        sx={{ typography: "m1", fontWeight: 600, minWidth: 0 }}
      >
        {draft.name}
      </Typography>
      <CustomTooltip show title="Rename environment" size="small" arrow>
        <IconButton
          aria-label="Rename environment"
          size="small"
          onClick={() => setRenaming(true)}
          sx={{ color: "text.subtitle" }}
        >
          <Iconify icon="solar:pen-linear" width={15} />
        </IconButton>
      </CustomTooltip>
      <StatusBadge status={status} label={label} variant="badge" />
      <Fade in={!!offer} timeout={reduce ? 0 : undefined} unmountOnExit>
        <Button
          size="small"
          variant="text"
          onClick={() => renameDraft(draft.id, lastOffer.current)}
          sx={{ typography: "s2", flexShrink: 0 }}
        >
          {`Use '${lastOffer.current}'?`}
        </Button>
      </Fade>
      <RenameEnvironmentDialog
        open={renaming}
        env={draft}
        onClose={() => setRenaming(false)}
        onSave={(name) => renameDraft(draft.id, name)}
      />
      <Box flex={1} />
      <CustomTooltip show title="More actions" size="small" arrow>
        <IconButton
          size="small"
          aria-label="More actions"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ color: "text.subtitle" }}
        >
          <Iconify icon="solar:menu-dots-bold" width={18} />
        </IconButton>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <MenuItem
          onClick={() => {
            setAnchor(null);
            setConfirm(true);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          </ListItemIcon>
          Discard draft
        </MenuItem>
      </Menu>
      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Discard this draft?"
        content={`${draft.name} and its design are removed.${build ? "" : " Nothing was built."}`}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              navigate(root);
              discard(draft.id);
            }}
          >
            Discard draft
          </Button>
        }
      />
    </Stack>
  );
}
DesignHeader.propTypes = {
  draft: PropTypes.object.isRequired,
  blocked: PropTypes.bool,
  build: PropTypes.shape({
    status: PropTypes.string,
    release: PropTypes.shape({ version: PropTypes.number }),
  }),
  suggestedName: PropTypes.string,
};

/**
 * The design proposed before a build, from Build from scratch or an agent's
 * Build environment: the builder's chat on the left, the design on the right.
 * Nothing is built until it's approved; the build after approval is a stub
 * for now (useDesignBuild).
 */
export default function DesignPage() {
  const { draftId } = useParams();
  const draft = useDesignDraftStore((s) => s.drafts[draftId]);
  const addFilesToDesign = useDesignDraftStore((s) => s.addFilesToDesign);
  const setDepChoice = useDesignDraftStore((s) => s.setDepChoice);
  const leaveOutFile = useDesignDraftStore((s) => s.leaveOutFile);
  const build = useDesignBuild(draftId);
  const approve = useApproveDesign(draftId);
  const navigate = useNavigate();
  // An approved build is followed on its environment's page; only a failed
  // check keeps it here, to repair.
  const toEnvironment = !!build && build.status !== "needs_repair";
  useEffect(() => {
    if (toEnvironment)
      navigate(paths.dashboard.simulate.environments.detail(draftId), {
        replace: true,
      });
  }, [toEnvironment, draftId, navigate]);
  const readUntil = draft?.readUntil || 0;
  const [now, setNow] = useState(Date.now);

  // Tick only while the files are read, so the parts fill in one by one.
  useEffect(() => {
    setNow(Date.now());
    if (Date.now() >= readUntil) return undefined;
    const t = setInterval(() => {
      setNow(Date.now());
      if (Date.now() >= readUntil) clearInterval(t);
    }, 200);
    return () => clearInterval(t);
  }, [readUntil]);

  const summary = useMemo(
    () => draft && designSummary(draft, now),
    [draft, now],
  );
  const renameDraft = useDesignDraftStore((s) => s.renameDraft);
  const suggested = summary?.suggestedName;
  // The builder's suggestion fills an untitled name; a typed one stays.
  useEffect(() => {
    if (draft && !draft.nameTyped && suggested && suggested !== draft.name)
      renameDraft(draftId, suggested, { typed: false });
  }, [draft, draftId, suggested, renameDraft]);
  const turns = useMemo(
    () => (draft ? turnsOf(draft, summary, build) : []),
    [draft, summary, build],
  );
  const chat = useCallback(
    ({ collapse, open, collapseRef }) => (
      <BuilderConsole
        turns={turns}
        frozen
        frozenReason={CHAT_FROZEN}
        onCollapse={collapse}
        collapseRef={collapseRef}
        active={open}
      />
    ),
    [turns],
  );

  if (!draft)
    return (
      <EmptyState
        icon="solar:document-text-linear"
        title="This design isn't here"
        description="Designs are kept in the browser they were started in until the build is wired."
        action={
          <Button
            variant="contained"
            color="primary"
            component={RouterLink}
            href={paths.dashboard.simulate.environments.root}
          >
            Go to environments
          </Button>
        }
      />
    );

  const addFiles = (files) => {
    const note = addFilesToDesign(draftId, files);
    if (!note) return;
    const said = [
      note.added.length && `Added ${note.added.join(", ")}.`,
      note.replaced.length &&
        `Replaced ${note.replaced.join(", ")} with the newer copy.`,
      note.skipped.length &&
        `${note.skipped.join(", ")} already in the design.`,
    ].filter(Boolean);
    enqueueSnackbar(said.join(" "), { variant: "info" });
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <DesignHeader
        draft={draft}
        blocked={!summary.readingParts.length && summary.approveBlocked}
        build={build}
        suggestedName={summary.suggestedName}
      />
      <Box sx={{ flex: 1, minHeight: 0 }}>
        <ChatSplitPane chat={chat} busy={summary.readingParts.length > 0}>
          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
            <DesignReview
              draft={draft}
              summary={summary}
              onAddFiles={addFiles}
              onDepChoice={(depId, choice) =>
                setDepChoice(draftId, depId, choice)
              }
              build={build}
              onApprove={() => approve.mutate()}
              onLeaveOut={(fileId) => leaveOutFile(draftId, fileId)}
            />
          </Box>
        </ChatSplitPane>
      </Box>
    </Box>
  );
}
