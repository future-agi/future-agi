import { createElement, Fragment } from "react";
import Box from "@mui/material/Box";

import { GATEWAY_MONO } from "../../utils/fonts";

// Where keys reach the tools of a server added from the connector catalog.
// The gateway leaves such a server out of what the organization's access
// default serves, so keys reach its tools through the connector's own
// virtual server, which applies its approvals.
export const CONNECTOR_KEY_ACCESS =
  "Keys reach this connector's tools through its own virtual server, which applies its approvals, and never through the organization default. Copy the endpoint from MCP > Virtual servers.";
// Shown when no virtual server under the connector's slug has it as a
// member: that virtual server was deleted, or edited to leave it out. The
// slug is the connector's server id (every add path writes its own virtual
// server under that id), set in code face as the Slug column of MCP >
// Virtual servers shows it. If that virtual server is still there, adding
// the connector back to it restores it, with the approvals and denied tools
// it holds; after a disconnect, connecting again is refused while it keeps
// the slug. If it was deleted, disconnecting and connecting again writes it
// back with the catalog's approvals and denied tools.
export function connectorNoOwnVirtualServer(slug) {
  return createElement(
    Fragment,
    null,
    "This connector's own virtual server was deleted or no longer has it as a member, so its approvals no longer apply anywhere. If MCP > Virtual servers still lists ",
    createElement(
      Box,
      {
        component: "code",
        sx: { fontFamily: GATEWAY_MONO, overflowWrap: "anywhere" },
      },
      slug,
    ),
    ", add the connector back to it. If not, disconnect the connector and connect it again.",
  );
}
// With the deployment's virtual-server switch off, the gateway serves no
// virtual server, so nothing serves a connector's tools.
export const CONNECTOR_NO_VIRTUAL_SERVERS =
  "Virtual servers are not available in this deployment, so no key can reach this connector's tools.";
// A connector this release does not connect (Microsoft's hold, or a pilot
// the gateway does not serve): the gateway refuses every call to its tools,
// whatever its virtual server says.
export const CONNECTOR_HELD_ACCESS =
  "No key can reach this connector's tools: connections to this app are not available in this release.";
export const CONNECTOR_NOT_ON_ORG_DEFAULT =
  "Connector tools are not served on the organization default. Keys reach a connector's tools through its own virtual server, which applies its approvals.";
