import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useConnectedAgents, useSimStore } from "../store";
import { issueProjectLinkOf } from "./issueFixModel";
import { clickable, focusRing } from "../components/clickable";

/**
 * Where the issue comes from: its Observe project, and the agent that project
 * is linked to. Read-only: an agent records its Observe project when it's
 * connected in Agents; an issue never picks one.
 */
export default function IssueProjectLine({ issue }) {
  const navigate = useNavigate();
  const { state } = useSimStore();
  const { agents } = useConnectedAgents();
  const link = issueProjectLinkOf(issue, state, agents);
  if (!link.project) return null;

  return (
    <Stack direction="row" alignItems="center" gap={0.75} sx={{ minWidth: 0 }}>
      <Iconify
        icon="solar:folder-with-files-linear"
        width={14}
        sx={{ color: "text.disabled", flexShrink: 0 }}
      />
      <Tooltip title="The Observe project these traces land in" arrow>
        <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
          {link.name}
        </Typography>
      </Tooltip>
      <Iconify
        icon="solar:arrow-right-linear"
        width={14}
        sx={{ color: "text.disabled", flexShrink: 0 }}
      />
      {link.status === "linked" ? (
        <Box
          component="span"
          {...clickable(
            link.agent
              ? () =>
                  navigate(
                    `${paths.dashboard.agents}/connected/${link.agent.id}`,
                  )
              : undefined,
            { role: "link" },
          )}
          sx={{
            typography: "s2",
            fontWeight: 600,
            color: "text.primary",
            ...(link.agent && {
              cursor: "pointer",
              "&:hover": { textDecoration: "underline" },
              ...focusRing,
            }),
          }}
        >
          {link.agent?.name || "Agent"}
        </Box>
      ) : (
        <Typography noWrap sx={{ typography: "s2", color: "text.disabled" }}>
          No agent
        </Typography>
      )}
    </Stack>
  );
}
IssueProjectLine.propTypes = { issue: PropTypes.object };
