import { useMCPReleaseFeatures } from "../../mcp/hooks/useMCPReleaseFeatures";
import { NOT_AVAILABLE_IN_DEPLOYMENT } from "src/sections/omega/shell/agent-infrastructure-nav";
import React, { useId } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { DetailRow, GatewayStatusChip } from "../../components/frame";
import { GATEWAY_MONO } from "../../utils/fonts";
import { denialText, isSignInCredential, toolRuns } from "../catalog-model";
import ServedToolsPanel from "../../mcp/servers/ServedToolsPanel";
import {
  CONNECTOR_HELD_ACCESS,
  CONNECTOR_KEY_ACCESS,
  CONNECTOR_NO_VIRTUAL_SERVERS,
  connectorNoOwnVirtualServer,
} from "../../mcp/servers/connectorKeyAccess";

// Catalogue account facts and historical/declared posture stay separate from
// the runtime-backed served inventory below. The historical tool rules never
// supply a live count or a fallback when the served read fails.
const SHARED = "One shared account for the organization";
const OWN = "Each person's own account";
const OWNERS = "Owners and Admins of this organization";

// Where keys reach an added connector's tools: never through the
// organization's access default; through the connector's own virtual server,
// which applies its approvals. A provider not added yet has no tools for a
// key to reach. A connector this release does not connect has none either:
// the gateway refuses every call to its tools.
function keyAccessText(server, virtualServersOff, held) {
  if (!server) return null;
  if (held) return CONNECTOR_HELD_ACCESS;
  if (virtualServersOff) return CONNECTOR_NO_VIRTUAL_SERVERS;
  return server.virtual_server
    ? CONNECTOR_KEY_ACCESS
    : connectorNoOwnVirtualServer(server.server_id);
}

// The account modes in play: the one the server was added in, else the
// ones the provider can be added in. The API lists a provider only in a
// mode it can run (connector_catalog.plan_for).
function modesOf(provider, server) {
  if (server && isSignInCredential(provider?.credential)) {
    return server.credential_mode === "per_user" ? ["user"] : ["agent"];
  }
  return provider?.account_modes || [];
}

function accountText(modes) {
  if (modes.includes("user")) {
    return modes.includes("agent") ? `${OWN}, or ${SHARED.toLowerCase()}` : OWN;
  }
  return modes.includes("agent") ? SHARED : null;
}

function whoText(provider, modes) {
  if (!isSignInCredential(provider?.credential) || !modes.includes("user")) {
    return OWNERS;
  }
  return modes.includes("agent")
    ? "Owners and Admins add it, for each person's own account or one shared account."
    : "Owners and Admins add it. Each person then signs in with their own account.";
}

// A tool's posture label. An allowed tool whose reason is approval_required
// runs once an Owner or Admin approves the call (MCP tools > Approvals).
const POSTURE_CHIPS = {
  allow: { tone: "healthy", label: "Allowed" },
  approval: { tone: "warning", label: "Needs approval" },
  deny: { tone: "neutral", label: "Blocked" },
};

const READD = "Re-add this app to let its write tools run after an approval.";
// True for every add path: a catalog add stores the tools the app listed, a
// per-person sign-in add the tools the catalog declares.
const STORED_NOTE =
  "These are the tools the app's virtual server names, with the access it stores for each.";

// A tool on the server card carries the access the app's virtual server
// stores (allowed, approval_required, denied or excluded; the last two read
// Blocked); a tool from the catalog, its gateway posture and reason. Both
// name an approval the same way.
function postureOf(tool) {
  if (!toolRuns(tool)) return "deny";
  return (tool.access || tool.reason) === "approval_required"
    ? "approval"
    : "allow";
}

// The line under a tool: what it does where it runs, else why the gateway
// blocks it. A tool on the server card takes them from the tool the catalog
// declares under its name.
function noteOf(tool, declared) {
  if (postureOf(tool) !== "deny") {
    const source = tool.access ? declared || {} : tool;
    return source.title || source.description;
  }
  if (!tool.access) return denialText(tool.reason);
  return declared?.gateway_posture === "deny"
    ? denialText(declared.reason)
    : null;
}

// A tool an access rule on the app's virtual server denies while the catalog
// now lets it run after an approval: the app was added before tool
// approvals. Not a tool the virtual server excludes (access excluded): an
// admin chose that, and re-adding the app would undo it.
function deniedBeforeApprovals(tool, declared) {
  return (
    tool.access === "denied" &&
    declared?.gateway_posture === "allow" &&
    declared.reason === "approval_required"
  );
}

export default function AccessControl({
  appName,
  provider,
  server,
  gatewayId,
  canManage = false,
  held = false,
}) {
  const features = useMCPReleaseFeatures(gatewayId);
  // Shown while the status loads or fails; a switch that is off says so.
  const virtualServers =
    features.shown?.virtualServers ?? features.virtualServers;
  const virtualServersOff = features.off?.virtualServers === true;
  const headingId = useId();
  const listed = Array.isArray(server?.tools);
  const tools = listed ? server.tools : provider?.tools || [];
  const declared = new Map(
    (provider?.tools || []).map((tool) => [tool.name, tool]),
  );
  const total = tools.length;
  const allowed = tools.filter(toolRuns).length;
  const stored = listed && tools.some((tool) => tool.access);
  const stale = (tool) => deniedBeforeApprovals(tool, declared.get(tool.name));
  const readd = tools.some(stale);
  const modes = modesOf(provider, server);
  const account = accountText(modes);
  const virtualServer = virtualServersOff
    ? server?.virtual_server?.slug && NOT_AVAILABLE_IN_DEPLOYMENT
    : virtualServers
      ? server?.virtual_server?.slug
      : null;
  const keyAccess = keyAccessText(server, virtualServersOff, held);

  return (
    <Box
      component="section"
      aria-labelledby={headingId}
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Typography
        id={headingId}
        component="h3"
        typography="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
      >
        Access control
      </Typography>
      {keyAccess && (
        <Typography typography="s2" color="text.secondary">
          {keyAccess}
        </Typography>
      )}
      <Box>
        <DetailRow label="Who can connect">
          {whoText(provider, modes)}
        </DetailRow>
        {account && <DetailRow label="Account">{account}</DetailRow>}
        {virtualServer && (
          <DetailRow label="Virtual server" mono>
            {virtualServer}
          </DetailRow>
        )}
        {/* Opens the tool-rules part, set apart from the account facts, when
            there is a part to open: tools, or a provider whose declaration
            the Tools row explains. */}
        {(total > 0 || provider) && (
          <Typography
            component="h4"
            typography="s2"
            fontWeight="fontWeightMedium"
            color="text.secondary"
            sx={{ mt: 1.5, mb: 0.5 }}
          >
            Historical / declared posture — not live
          </Typography>
        )}
        {total > 0 && (
          <DetailRow label="Tools">{`${allowed} of ${total} tools allowed`}</DetailRow>
        )}
        {total === 0 && provider && (
          <DetailRow label="Tools">
            {listed
              ? "No tools were recorded when this app was added."
              : "This provider does not declare its tools in the catalog."}{" "}
            An empty list does not establish read-only access or approval
            requirements. Review the served tools and set a tool policy on the
            app&apos;s virtual server.
          </DetailRow>
        )}
      </Box>
      {total > 0 && (
        <Box
          component="ul"
          sx={{
            listStyle: "none",
            m: 0,
            p: 0,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {tools.map((tool, index) => {
            const chip = POSTURE_CHIPS[postureOf(tool)];
            const note = noteOf(tool, declared.get(tool.name));
            return (
              <Box
                component="li"
                key={tool.name}
                sx={{
                  display: "flex",
                  gap: 1,
                  alignItems: "flex-start",
                  justifyContent: "space-between",
                  px: 1.5,
                  py: 1,
                  borderTop: index ? 1 : 0,
                  borderColor: "divider",
                }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <Typography
                    typography="s2_1"
                    color="text.primary"
                    sx={{ fontFamily: GATEWAY_MONO, overflowWrap: "anywhere" }}
                  >
                    {tool.name}
                  </Typography>
                  {note && (
                    <Typography typography="s2" color="text.secondary">
                      {note}
                    </Typography>
                  )}
                </Box>
                <GatewayStatusChip
                  tone={chip.tone}
                  label={chip.label}
                  showIcon={false}
                />
              </Box>
            );
          })}
        </Box>
      )}
      {(virtualServers || virtualServersOff) && total > 0 && !listed && (
        <Typography typography="s2" color="text.secondary">
          These are the catalog&apos;s rules, set on the app&apos;s virtual
          server when it is added. A later edit to that virtual server is not
          shown here.
        </Typography>
      )}
      {readd && (
        <Typography typography="s2" color="text.secondary">
          {READD}
        </Typography>
      )}
      {(virtualServers || virtualServersOff) && total > 0 && listed && (
        <Typography typography="s2" color="text.secondary">
          {stored
            ? STORED_NOTE
            : `These are the tools ${appName} listed when it was added, and the gateway's rule for each, set on the app's virtual server. A later edit to that virtual server is not shown here.`}
        </Typography>
      )}
      {canManage && gatewayId && server?.virtual_server && (
        <ServedToolsPanel
          key={server.server_id}
          gatewayId={gatewayId}
          serverId={server.server_id}
          virtualServer={server.virtual_server.id || server.virtual_server.slug}
        />
      )}
    </Box>
  );
}

AccessControl.propTypes = {
  appName: PropTypes.string,
  gatewayId: PropTypes.string,
  canManage: PropTypes.bool,
  // A connector this release does not connect: its tools are refused.
  held: PropTypes.bool,
  provider: PropTypes.shape({
    account_modes: PropTypes.arrayOf(PropTypes.string),
    tools: PropTypes.array,
  }),
  server: PropTypes.shape({
    credential_mode: PropTypes.string,
    server_id: PropTypes.string,
    virtual_server: PropTypes.shape({
      id: PropTypes.string,
      slug: PropTypes.string,
    }),
    tools: PropTypes.array,
  }),
};
