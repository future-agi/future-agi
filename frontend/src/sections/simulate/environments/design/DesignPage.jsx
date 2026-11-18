import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Box, Button } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import EmptyState from "src/components/empty-state";
import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";
import ChatSplitPane from "../components/ChatSplitPane";
import BuilderConsole from "../buildEnvironment/console/BuilderConsole";
import { designSummary } from "./design";
import DesignReview from "./DesignReview";
import { useDesignDraftStore } from "./useDesignDraftStore";

const CHAT_FROZEN = "UI only: replying to the builder isn't wired yet";

const names = (list) => list.map((f) => f.name).join(", ");

// The builder's turns on a design, read only: what it read, what it found,
// then its first open question. Nothing is said before the page shows it.
function turnsOf(draft, summary) {
  const turns = [];
  if (draft.description)
    turns.push({ id: "brief", role: "user", text: draft.description });
  const reading = summary.readingParts.length > 0;
  const steps = [];
  if (reading) {
    steps.push({
      kind: "note",
      text: `Reading ${names(draft.files)}. I'll say what I found in a moment.`,
    });
  } else {
    summary.sources.forEach((f) =>
      steps.push({ kind: "file", path: f.name, note: `Read as ${f.role}` }),
    );
    const unread = summary.unusable.length
      ? ` I couldn't read ${names(summary.unusable)}.`
      : "";
    const read = names(summary.sources);
    const ask = summary.sources.length
      ? "What does the agent do? For example: it triages new issues, labels and assigns them and closes duplicates."
      : "What does the agent work with, and what can it do? For example: customers and their orders; it can look up an order and refund up to $200.";
    const count = (n, what) => n && `${n} ${what}${n === 1 ? "" : "s"}`;
    const counts = [
      count(summary.records.length, "table"),
      count(summary.tools.length, "tool"),
      count(summary.rules.length, "rule"),
    ].filter(Boolean);
    steps.push({
      kind: "note",
      text: summary.approveBlocked
        ? read
          ? `I read ${read}, but it doesn't say what the agent does.${unread} There's nothing to build yet. ${ask}`
          : `There's nothing to build yet.${unread} ${ask}`
        : `${read ? `I read ${read}. ` : ""}The design has ${counts.join(", ")}, each saying where it came from.${unread} It's on the right. Nothing is built until you approve.`,
    });
  }
  turns.push({ id: "found", role: "builder", steps });
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

/**
 * The design proposed before a build, from Build from scratch or an agent's
 * Build environment: the builder's chat on the left, the design on the right.
 * Nothing is built until it's approved, and approving is UI only for now.
 */
export default function DesignPage() {
  const { draftId } = useParams();
  const draft = useDesignDraftStore((s) => s.drafts[draftId]);
  const addFilesToDesign = useDesignDraftStore((s) => s.addFilesToDesign);
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
    <Box sx={{ height: "100%", minHeight: 0 }}>
      <ChatSplitPane chat={chat} busy={summary.readingParts.length > 0}>
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <DesignReview draft={draft} summary={summary} onAddFiles={addFiles} />
        </Box>
      </ChatSplitPane>
    </Box>
  );
}
