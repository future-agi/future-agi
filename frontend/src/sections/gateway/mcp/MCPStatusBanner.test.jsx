// The MCP status banner's verdict for each answer the status API can give.
// Every title must be true for the answer it shows: none may say MCP is in
// development, and none may say "not enabled" when the gateway answered that
// MCP is on.
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

import MCPStatusBanner from "./MCPStatusBanner";

const testTheme = createTheme({ palette: palette("light") });

function renderBanner(props) {
  return render(
    <ThemeProvider theme={testTheme}>
      <MCPStatusBanner {...props} />
    </ThemeProvider>,
  );
}

const COUNTS = { sessions: 0, tools: 0, resources: 0, prompts: 0 };

// [case, props, title, text, alert severity class]
const VERDICTS = [
  [
    "MCP on",
    { status: { enabled: true, ...COUNTS, servers: [] } },
    "MCP gateway: Enabled",
    null,
    "Success",
  ],
  [
    "the status request failed",
    { status: undefined, isError: true },
    "MCP gateway: status unknown",
    "MCP status could not be loaded.",
    "Warning",
  ],
  [
    "the gateway could not be reached",
    {
      status: {
        enabled: false,
        reason: "gateway_unreachable",
        ...COUNTS,
        servers: [],
      },
    },
    "MCP gateway: status unknown",
    "The gateway could not be reached, so its MCP status is unknown.",
    "Warning",
  ],
  [
    "MCP turned off on the gateway",
    {
      status: {
        enabled: false,
        reason: "gateway_mcp_disabled",
        ...COUNTS,
        servers: [],
      },
    },
    "MCP gateway: not enabled",
    "MCP is turned off on this gateway.",
    "Warning",
  ],
  [
    "MCP on, and the org has no MCP server",
    { status: { enabled: false, ...COUNTS, servers: [] } },
    "MCP gateway: no server configured",
    "No MCP server is configured for this organization.",
    "Info",
  ],
  [
    "the org has servers and the gateway did not say enabled",
    {
      status: {
        enabled: false,
        ...COUNTS,
        servers: [{ server_id: "alpha", healthy: true }],
      },
    },
    "MCP gateway: not enabled",
    "The gateway did not report MCP as enabled.",
    "Warning",
  ],
  [
    "a reason this page does not know",
    {
      status: {
        enabled: false,
        reason: "something_new",
        ...COUNTS,
        servers: [],
      },
    },
    "MCP gateway: not enabled",
    "The gateway did not report MCP as enabled.",
    "Warning",
  ],
  [
    "an answer with no server list",
    { status: { enabled: false } },
    "MCP gateway: not enabled",
    "The gateway did not report MCP as enabled.",
    "Warning",
  ],
];

describe("MCPStatusBanner", () => {
  it.each(VERDICTS)(
    "says the true verdict when %s",
    (_, props, title, text, severity) => {
      const { container } = renderBanner(props);

      expect(screen.getByText(title)).toBeInTheDocument();
      if (text) expect(screen.getByText(text, { exact: false })).toBeTruthy();
      expect(
        container.querySelector(`.MuiAlert-standard${severity}`),
      ).not.toBeNull();
      expect(container).not.toHaveTextContent(/in development/i);
    },
  );

  it("never says MCP is in development", () => {
    for (const [, props] of VERDICTS) {
      const { container, unmount } = renderBanner(props);
      expect(container).not.toHaveTextContent(
        "MCP gateway: in development on this build",
      );
      unmount();
    }
  });

  it("an org with no server is not told the gateway failed to report MCP", () => {
    renderBanner({ status: { enabled: false, ...COUNTS, servers: [] } });

    expect(screen.queryByText("MCP gateway: not enabled")).toBeNull();
    expect(screen.queryByText(/did not report MCP as enabled/)).toBeNull();
    expect(screen.queryByText(/Reason:/)).toBeNull();
  });

  it("shows the reason code the API gave", () => {
    renderBanner({
      status: {
        enabled: false,
        reason: "gateway_unreachable",
        ...COUNTS,
        servers: [],
      },
    });

    expect(screen.getByText("gateway_unreachable")).toBeInTheDocument();
  });

  it("shows nothing while the status is loading", () => {
    const { container } = renderBanner({ status: undefined, isError: false });

    expect(container).toBeEmptyDOMElement();
  });
});
