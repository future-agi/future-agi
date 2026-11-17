import React from "react";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { OmegaError } from "src/sections/omega/shared/omegaStates";
import { hydrateStoredFilterList } from "src/api/contracts/filter-contract";

import { resetAccessTokensForTests } from "../lib/accessTokens";
import { ID, detailView, fakeApi, renderDeployments } from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: DeploymentDetailPage } = await import(
  "../DeploymentDetailPage"
);

const PROJECT = "7f9c2b4e-1d2a-4c55-9e1f-0a1b2c3d4e5f";
const at = `/dashboard/deployments/${ID}`;

// EE's `observe` view (PX-OBSERVE-WIRE): the deployment's own project once linked.
const observe = (overrides = {}) => ({
  projectName: "deploy/greeter",
  captureContent: true,
  projectId: PROJECT,
  link: "linked",
  linkError: null,
  alertsDeferred: null,
  ...overrides,
});

const withObserve = (value) =>
  fakeApi({ get: vi.fn(async () => detailView({ observe: value })) });

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
  resetAccessTokensForTests();
});
afterEach(() => vi.restoreAllMocks());

describe("the deployment's Observe card", () => {
  it.each(["light", "dark"])(
    "uses the warning foreground token for failure text in %s mode (FA-DEP-UX-08)",
    async (mode) => {
      const themePalette = palette(mode);
      const theme = createTheme({ palette: themePalette, typography });
      renderDeployments(
        <ThemeProvider theme={theme}>
          <DeploymentDetailPage />
        </ThemeProvider>,
        {
          api: withObserve(
            observe({
              projectId: null,
              link: "failing",
              linkError: { message: "The Observe link failed." },
            }),
          ),
          route: at,
        },
      );
      expect(await screen.findByTestId("observe-link-state")).toHaveStyle({
        color: themePalette.status.warning.fg,
      });
    },
  );

  it("opens the deployment's own Observe project in one click, scoped to it, with the way back", async () => {
    renderDeployments(<DeploymentDetailPage />, {
      api: withObserve(observe()),
      route: at,
    });
    const open = await screen.findByTestId("open-in-observe");
    expect(open.tagName).toBe("A");
    const href = new URL(open.getAttribute("href"), window.location.origin);
    expect(href.pathname).toBe(`/dashboard/observe/${PROJECT}/llm-tracing`);
    expect(href.searchParams.get("observeReturnTo")).toBe(at);
    expect(href.searchParams.get("observeFrom")).toBe("greeter in Deployments");
    // The filter Observe hydrates into removable chips: this deployment only.
    const rows = hydrateStoredFilterList(
      JSON.parse(href.searchParams.get("observeLinkFilter")),
    );
    expect(rows.map((row) => row.filter_config?.filter_value)).toEqual([ID]);
    expect(screen.getByTestId("observe-card")).toHaveTextContent(
      "deploy/greeter",
    );

    await userEvent.click(open);
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/observe/${PROJECT}/llm-tracing?`,
    );
  });

  it("says why traces do not reach Observe, in EE's words, with no link and no promise of its own", async () => {
    renderDeployments(<DeploymentDetailPage />, {
      api: withObserve(
        observe({
          projectId: null,
          link: "failing",
          linkError: {
            code: "not_admitted",
            message:
              "Future AGI has not admitted this organization to Deployments yet; traces start once it does.",
          },
        }),
      ),
      route: at,
    });
    const state = await screen.findByTestId("observe-link-state");
    expect(state).toHaveAttribute("data-link", "failing");
    expect(state).toHaveTextContent(
      "Traces do not reach Observe: Future AGI has not admitted this organization to Deployments yet; traces start once it does.",
    );
    expect(state).not.toHaveTextContent("retried by itself");
    expect(screen.queryByTestId("open-in-observe")).toBeNull();
  });

  it("never promises a retry for a link that cannot move, and offers no button to the old project", async () => {
    renderDeployments(<DeploymentDetailPage />, {
      api: withObserve(
        observe({
          projectId: null,
          link: "failing",
          linkError: {
            code: "linked_elsewhere",
            message:
              "Future AGI has this deployment linked in another Future AGI workspace, and a link cannot move between workspaces. Its traces stay refused while this workspace is mapped elsewhere; mapping it back restores the link, and a new deployment links in the workspace mapped now.",
          },
        }),
      ),
      route: at,
    });
    const state = await screen.findByTestId("observe-link-state");
    expect(state).toHaveAttribute("data-link", "failing");
    expect(state).toHaveTextContent("a link cannot move between workspaces");
    expect(state).not.toHaveTextContent("retried");
    expect(screen.queryByTestId("open-in-observe")).toBeNull();
  });

  it("says a link is being made, or that this server does not link, and never shows a dead button", async () => {
    renderDeployments(<DeploymentDetailPage />, {
      api: withObserve(observe({ projectId: null, link: "pending" })),
      route: at,
    });
    expect(await screen.findByTestId("observe-link-state")).toHaveTextContent(
      "Linking this deployment to its own Observe project",
    );
    expect(screen.queryByTestId("open-in-observe")).toBeNull();
  });

  it("explains a server that does not link deployments to Observe", async () => {
    renderDeployments(<DeploymentDetailPage />, {
      api: withObserve(observe({ projectId: null, link: "off" })),
      route: at,
    });
    expect(await screen.findByTestId("observe-link-state")).toHaveTextContent(
      "This server does not send deployment traces to Observe",
    );
    expect(screen.queryByTestId("open-in-observe")).toBeNull();
  });

  it("shows nothing without a relay, and nothing for another workspace's deployment", async () => {
    renderDeployments(<DeploymentDetailPage />, {
      api: withObserve(null),
      route: at,
    });
    await screen.findByTestId("overview-url");
    expect(screen.queryByTestId("observe-card")).toBeNull();
    const other = fakeApi({
      get: vi.fn(async () => {
        throw new OmegaError({ state: "not-found", status: 404 });
      }),
    });
    renderDeployments(<DeploymentDetailPage />, { api: other, route: at });
    expect(await screen.findByText("Deployment not found")).toBeInTheDocument();
    expect(screen.queryAllByTestId("open-in-observe")).toHaveLength(0);
  });

  it("notes deferred default alerts beside a working link", async () => {
    renderDeployments(<DeploymentDetailPage />, {
      api: withObserve(
        observe({ alertsDeferred: { reason: "monitor_limit_reached" } }),
      ),
      route: at,
    });
    expect(await screen.findByTestId("observe-card")).toHaveTextContent(
      "Its two default alerts start once your plan has room for them.",
    );
    expect(screen.getByTestId("open-in-observe")).toBeInTheDocument();
  });
});
