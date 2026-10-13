import PropTypes from "prop-types";
import { Button, Divider, Stack } from "@mui/material";

/**
 * The customer-facing launch information architecture. Actions only select
 * already-implemented tenant surfaces; they do not imply a provider or route
 * exists when the remote capability response says it is unavailable.
 */
export default function SquadLaunchNav({
  onSurfaceChange,
  onNewAgent,
  onImport,
  getStartedHref = "/dashboard/get-started",
}) {
  const select = (surface) => onSurfaceChange?.(surface);
  return (
    <Stack
      component="nav"
      aria-label="Squad launch navigation"
      spacing={1.25}
      sx={{ mb: 3 }}
    >
      <Stack
        direction="row"
        spacing={0.5}
        useFlexGap
        flexWrap="wrap"
        alignItems="center"
      >
        <Button size="small" onClick={() => select("chat")} href="#squad-chat">
          Chat
        </Button>
        <Button
          size="small"
          onClick={() => select("approvals")}
          href="#squad-explore"
        >
          Inbox
        </Button>
        <Button
          size="small"
          onClick={() => select("explore")}
          href="#squad-explore"
        >
          Explore
        </Button>
        <Button
          size="small"
          onClick={() => select("agents")}
          href="#workspace-agents"
        >
          Workspace Agents
        </Button>
        <Button
          size="small"
          onClick={() => select("templates")}
          href="#workspace-agents"
        >
          Templates
        </Button>
        <Button
          size="small"
          onClick={() => select("connectors")}
          href="#squad-explore"
        >
          Integrations
        </Button>
        <Button
          size="small"
          onClick={() => select("skills")}
          href="#squad-explore"
        >
          Skills
        </Button>
        <Button size="small" component="a" href={getStartedHref}>
          Get Started
        </Button>
        <Button
          size="small"
          onClick={() => onNewAgent?.()}
          href="#workspace-agents"
          variant="contained"
        >
          New agent
        </Button>
        <Button
          size="small"
          onClick={() => select("templates")}
          href="#workspace-agents"
        >
          Start from a template
        </Button>
        <Button
          size="small"
          onClick={() => onImport?.()}
          href="#workspace-agents"
        >
          Import from a .zip file
        </Button>
      </Stack>
      <Divider />
      <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
        <Button
          size="small"
          onClick={() => select("billing")}
          href="#squad-explore"
        >
          Usage
        </Button>
        <Button
          size="small"
          component="a"
          href="/dashboard/settings/profile-settings"
        >
          Settings
        </Button>
      </Stack>
    </Stack>
  );
}

SquadLaunchNav.propTypes = {
  onSurfaceChange: PropTypes.func,
  onNewAgent: PropTypes.func,
  onImport: PropTypes.func,
  getStartedHref: PropTypes.string,
};
