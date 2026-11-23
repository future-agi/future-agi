import PropTypes from "prop-types";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import { useState } from "react";
import {
  Box,
  Button,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { AGENT_SUMMARY_COPY } from "./overview.constants";
import { CONNECTED_AGENTS_PATH } from "src/sections/agent-playground/agentsTabs";
import PromptDrawer from "./PromptDrawer";
import PlatformLogo from "../../components/PlatformLogo";
import { agentLineOf } from "./agentLine";

export default function AgentSummarySection({
  subject,
  source,
  connector,
  policy,
  noWorld = false,
  onManageVersions,
  systemPrompt,
  systemPromptOrigin,
}) {
  const [promptOpen, setPromptOpen] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState(null);
  const line = agentLineOf({ subject, source, connector, policy, noWorld });
  const hasMenu = Boolean(systemPrompt || onManageVersions);

  return (
    <Box
      sx={{
        my: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2, py: 1.25 }}
      >
        {line.logo ? (
          <PlatformLogo id={line.logo} name={line.label} />
        ) : (
          <Iconify
            icon={line.icon}
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        )}
        <OverflowTooltip
          title={`${subject ? `${AGENT_SUMMARY_COPY.tests} ` : ""}${line.title}${
            line.facts ? ` · ${line.facts}` : ""
          }`}
        >
          <Typography noWrap sx={{ typography: "s1", flex: 1, minWidth: 0 }}>
            {subject && (
              <Box component="span" sx={{ color: "text.tertiary" }}>
                {`${AGENT_SUMMARY_COPY.tests} `}
              </Box>
            )}
            <Box component="span" sx={{ fontWeight: 600 }}>
              {line.title}
            </Box>
            {line.facts && (
              <Box component="span" sx={{ color: "text.tertiary" }}>
                {` · ${line.facts}`}
              </Box>
            )}
          </Typography>
        </OverflowTooltip>
        {subject?.id && (
          <Button
            component={RouterLink}
            href={`${CONNECTED_AGENTS_PATH}/${subject.id}`}
            size="small"
            variant="outlined"
            endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
            sx={{ color: "text.primary", flexShrink: 0 }}
          >
            {AGENT_SUMMARY_COPY.viewAgent}
          </Button>
        )}
        {hasMenu && (
          <IconButton
            size="small"
            aria-label={AGENT_SUMMARY_COPY.more}
            onClick={(event) => setMenuAnchor(event.currentTarget)}
          >
            <Iconify icon="solar:menu-dots-bold" width={20} />
          </IconButton>
        )}
      </Stack>
      <Menu
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={() => setMenuAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {systemPrompt && (
          <MenuItem
            onClick={() => {
              setMenuAnchor(null);
              setPromptOpen(true);
            }}
          >
            <ListItemIcon>
              <Iconify icon="solar:document-text-linear" width={16} />
            </ListItemIcon>
            <ListItemText>{AGENT_SUMMARY_COPY.viewPrompt}</ListItemText>
          </MenuItem>
        )}
        {onManageVersions && (
          <MenuItem
            onClick={() => {
              setMenuAnchor(null);
              onManageVersions();
            }}
          >
            <ListItemIcon>
              <Iconify icon="solar:layers-minimalistic-linear" width={16} />
            </ListItemIcon>
            <ListItemText>{AGENT_SUMMARY_COPY.manage}</ListItemText>
          </MenuItem>
        )}
      </Menu>
      {systemPrompt && (
        <PromptDrawer
          open={promptOpen}
          onClose={() => setPromptOpen(false)}
          agentName={subject?.name}
          prompt={systemPrompt}
          origin={systemPromptOrigin}
        />
      )}
    </Box>
  );
}

AgentSummarySection.propTypes = {
  subject: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    provider: PropTypes.string,
    versions_count: PropTypes.number,
    active_version: PropTypes.string,
  }),
  source: PropTypes.shape({
    kind: PropTypes.string,
    ref: PropTypes.string,
    commit_sha: PropTypes.string,
  }),
  connector: PropTypes.string,
  policy: PropTypes.string,
  noWorld: PropTypes.bool,
  onManageVersions: PropTypes.func,
  systemPrompt: PropTypes.string,
  systemPromptOrigin: PropTypes.object,
};
