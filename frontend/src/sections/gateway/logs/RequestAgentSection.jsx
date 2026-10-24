import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { asText } from "./autoDecision";
import DetailRow from "./DetailRow";

// The agent context a request carried (the log's agent block). Run ids are
// plain text. Renders nothing for a request without one.

const RUN_FIELDS = [
  ["run_id", "Run"],
  ["parent_run_id", "Parent run"],
  ["root_run_id", "Root run"],
];

const TEXT_FIELDS = [
  ["task_id", "Task"],
  ["workflow", "Workflow"],
  ["step", "Step"],
  ["turn", "Turn"],
  ["env", "Environment"],
  ["budget_scope", "Budget scope"],
];

export default function RequestAgentSection({ log }) {
  const agent = log.agent || {};
  const agentId = asText(agent.agent_id);
  const version = asText(agent.agent_version);
  const framework = asText(agent.framework);
  const runs = RUN_FIELDS.filter(([key]) => asText(agent[key]));
  const texts = TEXT_FIELDS.filter(([key]) => asText(agent[key]));
  if (!agentId && !framework && runs.length === 0 && texts.length === 0) {
    return null;
  }

  const stepSource = asText(agent.step_source);
  const identity = [
    agentId && `${agentId}${version ? ` (version ${version})` : ""}`,
    framework,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Box>
      <Typography variant="subtitle2" gutterBottom>
        Agent
      </Typography>
      <Stack spacing={0.5}>
        {identity && <DetailRow label="Agent">{identity}</DetailRow>}
        {runs.map(([key, label]) => (
          <DetailRow key={key} label={label}>
            <Box
              component="span"
              sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
            >
              {asText(agent[key])}
            </Box>
          </DetailRow>
        ))}
        {texts.map(([key, label]) => (
          <DetailRow key={key} label={label}>
            {asText(agent[key])}
            {key === "step" && stepSource ? ` (${stepSource})` : ""}
          </DetailRow>
        ))}
      </Stack>
    </Box>
  );
}

RequestAgentSection.propTypes = { log: PropTypes.object.isRequired };
