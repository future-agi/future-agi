import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Box,
  Button,
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
import ChatSplitPane from "../components/ChatSplitPane";
import BuilderConsole from "../buildEnvironment/console/BuilderConsole";
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

// The builder's turns on a design, read only: an agent's build first says
// what it read from the agent, then what it found, then its first open
// question. Nothing is said before the page shows it.
function turnsOf(draft, summary) {
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
  return turns;
}

/* The bar above the chat and the design: back, the draft's name, where it
   stands, and its overflow menu. */
function DesignHeader({ draft, blocked }) {
  const navigate = useNavigate();
  const discard = useDesignDraftStore((s) => s.discardDesignDraft);
  const [anchor, setAnchor] = useState(null);
  const [confirm, setConfirm] = useState(false);
  const root = paths.dashboard.simulate.environments.root;
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
      <StatusBadge
        status="needs_review"
        label={blocked ? "Needs detail" : "Awaiting approval"}
        variant="badge"
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
        content={`${draft.name} and its design are removed. Nothing was built.`}
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
};

/**
 * The design proposed before a build, from Build from scratch or an agent's
 * Build environment: the builder's chat on the left, the design on the right.
 * Nothing is built until it's approved, and approving is UI only for now.
 */
export default function DesignPage() {
  const { draftId } = useParams();
  const draft = useDesignDraftStore((s) => s.drafts[draftId]);
  const addFilesToDesign = useDesignDraftStore((s) => s.addFilesToDesign);
  const setDepChoice = useDesignDraftStore((s) => s.setDepChoice);
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
  const turns = useMemo(
    () => (draft ? turnsOf(draft, summary) : []),
    [draft, summary],
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
            />
          </Box>
        </ChatSplitPane>
      </Box>
    </Box>
  );
}
