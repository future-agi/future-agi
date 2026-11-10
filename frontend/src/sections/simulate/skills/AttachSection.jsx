import PropTypes from "prop-types";
import React, { useState } from "react";
import {
  Button,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useConnectedAgents } from "src/api/agents/connectedAgents";
import { useAttachSkill, useDetachSkill } from "src/api/simulate-skills/skills";
import { ROW_HEIGHT } from "src/theme/sizes";
import { UNSUPPORTED_SOURCES } from "./skillLabels";

const refText = (attachment) =>
  attachment.tag ? attachment.tag : `v${attachment.version}`;

export default function AttachSection({ skill }) {
  const [agentId, setAgentId] = useState("");
  const [ref, setRef] = useState(
    (skill.tags || []).some((tag) => tag.name === "production")
      ? "production"
      : String(skill.versions?.[0]?.version_number ?? ""),
  );
  const agents = useConnectedAgents({ page: 0, pageSize: 100, search: "" });
  const attach = useAttachSkill(skill.id);
  const detach = useDetachSkill(skill.id);
  const attachedIds = new Set(
    (skill.attachments || []).map((one) => one.agent_definition_id),
  );

  const report = (error, fallback) =>
    enqueueSnackbar(error?.message || fallback, { variant: "error" });

  const submit = async () => {
    const isTag = ref === "production" || ref === "staging";
    try {
      await attach.mutateAsync({
        agent_definition_id: agentId,
        tag: isTag ? ref : null,
        version: isTag ? null : Number(ref),
      });
      enqueueSnackbar("Attached. The agent's next run loads it.", {
        variant: "success",
      });
      setAgentId("");
    } catch (error) {
      report(error, "Could not attach the skill");
    }
  };

  const remove = async (agentDefinitionId) => {
    try {
      await detach.mutateAsync({ agent_definition_id: agentDefinitionId });
    } catch (error) {
      report(error, "Could not detach the skill");
    }
  };

  return (
    <Stack spacing={1}>
      <Typography sx={{ typography: "m3" }}>Used by</Typography>
      {(skill.attachments || []).length ? (
        <Table size="small">
          <TableBody>
            {skill.attachments.map((one) => (
              <TableRow
                key={one.agent_definition_id}
                hover
                sx={{ height: ROW_HEIGHT.default }}
              >
                <TableCell>{one.agent_name}</TableCell>
                <TableCell sx={{ color: "text.secondary" }}>
                  {refText(one)}
                </TableCell>
                <TableCell align="right">
                  <Button
                    size="small"
                    onClick={() => remove(one.agent_definition_id)}
                    disabled={detach.isPending}
                  >
                    Detach
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          Not attached to any agent yet.
        </Typography>
      )}
      <Stack direction="row" spacing={1} alignItems="center">
        <TextField
          select
          size="small"
          label="Agent"
          value={agentId}
          onChange={(event) => setAgentId(event.target.value)}
          sx={{ flex: 1 }}
        >
          {(agents.data?.items || [])
            .filter((agent) => !attachedIds.has(agent.id))
            .map((agent) => (
              <MenuItem
                key={agent.id}
                value={agent.id}
                disabled={UNSUPPORTED_SOURCES.has(agent.source)}
              >
                {UNSUPPORTED_SOURCES.has(agent.source)
                  ? `${agent.name} · Unsupported, runs outside our sandbox`
                  : agent.name}
              </MenuItem>
            ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Version"
          value={ref}
          onChange={(event) => setRef(event.target.value)}
          sx={{ width: 180 }}
        >
          {(skill.tags || []).map((tag) => (
            <MenuItem key={tag.name} value={tag.name}>
              {`${tag.name} (v${tag.version})`}
            </MenuItem>
          ))}
          {(skill.versions || []).map((version) => (
            <MenuItem
              key={version.version_number}
              value={String(version.version_number)}
            >
              {`v${version.version_number}`}
            </MenuItem>
          ))}
        </TextField>
        <Button
          size="small"
          variant="outlined"
          onClick={submit}
          disabled={!agentId || !ref || attach.isPending}
        >
          Attach
        </Button>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        Attached to a tag, the agent gets whichever version the tag points to
        when each run starts. Runs record the exact version they used.
      </Typography>
    </Stack>
  );
}

AttachSection.propTypes = { skill: PropTypes.object.isRequired };
