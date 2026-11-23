import { createElement, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Divider,
  IconButton,
  Menu,
  MenuItem,
  Tooltip,
  Typography,
} from "@mui/material";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  connectedCloudAgents,
  connectedCodingAgent,
  dispatchToCloudAgent,
  handBriefTo,
  useIntegrations,
} from "src/sections/simulate-v2/_mock/integrations";
import BrandMark from "src/sections/simulate-v2/integrations/BrandMark";
import { useIssueExport } from "./issueExport";
import { useIssueBrief } from "./issueBrief";

/* A menu section's label: 11/500, tertiary, sentence case. */
function SectionLabel({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 500,
        color: "text.tertiary",
        px: 1.5,
        pt: 0.75,
        pb: 0.25,
      }}
    >
      {children}
    </Typography>
  );
}
SectionLabel.propTypes = { children: PropTypes.node };

function Item({ icon, mark, label, note, onClick, disabled }) {
  return (
    <MenuItem onClick={onClick} disabled={disabled} sx={{ gap: 1 }}>
      {mark || (
        <Iconify
          icon={icon}
          width={16}
          sx={{ flexShrink: 0, color: "text.secondary" }}
        />
      )}
      <Typography sx={{ typography: "s2", flex: 1 }}>{label}</Typography>
      {note && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", ml: 2 }}>
          {note}
        </Typography>
      )}
    </MenuItem>
  );
}
Item.propTypes = {
  icon: PropTypes.string,
  mark: PropTypes.node,
  label: PropTypes.node,
  note: PropTypes.node,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

/**
 * The issue header's ⋯ menu: everything in the header that isn't the one
 * primary action. Link and ID, Share, the two exports, and handing the
 * issue to a coding agent (Open in …, Dispatch to …, Copy for coding agent).
 * Status is changed in the right rail.
 */
export default function IssueActionsMenu({ issue, agent, rootCause }) {
  const navigate = useNavigate();
  const [anchor, setAnchor] = useState(null);
  const { exportTraces, exportAnalysis, hasTraces } = useIssueExport(
    issue,
    rootCause,
  );
  const { brief, handOff } = useIssueBrief(issue, agent);
  const integrations = useIntegrations();
  const coder = connectedCodingAgent(integrations);
  const clouds = connectedCloudAgents(integrations);
  const target = { kind: "issue", issueId: issue?.cluster_id };

  const close = () => setAnchor(null);
  const run = (fn) => () => {
    close();
    fn();
  };
  const copy = (text, msg) =>
    navigator.clipboard
      ?.writeText(text)
      .then(() => enqueueSnackbar(msg, { variant: "success" }))
      .catch(() => {});

  const share = () => {
    const url = window.location.href;
    const title = issue?.error?.name || issue?.cluster_id;
    if (navigator.share) {
      navigator.share({ title, url }).catch(() => {});
      return;
    }
    copy(`${title} (${issue?.cluster_id})\n${url}`, "Copied the issue and its link to share");
  };

  /* Where the coding agent's attempts land, one click away on the toast. */
  const landed = (message, where) =>
    enqueueSnackbar(message, {
      variant: "success",
      autoHideDuration: where?.href ? 12000 : 6000,
      ...(where?.href && {
        action: (snack) =>
          createElement(
            Button,
            {
              size: "small",
              color: "inherit",
              onClick: () => {
                closeSnackbar(snack);
                navigate(
                  where.href,
                  where.state ? { state: where.state } : undefined,
                );
              },
            },
            where.label || "See its attempts",
          ),
      }),
    });
  const openInCoder = () => {
    handBriefTo({ agentId: coder.id, target, brief: brief() });
    landed(
      `Ready in ${coder.label}. Ask it to "fix ${issue?.cluster_id}" — it fetches the brief itself.`,
      handOff(coder),
    );
  };
  const dispatchTo = (c) => {
    dispatchToCloudAgent({ agentId: c.id, target, brief: brief() });
    landed(
      `Dispatched to ${c.label}. Its change comes back as a candidate for Future AGI to check.`,
      handOff(c),
    );
  };

  return (
    <>
      <Tooltip arrow title="More actions">
        <IconButton
          size="small"
          aria-label="More actions"
          aria-haspopup="menu"
          aria-expanded={!!anchor}
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ color: "text.subtitle" }}
        >
          <Iconify icon="solar:menu-dots-bold" width={20} />
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 260, mt: 0.5 } } }}
      >
        <Item
          icon="solar:link-linear"
          label="Copy link"
          onClick={run(() => copy(window.location.href, "Link copied"))}
        />
        <Item
          icon="solar:copy-linear"
          label="Copy issue ID"
          note={issue?.cluster_id}
          onClick={run(() => copy(issue?.cluster_id, "Issue ID copied"))}
        />
        <Item
          icon="solar:share-linear"
          label="Share"
          onClick={run(share)}
        />
        <Divider sx={{ my: 0.5 }} />
        <SectionLabel>Export</SectionLabel>
        <Item
          icon="solar:document-text-linear"
          label="Traces"
          note="CSV"
          disabled={!hasTraces}
          onClick={run(exportTraces)}
        />
        <Item
          icon="solar:clipboard-text-linear"
          label="Analysis"
          note="Markdown"
          onClick={run(exportAnalysis)}
        />
        <Divider sx={{ my: 0.5 }} />
        <SectionLabel>Coding agent</SectionLabel>
        {coder && (
          <Item
            mark={
              <Box sx={{ display: "inline-flex", width: 16, justifyContent: "center" }}>
                <BrandMark brand={coder.id} size={27} tile={false} />
              </Box>
            }
            label={`Open in ${coder.label}`}
            onClick={run(openInCoder)}
          />
        )}
        {clouds.map((c) => (
          <Item
            key={c.id}
            mark={
              <Box sx={{ display: "inline-flex", width: 16, justifyContent: "center" }}>
                <BrandMark brand={c.id} size={27} tile={false} />
              </Box>
            }
            label={`Dispatch to ${c.short || c.label}`}
            onClick={run(() => dispatchTo(c))}
          />
        ))}
        <Item
          icon="solar:copy-linear"
          label="Copy for coding agent"
          onClick={run(() => {
            try {
              navigator.clipboard?.writeText(brief());
              enqueueSnackbar("Brief copied", { variant: "success" });
            } catch {
              /* clipboard unavailable — no-op */
            }
          })}
        />
        {(coder || clouds.length > 0) && (
          <Item
            icon="solar:settings-linear"
            label="IDE settings"
            onClick={run(() =>
              navigate(`${paths.dashboard.settings.integrations}/coding-agents`),
            )}
          />
        )}
      </Menu>
    </>
  );
}

IssueActionsMenu.propTypes = {
  issue: PropTypes.object,
  agent: PropTypes.object,
  rootCause: PropTypes.object,
};
