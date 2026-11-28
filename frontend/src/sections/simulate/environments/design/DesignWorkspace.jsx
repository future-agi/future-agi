import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import ChatSplitPane from "../components/ChatSplitPane";
import BuilderConsole from "../buildEnvironment/console/BuilderConsole";
import DesignReview from "./DesignReview";
import SourceReviewDesign from "./SourceReviewDesign";
import { useEnvironmentSourceReview } from "src/api/simulate-environments/sourceImports";
import { ROLES } from "../workspace/sources/sources.constants";
import ChatMarkdown from "../buildEnvironment/console/ChatMarkdown";

function ProposedPlan({
  question,
  canEdit,
  send,
  busy,
  requirements,
  modality,
  sourceReviewId,
}) {
  // No question yet: the builder is still drafting, so every part is read.
  const proposal = question?.design_proposal;
  const names = (items = []) => items.map((name) => ({ name }));
  const texts = (items = []) => items.map((text) => ({ text }));
  const review = useEnvironmentSourceReview(sourceReviewId);
  const sources = (
    review.data?.state === "accepted" ? review.data.preview?.sources || [] : []
  )
    .filter((source) => source.pin?.source_id && source.filename)
    .map((source) => ({
      id: source.pin.source_id,
      name: source.filename,
      role: ROLES.find((role) => role.id === source.pin.role)?.label,
      visibility: source.visibility,
      reviewed: true,
    }));
  const rules = texts(proposal?.rules).map((rule, index) => {
    const locations = (proposal.rule_sources || []).filter(
      (ref) =>
        ref.rule_index === index &&
        Number.isInteger(ref.page) &&
        ref.page > 0 &&
        sources.some(
          (source) =>
            source.id === ref.source_id &&
            source.visibility === "agent_visible",
        ),
    );
    return {
      ...rule,
      sourceIds: locations.map((ref) => ref.source_id),
      src: locations
        .map(
          (ref) =>
            `From ${sources.find((source) => source.id === ref.source_id).name} · page ${ref.page}`,
        )
        .join(", "),
    };
  });
  const readingNames =
    sources.map((source) => source.name).join(", ") || "your brief";
  const summary = {
    structured: !question || Boolean(proposal),
    brief: {
      description: proposal
        ? [proposal.one_liner, proposal.summary].filter(Boolean).join("\n\n")
        : question?.prompt || requirements,
      channel: modality,
      sourcesLine: sourceReviewId
        ? "Reviewed source material"
        : "None selected",
    },
    records: names(proposal?.records),
    tools: names(proposal?.tools),
    rules,
    people: names(proposal?.people),
    scenarios: texts(proposal?.use_cases),
    deps: (proposal?.dependencies || []).map((request) => ({
      id: request,
      request,
    })),
    readingParts: question
      ? []
      : ["records", "tools", "rules", "people", "scenarios"],
    readingNames,
    readingLine: `The builder is drafting the design from ${readingNames}. Nothing is built until you approve it.`,
    sources,
    unusable: [],
    approveBlocked: Boolean(proposal?.dependencies?.length),
  };
  return (
    <DesignReview
      live
      disabled={busy || !canEdit}
      draft={{ description: requirements, files: [] }}
      summary={summary}
      onApprove={() => send("Build this")}
      onDepChoice={(dependency, choice) =>
        send(
          "Revise the design. " +
            (choice === "simulate"
              ? "Simulate this dependency: "
              : "Leave out this dependency: ") +
            dependency,
        )
      }
    />
  );
}
ProposedPlan.propTypes = {
  question: PropTypes.object,
  canEdit: PropTypes.bool,
  send: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  requirements: PropTypes.string,
  modality: PropTypes.string,
  sourceReviewId: PropTypes.string,
};

export default function DesignWorkspace({
  chat,
  canEdit,
  question,
  drafting,
  review,
  progress,
  requirements,
  modality,
  sourceReviewId,
}) {
  return (
    <Box sx={{ flex: 1, minHeight: 0 }}>
      <ChatSplitPane
        busy={chat.running || chat.inFlight}
        chat={({ collapse, open, collapseRef }) => (
          <BuilderConsole
            emptyMessage={
              review
                ? "The builder starts after source review. Check the status of your files beside this conversation."
                : undefined
            }
            turns={chat.turns}
            running={chat.running}
            working={chat.working}
            onSend={chat.send}
            onStop={chat.stop}
            canStop={chat.canStop}
            stopping={chat.stopping}
            frozen={Boolean(review) || !canEdit || chat.frozen}
            frozenReason={
              review
                ? "Review the sources to start the builder."
                : chat.frozenReason
            }
            answerWithOptions={chat.answerWithOptions}
            onCollapse={collapse}
            collapseRef={collapseRef}
            active={open}
          />
        )}
      >
        <Stack spacing={2} sx={{ p: 3, height: "100%", overflowY: "auto" }}>
          {review ? (
            <SourceReviewDesign
              requirements={requirements}
              modality={modality}
              review={review}
            />
          ) : question || drafting ? (
            // One page from drafting to the proposal, so the parts fade in as they arrive.
            <ProposedPlan
              question={question}
              requirements={requirements}
              modality={modality}
              sourceReviewId={sourceReviewId}
              canEdit={canEdit}
              send={chat.send}
              busy={chat.running || chat.frozen}
            />
          ) : (
            <>
              {requirements && (
                <Box>
                  <Typography variant="h6">Requested environment</Typography>
                  <ChatMarkdown text={requirements} />
                </Box>
              )}
              {progress}
            </>
          )}
        </Stack>
      </ChatSplitPane>
    </Box>
  );
}
DesignWorkspace.propTypes = {
  chat: PropTypes.object.isRequired,
  canEdit: PropTypes.bool,
  question: PropTypes.object,
  drafting: PropTypes.bool,
  review: PropTypes.node,
  progress: PropTypes.node,
  requirements: PropTypes.string,
  modality: PropTypes.string,
  sourceReviewId: PropTypes.string,
};
