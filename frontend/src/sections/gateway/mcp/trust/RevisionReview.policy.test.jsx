import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import axios from "src/utils/axios";
import RevisionReview from "./RevisionReview";
import { oldHash, revision } from "./revisionFixture";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: { gateway: {} },
}));

const REVISIONS = "/agentcc/mcp/revisions/";
const CONFIG = "/agentcc/gateways/gw-1/config/";
const UPDATE = "/agentcc/gateways/gw-1/update-config/";
const scope = { timeout: 30000, headers: { "X-Organization-Id": "org-a" } };
const theme = createTheme({ palette: palette("light"), typography });
let client;
let config;
let rows;

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  rows = [revision];
  config = {
    mcp: {
      pinning: { pins: { "orders/lookup": oldHash, "orders/other": oldHash } },
    },
  };
  vi.mocked(axios.get).mockImplementation(async (url, options) => {
    if (url === REVISIONS)
      return {
        data: {
          status: true,
          result: {
            revisions: options.params.status === "pending" ? rows : [],
            gateway_synced: true,
          },
        },
      };
    if (url === CONFIG) return { data: { status: true, result: config } };
    throw new Error(`Unexpected GET ${url}`);
  });
  vi.mocked(axios.post).mockImplementation(async (url) => {
    if (url.endsWith("/reject/") || url.endsWith("/approve/")) {
      const row = rows[0];
      rows = [];
      return {
        data: {
          status: true,
          result: {
            ...row,
            status: url.endsWith("/reject/") ? "rejected" : "approved",
            gateway_synced: true,
          },
        },
      };
    }
    if (url === UPDATE)
      return {
        data: { status: true, result: { version: 2, gateway_synced: true } },
      };
    throw new Error(`Unexpected POST ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

async function openReview(action) {
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <RevisionReview gatewayId="gw-1" organizationId="org-a" canEdit />
      </ThemeProvider>
    </QueryClientProvider>,
  );
  const row = await screen.findByRole("article", {
    name: "orders / lookup revision",
  });
  const button = within(row).getByRole(
    "button",
    /** @type {import("@testing-library/react").ByRoleOptions} */ ({
      name: action,
      exact: true,
    }),
  );
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  return screen.findByRole("dialog");
}

const cases = [
  {
    name: "warn-mode changed tool",
    policy: { mode: "warn" },
    reason: "changed",
    held: false,
    disclosure: /warn mode.*still serves changed and new tools/i,
  },
  {
    name: "warn-mode new tool",
    policy: { mode: "warn" },
    reason: "new",
    held: false,
    disclosure: /warn mode.*still serves changed and new tools/i,
  },
  {
    name: "quarantine with allow-new tool",
    policy: { mode: "quarantine", new_tools: "allow" },
    reason: "new",
    held: false,
    disclosure: /new tools are allowed.*still serves unpinned new tools/i,
  },
  {
    name: "default-mode allow-new tool",
    policy: { new_tools: "allow" },
    reason: "new",
    held: false,
    disclosure: /new tools are allowed.*still serves unpinned new tools/i,
  },
  {
    name: "off-mode changed tool",
    policy: { mode: "off" },
    reason: "changed",
    held: false,
    disclosure: /tool pinning is off/i,
  },
  {
    name: "default-policy changed tool",
    policy: {},
    reason: "changed",
    held: true,
  },
  { name: "default-policy new tool", policy: {}, reason: "new", held: true },
  {
    name: "unknown-mode changed tool",
    policy: { mode: "unexpected", new_tools: "allow" },
    reason: "changed",
    held: true,
  },
  {
    name: "unknown-policy new tool",
    policy: { mode: "WARN", new_tools: "ALLOW" },
    reason: "new",
    held: true,
  },
  {
    name: "allow-new does not release a changed tool",
    policy: { mode: "quarantine", new_tools: "allow" },
    reason: "changed",
    held: true,
    disclosure: /new tools are allowed.*still serves unpinned new tools/i,
  },
];

describe("revision review policy copy", () => {
  it.each(cases)(
    "reject is truthful for $name",
    async ({ policy, reason, held, disclosure }) => {
      Object.assign(config.mcp.pinning, policy);
      if (reason === "new") {
        delete config.mcp.pinning.pins["orders/lookup"];
        rows = [
          {
            ...revision,
            reason,
            previous_revision: "",
            previous_definition: null,
          },
        ];
      }
      const originalConfig = JSON.stringify(config);
      const dialog = await openReview("Reject");
      if (disclosure)
        expect.soft(screen.queryByText(disclosure)).toBeInTheDocument();
      if (held) {
        expect.soft(dialog).toHaveTextContent(/\b(stays|remains) held\b/);
        expect.soft(dialog).not.toHaveTextContent(/still serves/i);
      } else {
        expect.soft(dialog).not.toHaveTextContent(/\bheld\b/);
        expect.soft(dialog).toHaveTextContent(/still serves this tool/i);
      }
      expect(axios.post).not.toHaveBeenCalled();
      fireEvent.click(
        within(dialog).getByRole(
          "button",
          /** @type {import("@testing-library/react").ByRoleOptions} */ ({
            name: "Reject",
            exact: true,
          }),
        ),
      );
      const notice = await screen.findByText(/^Revision rejected\./);
      if (held) {
        expect.soft(notice).toHaveTextContent(/remains held/);
        expect.soft(notice).not.toHaveTextContent(/still serves/i);
      } else {
        expect.soft(notice).not.toHaveTextContent(/\bheld\b/);
        expect.soft(notice).toHaveTextContent(/still serves this tool/i);
      }
      await screen.findByText("No pending tool revisions.");
      expect(vi.mocked(axios.post).mock.calls).toEqual([
        [`${REVISIONS}17/reject/`, {}, scope],
      ]);
      expect(JSON.stringify(config)).toBe(originalConfig);
    },
  );

  it.each([
    { name: "warn", policy: { mode: "warn" }, held: false },
    { name: "off", policy: { mode: "off" }, held: false },
    { name: "default quarantine", policy: {}, held: true },
    {
      name: "allow-new quarantine",
      policy: { new_tools: "allow" },
      held: true,
    },
  ])(
    "pin confirmation respects $name after adding the pin",
    async ({ policy, held }) => {
      Object.assign(config.mcp.pinning, policy);
      delete config.mcp.pinning.pins["orders/lookup"];
      // Re-pin a previously accepted definition of a tool now reported as new.
      rows = [{ ...revision, reason: "new" }];
      const originalConfig = JSON.stringify(config);
      const dialog = await openReview("Pin accepted revision");
      if (held) {
        expect.soft(dialog).toHaveTextContent(/held/);
      } else {
        expect.soft(dialog).not.toHaveTextContent(/\bheld\b/);
        expect
          .soft(dialog)
          .toHaveTextContent(/still serves a different upstream definition/i);
      }
      fireEvent.click(
        within(dialog).getByRole(
          "button",
          /** @type {import("@testing-library/react").ByRoleOptions} */ ({
            name: "Pin",
            exact: true,
          }),
        ),
      );
      expect(
        await screen.findByText("Accepted revision pinned."),
      ).toBeVisible();
      expect(vi.mocked(axios.post).mock.calls).toEqual([
        [
          UPDATE,
          { mcp: { pinning: { pins: { "orders/lookup": oldHash } } } },
          scope,
        ],
      ]);
      expect(JSON.stringify(config)).toBe(originalConfig);
    },
  );

  it.each(cases.filter(({ held }) => !held))(
    "approve does not claim to release an already served $name",
    async ({ policy, reason }) => {
      Object.assign(config.mcp.pinning, policy);
      if (reason === "new") {
        delete config.mcp.pinning.pins["orders/lookup"];
        rows = [
          {
            ...revision,
            reason,
            previous_revision: "",
            previous_definition: null,
          },
        ];
      }
      const dialog = await openReview("Approve");
      expect.soft(dialog).not.toHaveTextContent(/become available|\bheld\b/);
      expect.soft(dialog).toHaveTextContent(/updates the pin/i);
      fireEvent.click(
        within(dialog).getByRole(
          "button",
          /** @type {import("@testing-library/react").ByRoleOptions} */ ({
            name: "Approve",
            exact: true,
          }),
        ),
      );
      expect(
        await screen.findByText("Revision approved and pinned."),
      ).toBeVisible();
      expect(vi.mocked(axios.post).mock.calls).toEqual([
        [`${REVISIONS}17/approve/`, {}, scope],
      ]);
    },
  );
});
