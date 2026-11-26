import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import ChatSplitPane from "../components/ChatSplitPane";
import BuilderConsole from "../buildEnvironment/console/BuilderConsole";
import DesignReview from "./DesignReview";
import ChatMarkdown from "../buildEnvironment/console/ChatMarkdown";

function ProposedPlan({
  question,
  canEdit,
  send,
  busy,
  requirements,
  modality,
  hasSources,
}) {
  const proposal = question.design_proposal;
  const names = (items = []) => items.map((name) => ({ name }));
  const texts = (items = []) => items.map((text) => ({ text }));
  const summary = {
    structured: Boolean(proposal),
    brief: {
      description: proposal
        ? [proposal.one_liner, proposal.summary].filter(Boolean).join("\n\n")
        : question.prompt,
      channel: modality,
      sourcesLine: hasSources ? "Reviewed source material" : "None selected",
    },
    records: names(proposal?.records),
    tools: names(proposal?.tools),
    rules: texts(proposal?.rules),
    people: names(proposal?.people),
    scenarios: texts(proposal?.use_cases),
    deps: (proposal?.dependencies || []).map((request) => ({
      id: request,
      request,
    })),
    readingParts: [],
    sources: [],
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
  question: PropTypes.object.isRequired,
  canEdit: PropTypes.bool,
  send: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  requirements: PropTypes.string,
  modality: PropTypes.string,
  hasSources: PropTypes.bool,
};

export default function DesignWorkspace({
  chat,
  canEdit,
  question,
  review,
  progress,
  requirements,
  modality,
  hasSources,
}) {
  return (
    <Box sx={{ flex: 1, minHeight: 0 }}>
      <ChatSplitPane
        busy={chat.running || chat.inFlight}
        chat={({ collapse, open, collapseRef }) => (
          <BuilderConsole
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
          {review ||
            (question ? (
              <ProposedPlan
                key={question.message_id}
                question={question}
                requirements={requirements}
                modality={modality}
                hasSources={hasSources}
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
            ))}
        </Stack>
      </ChatSplitPane>
    </Box>
  );
}
DesignWorkspace.propTypes = {
  chat: PropTypes.object.isRequired,
  canEdit: PropTypes.bool,
  question: PropTypes.object,
  review: PropTypes.node,
  progress: PropTypes.node,
  requirements: PropTypes.string,
  modality: PropTypes.string,
  hasSources: PropTypes.bool,
};
