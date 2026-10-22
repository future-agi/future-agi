import PropTypes from "prop-types";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { useAgentImprovementFlag } from "../_mock/featureFlags";
import { issueFixNav, useIssueFix } from "./issueFix";

const ago = (iso) => {
  const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
};

/**
 * An issue that came back after its fix was released. Says which change was
 * meant to fix it — the version, how it shipped, the improvement that proved
 * it — so the reopen is a lead, not just a status. Fix again starts a new
 * improvement; the old one stays as the record of what didn't hold.
 */
export default function ReopenedBanner({ issue, onFixAgain }) {
  const navigate = useNavigate();
  const { status, agent, gate } = useIssueFix(issue);
  const improving = useAgentImprovementFlag();
  if (!improving || status?.key !== "reopened" || !status.change) return null;
  const c = status.change;
  /* Same hold as the header's Fix: a disconnected or archived agent, or one
     that's Coming soon — said on hover. Live-test consent isn't one: the Fix
     drawer asks for it before anything runs. */
  const blockedWhy = gate && gate.key !== "consent" ? `${gate.title}. ${gate.body}` : null;
  return (
    <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}>
      <Iconify icon="mdi:restore" width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {c.reopenedBy
            ? `Reopened by ${c.reopenedBy} ${ago(c.seenAt)} — the fix on agent ${c.version} had been released ${ago(c.releasedAt)}`
            : `Reopened — seen again on agent ${c.version}, released ${ago(c.releasedAt)}`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {c.reopenedBy
            ? `The change that was meant to fix it: ${c.name}${c.via ? `, shipped as ${c.via}` : ""}. Fix again starts a new improvement from the version in production; this one keeps its page.`
            : `The change that was meant to fix it: ${c.name}${c.via ? `, shipped as ${c.via}` : ""}. Its scenarios passed in simulation, so production is doing something they don't capture.`}
        </Typography>
      </Box>
      <Button
        size="small" variant="outlined"
        onClick={() => navigate(...issueFixNav(status, issue?.cluster_id))}
        sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
      >
        View fix
      </Button>
      {/* No agent linked: the header's Connect agent to fix is the way on. */}
      {agent && (
        <Tooltip arrow title={blockedWhy || ""}>
          <span>
            <Button size="small" variant="contained" color="primary" disabled={!!blockedWhy} onClick={onFixAgain} sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}>
              Fix again
            </Button>
          </span>
        </Tooltip>
      )}
    </Stack>
  );
}

ReopenedBanner.propTypes = { issue: PropTypes.object, onFixAgain: PropTypes.func };
