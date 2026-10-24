import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Box,
  Divider,
  Link,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { sourceKindOf } from "../_mock/agentRegistry";
import { TRACEAI_DOCS_URL, useAgentObserveProject } from "./observeProject";
import { ActionText, InfoTip } from "./AgentBits";

const HOW = {
  set: "linked",
  watching: "linked through its call logs",
  seeded: "linked",
};

const CODE_KINDS = ["repo", "upload", "framework"];

/**
 * The agent's Observe project — one per agent. Its traces or call logs feed
 * the Error Feed, and its issues are fixed against this agent. Linked, with
 * Change; or not linked, with Link a project.
 */
export function ObserveProjectCard({ agent }) {
  const {
    project,
    pending,
    options,
    link,
    unlink,
    canUnlink,
    viewIssues,
    openInObserve,
  } = useAgentObserveProject(agent);
  const rowRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const setAnchor = (on) => setMenuOpen(!!on);
  const code = CODE_KINDS.includes(sourceKindOf(agent));

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        ref={rowRef}
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 2, py: 1 }}
      >
        <Typography
          sx={{
            typography: "s2",
            color: "text.subtitle",
            width: 150,
            flexShrink: 0,
          }}
        >
          Observe project
          <InfoTip text="Its traces or call logs feed the Error Feed. This link is what lets Fix find this agent behind an issue." />
        </Typography>
        {project ? (
          <>
            <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
              <Box component="span" sx={{ fontWeight: 600 }}>
                {project.name}
              </Box>
              <Box component="span" sx={{ color: "text.subtitle" }}>
                {pending
                  ? " · waiting for its first trace"
                  : ` · ${HOW[project.how] || "linked"}`}
              </Box>
            </Typography>
            {/* Nothing in Observe until the first trace arrives. */}
            {!pending && (
              <ActionText onClick={openInObserve}>Open in Observe</ActionText>
            )}
            {!pending && (
              <ActionText onClick={viewIssues}>View issues</ActionText>
            )}
            <ActionText onClick={() => setAnchor(true)}>Change</ActionText>
          </>
        ) : (
          <>
            <Typography
              noWrap
              sx={{
                typography: "s2",
                color: "text.subtitle",
                flex: 1,
                minWidth: 0,
              }}
            >
              Not linked
              <InfoTip text="Its Error Feed issues can't be fixed against this agent until a project that traces it is linked." />
            </Typography>
            <ActionText onClick={() => setAnchor(true)}>
              Link a project
            </ActionText>
          </>
        )}
      </Stack>
      {!project && code && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              flex: 1,
              minWidth: 0,
            }}
          >
            No project traces it yet? Add traceAI to the agent&apos;s code, then
            link the project it sends to.
          </Typography>
          <Link
            href={TRACEAI_DOCS_URL}
            target="_blank"
            rel="noopener noreferrer"
            underline="hover"
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.primary",
              flexShrink: 0,
            }}
          >
            traceAI setup
          </Link>
        </Stack>
      )}
      <Menu
        anchorEl={() => rowRef.current}
        open={menuOpen}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 320, mt: 0.5 } } }}
      >
        {options.map((p) => (
          <MenuItem
            key={p.id}
            onClick={() => {
              setAnchor(null);
              link(p.id);
            }}
            sx={{ alignItems: "flex-start", py: 1 }}
          >
            <Box minWidth={0}>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 500 }}>
                {p.name}
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.subtitle" }}
              >
                {["No agent yet", p.traces].filter(Boolean).join(" · ")}
              </Typography>
            </Box>
          </MenuItem>
        ))}
        {!options.length && (
          <MenuItem disabled sx={{ typography: "s3" }}>
            Every project already has its agent
          </MenuItem>
        )}
        {canUnlink && <Divider />}
        {canUnlink && (
          <MenuItem
            onClick={() => {
              setAnchor(null);
              unlink();
            }}
            sx={{ typography: "s2", gap: 1 }}
          >
            <Iconify icon="solar:link-broken-linear" width={16} />
            Unlink this project
          </MenuItem>
        )}
        <Divider />
        <MenuItem
          onClick={() => {
            setAnchor(null);
            openInObserve();
          }}
          sx={{ typography: "s2", gap: 1 }}
        >
          <Iconify
            icon="solar:add-circle-linear"
            width={16}
            sx={{ color: "text.secondary" }}
          />
          New project in Observe
        </MenuItem>
      </Menu>
    </Box>
  );
}
ObserveProjectCard.propTypes = { agent: PropTypes.object.isRequired };
