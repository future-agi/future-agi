import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useConnectedAgents, useSimStore } from "../store";
import { observeProjectById } from "../_mock/connectAgent";
import { issueProjectLinkOf } from "./issueFixModel";
import { useIssueKnobs } from "./issueFixHeader";
import { clickable, focusRing } from "../components/clickable";

/**
 * Where the issue comes from: "Project: {its Observe project}", linked to
 * the project in Tracing, then the agent that project is linked to. An
 * agent records its Observe project when it's connected in Agents; an
 * issue never picks one. With no agent linked, the row under the header
 * says so (and the header's button links one).
 */
export default function IssueProjectLine({ issue }) {
  const navigate = useNavigate();
  const { state } = useSimStore();
  const { agents } = useConnectedAgents();
  const knobs = useIssueKnobs();
  const link = issueProjectLinkOf(issue, state, agents);
  if (!link.project) return null;
  const inTracing = observeProjectById(link.project)?.tracing;
  const openProject = () =>
    navigate(
      inTracing
        ? `${paths.dashboard.observe}/${link.project}`
        : paths.dashboard.observe,
    );
  const showAgent = link.status === "linked" && !knobs.prereq;

  return (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ minWidth: 0 }}>
      <Iconify
        icon="solar:folder-with-files-linear"
        width={14}
        sx={{ color: "text.disabled", flexShrink: 0 }}
      />
      <Typography
        noWrap
        sx={{ typography: "s2", color: "text.secondary", flexShrink: 0 }}
      >
        Project:
      </Typography>
      <Tooltip arrow title="The Tracing project these traces land in">
        <Typography
          noWrap
          {...clickable(openProject, { role: "link" })}
          sx={{
            typography: "s2",
            fontWeight: 500,
            color: "primary.main",
            minWidth: 56,
            flexShrink: 1,
            cursor: "pointer",
            "&:hover": { textDecoration: "underline" },
            ...focusRing,
          }}
        >
          {link.name}
        </Typography>
      </Tooltip>
      {showAgent && (
        <>
          <Iconify
            icon="solar:arrow-right-linear"
            width={14}
            sx={{ color: "text.disabled", flexShrink: 0, mx: 0.25 }}
          />
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
              color: "text.secondary",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              minWidth: 0,
              ...(link.agent && {
                cursor: "pointer",
                "&:hover": { color: "text.primary", textDecoration: "underline" },
                ...focusRing,
              }),
            }}
          >
            <Tooltip title={`Agent: ${link.agent?.name || "Agent"}`} arrow>
              <span>{link.agent?.name || "Agent"}</span>
            </Tooltip>
          </Box>
        </>
      )}
    </Stack>
  );
}
IssueProjectLine.propTypes = { issue: PropTypes.object };
