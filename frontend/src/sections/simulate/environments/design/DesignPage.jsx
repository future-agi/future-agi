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
import DesignReview from "./DesignReview";
import { useDesignDraftStore } from "./useDesignDraftStore";

const CHAT_FROZEN = "UI only: replying to the builder isn't wired yet";

const names = (list) => list.map((f) => f.name).join(", ");
const count = (n, one, many = `${one}s`) => n && `${n} ${n === 1 ? one : many}`;

// What an agent's build read from it, as the builder's tool steps.
function agentTurn(agent, summary) {
  const tools = summary.tools;
  const rules = summary.rules;
  return {
    id: "agent",
    role: "builder",
    title: "Understanding the agent",
    steps: [
      {
        kind: "note",
        text: `Reading the agent's config${agent.source ? ` from ${agent.source}` : ""}: the prompt, tools and settings ${agent.name} runs with.`,
      },
      {
        kind: "tool",
        label: "read_config",
        state: "completed",
        result: [agent.source, agent.name, agent.version]
          .filter(Boolean)
          .join(" · "),
      },
      {
        kind: "tool",
        label: "extract_tools",
        state: tools ? "completed" : "running",
        result: tools
          ? [
              count(tools.length, "tool") || "No tools",
              ...tools.map((t) => t.name),
            ].join(" · ")
          : "",
      },
      {
        kind: "tool",
        label: "extract_rules",
        state: rules ? "completed" : "running",
        result: rules
          ? `${count(rules.length, "rule") || "No rules"} · read from what to test`
          : "",
      },
      {
        kind: "note",
        text: "Contract written from what we read, not from a form. Rules stated only in a prompt are graded rather than guaranteed, so they get the hardest scenarios.",
      },
    ],
  };
}

// What the builder says once the design is approved, by build status.
function buildTurn(build) {
  const failed = build.validation?.checks.find((c) => !c.ok);
  const text = {
    building:
      "Approved. Building the records and data, tool handlers, rules and checks, then the scenarios. It's checked before it saves.",
    saved: `Built and checked, and saved as v${build.release?.version}. Nothing runs until you connect an agent and start a run.`,
    needs_repair: [
      failed && `A check failed: ${failed.label.toLowerCase()}.`,
      failed?.detail,
      "Nothing was saved and the draft is kept, so you can fix it and approve again.",
    ]
      .filter(Boolean)
      .join(" "),
  }[build.status === "validating" ? "building" : build.status];
  return { id: "build", role: "builder", steps: [{ kind: "note", text }] };
}

// The builder's turns on a design, read only: an agent's build first says
// what it read from the agent, then what it found, then its first open
// question, then how the approved build went. Nothing is said before the page
// shows it.
function turnsOf(draft, summary, build) {
  const turns = draft.agent ? [agentTurn(draft.agent, summary)] : [];
  const reading = summary.readingParts.length > 0;
  let text;
  if (reading) {
    text = `Reading ${summary.readingNames}. I'll say what I found in a moment.`;
  } else {
    const unread = summary.unusable.length
      ? ` I couldn't read ${names(summary.unusable)}.`
      : "";
    const read = names(summary.sources);
    const ask = summary.sources.length
      ? "What does the agent do? For example: it triages new issues, labels and assigns them and closes duplicates."
      : "What does the agent work with, and what can it do? For example: customers and their orders; it can look up an order and refund up to $200.";
    const counts = [
      count(summary.records.length, "kind of record", "kinds of record"),
      count(summary.tools.length, "tool"),
      count(summary.rules.length, "rule"),
    ].filter(Boolean);
    // Only an agent's build can have nothing found and still not be blocked.
    const found = counts.length
      ? `The design has ${counts.join(", ")}, each saying where it came from.${unread} It's on the right.`
      : `No records, tools or rules yet.${unread} The tools are read from the agent's code when it builds.`;
    text = summary.approveBlocked
      ? read
        ? `I read ${read}, but it doesn't say what the agent does.${unread} There's nothing to build yet. ${ask}`
        : `There's nothing to build yet.${unread} ${ask}`
      : `${read ? `I read ${read}. ` : ""}${found} Nothing is built until you approve.`;
  }
  turns.push({ id: "found", role: "builder", steps: [{ kind: "note", text }] });
  // One question at a time: only the first open one is asked.
  const q = summary.questions[0];
  if (q)
    turns.push({
      id: q.id,
      role: "builder",
      steps: [
        {
          kind: "note",
          markdown: true,
          text: [
            `One question first. ${q.why}`,
            `**${q.prompt}**`,
            q.options.map((o) => `- ${o.label}: ${o.description}`).join("\n"),
          ].join("\n\n"),
        },
      ],
    });
  if (build) turns.push(buildTurn(build));
  return turns;
}

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
