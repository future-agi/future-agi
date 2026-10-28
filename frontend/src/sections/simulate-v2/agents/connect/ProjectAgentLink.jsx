import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  ButtonBase,
  Dialog,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { SimStoreProvider, useConnectedAgents } from "../../store";
import { agentIdOf } from "../../_mock/agentRegistry";
import { projectLinkOf } from "../../_mock/projectLinks";
import { TextLink } from "./connectBits";
import YourAgentsList from "./YourAgentsList";

/*
  A tracing project's agent, on the project itself. A project only exists
  once its first trace arrives, so this is never asked before there's
  anything to match — and it's the same link Connect agent to fix makes:
  saved on the agent, one project per agent. Linked: the agent, opening its
  page. Not linked: "Link an agent" — one of your agents, or connect one.
*/
function ProjectAgentLinkInner({ project, projectName }) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const { agents, state } = useConnectedAgents();
  const [open, setOpen] = useState(false);
  const link = projectLinkOf(project, state, agents);
  const agent = link.status === "linked" ? link.agent : null;

  if (agent)
    return (
      <ButtonBase
        onClick={() =>
          navigate(
            `${paths.dashboard.agents}/connected/${agentIdOf(agent.key)}`,
          )
        }
        sx={{
          gap: 0.75,
          px: 1,
          height: 30,
          borderRadius: 1,
          typography: "s2",
          color: "text.secondary",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify icon="solar:link-minimalistic-2-linear" width={14} />
        <Box component="span">Agent:</Box>
        <Box component="span" sx={{ color: "text.primary", fontWeight: 500 }}>
          {agent.name}
        </Box>
      </ButtonBase>
    );

  const connectNew = () => {
    const back = `${window.location.pathname}${window.location.search}`;
    navigate(
      `${paths.dashboard.agents}/connect?${new URLSearchParams({ project, projectName, return: back })}`,
    );
  };

  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ typography: "s2", color: "text.secondary", px: 1, flexShrink: 0 }}
      >
        <Iconify icon="solar:link-broken-minimalistic-linear" width={14} />
        <Box component="span">Agent: not linked</Box>
        <Box component="span" sx={{ color: "text.disabled" }}>
          ·
        </Box>
        <TextLink onClick={() => setOpen(true)}>Link an agent</TextLink>
      </Stack>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        PaperProps={{
          sx: {
            borderRadius: 2,
            maxWidth: 720,
            width: "100%",
            bgcolor: "background.paper",
            backgroundImage: "none",
            border: "1px solid",
            borderColor: "divider",
          },
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2.5,
            py: 1.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            noWrap
            sx={{ typography: "m3", fontWeight: 600, flex: 1, minWidth: 0 }}
          >
            {`Link an agent to ${projectName}`}
          </Typography>
          <IconButton
            size="small"
            onClick={() => setOpen(false)}
            aria-label="Close"
          >
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Stack>
        <YourAgentsList
          project={project}
          projectName={projectName}
          onLinked={(a) => {
            setOpen(false);
            enqueueSnackbar(
              `${projectName} is linked to ${a.name} — its Error Feed issues can be fixed now`,
              { variant: "success" },
            );
          }}
        />
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2.5,
            py: 2,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s1", fontWeight: 500 }}>
              Or connect it
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Not in Agents yet — connect its code, the platform it runs on, or
              its endpoint. This project comes with it.
            </Typography>
          </Box>
          <ButtonBase
            onClick={connectNew}
            sx={{
              px: 1.5,
              height: 30,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
              typography: "s2",
              fontWeight: 500,
              flexShrink: 0,
            }}
          >
            Choose how
          </ButtonBase>
        </Stack>
      </Dialog>
    </>
  );
}
ProjectAgentLinkInner.propTypes = {
  project: PropTypes.string.isRequired,
  projectName: PropTypes.string,
};

/** Usable outside the simulation pages — it brings the agents store with it. */
export default function ProjectAgentLink({ project, projectName }) {
  if (!project) return null;
  return (
    <SimStoreProvider>
      <ProjectAgentLinkInner
        project={project}
        projectName={projectName || "this project"}
      />
    </SimStoreProvider>
  );
}
ProjectAgentLink.propTypes = {
  /* The Observe project id. */
  project: PropTypes.string,
  projectName: PropTypes.string,
};
