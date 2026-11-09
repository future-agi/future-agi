import React from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";

import { deploymentKeys } from "../api/queries";
import { pill } from "../components/StatusChip";
import {
  ID,
  detailView,
  fakeApi,
  renderDeployments,
  summaryOf,
} from "./helpers";
import { failRefetch, refetchErrors } from "./refetchFailure";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: DeploymentsListPage } = await import("../DeploymentsListPage");

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
});

describe("Deployments list", () => {
  it("links the deployment name without a second row tab stop (FA-DEP-UX-06)", async () => {
    renderDeployments(<DeploymentsListPage />);
    const [row] = await screen.findAllByTestId("deployment-row");
    const link = within(row).getByRole("link", { name: "greeter" });
    expect(link).toHaveAttribute("href", `/dashboard/deployments/${ID}`);
    expect(link.tabIndex).toBe(0);
    expect(row.tabIndex).toBe(-1);
    await userEvent.click(link);
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/deployments/${ID}`,
    );
  });

  it("keeps one card tab stop and a non-shrinking status on narrow screens (FA-DEP-UX-06/07)", async () => {
    const original = window.matchMedia;
    window.matchMedia = (query) => ({
      matches: /max-width/.test(query),
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    });
    try {
      renderDeployments(<DeploymentsListPage />);
      const [card] = await screen.findAllByTestId("deployment-row");
      const link = within(card).getByRole("link", { name: "greeter" });
      expect(link).toHaveAttribute("href", `/dashboard/deployments/${ID}`);
      expect(link.tabIndex).toBe(-1);
      expect(card.tabIndex).toBe(0);
      expect(within(card).getByTestId("deployment-status")).toHaveStyle({
        flexShrink: "0",
      });
    } finally {
      window.matchMedia = original;
    }
  });

  it("bounds only status pills and gives their ellipsized labels a title (FA-DEP-UX-07)", async () => {
    renderDeployments(<DeploymentsListPage />);
    const [row] = await screen.findAllByTestId("deployment-row");
    // HealthChip and QuietTag share pill, but must not inherit clipping.
    expect(pill).not.toHaveProperty("maxWidth");
    expect(pill).not.toHaveProperty("overflow");
    expect(within(row).getByTestId("deployment-status")).toHaveStyle({
      maxWidth: "100%",
      overflow: "hidden",
    });
    const label = within(row).getByText("Running on this host");
    expect(label).toHaveStyle({ overflow: "hidden", textOverflow: "ellipsis" });
    expect(label).toHaveAttribute("title", "Running on this host");
  });

  it("shows name, status, live revision, URL and update time for each deployment", async () => {
    const building = summaryOf(
      detailView({
        id: "dep-ffffffffffffffff",
        name: "triage",
        status: "building",
        revision: 1,
        liveRevision: null,
        url: null,
        studioUrl: null,
      }),
    );
    const api = fakeApi({
      list: vi.fn(async () => [summaryOf(detailView()), building]),
    });
    renderDeployments(<DeploymentsListPage />, { api });
    const rows = await screen.findAllByTestId("deployment-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("greeter")).toBeInTheDocument();
    // Only this host can reach it (EE urlReach "deployment-host"): "Running
    // on this host", and its address as text, never a link (DEMO-FIX-2 item 1).
    expect(within(rows[0]).getByTestId("deployment-status")).toHaveTextContent(
      "Running on this host",
    );
    expect(within(rows[0]).getByText("Revision 2")).toBeInTheDocument();
    const url = within(rows[0]).getByTestId("deployment-url");
    expect(url).toHaveTextContent("127.0.0.1:55408");
    expect(url.tagName).not.toBe("A");
    expect(url).not.toHaveAttribute("href");
    expect(url).toHaveAttribute(
      "title",
      "Reachable from the deployment host only.",
    );
    expect(within(rows[0]).getByText("2 min ago")).toBeInTheDocument();
    expect(within(rows[1]).getByTestId("deployment-status")).toHaveTextContent(
      "Building",
    );
    expect(within(rows[1]).queryByTestId("deployment-url")).toBeNull();
  });

  it("has no link to a host-only deployment's address anywhere in the list, at either width (0 Live links that open a key page)", async () => {
    const original = window.matchMedia;
    for (const width of [1280, 390]) {
      window.matchMedia = (query) => ({
        matches: width < 900 && /max-width/.test(query),
        media: query,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      });
      const { unmount } = renderDeployments(<DeploymentsListPage />);
      await screen.findAllByTestId("deployment-row");
      const links = [...document.querySelectorAll("a[href]")].map((a) =>
        a.getAttribute("href"),
      );
      expect(
        links.filter((href) => /127\.0\.0\.1|localhost/.test(href)),
      ).toEqual([]);
      expect(screen.queryByText("Live", { exact: true })).toBeNull();
      unmount();
    }
    window.matchMedia = original;
  });

  it("still says Live and links the address of a deployment with a network address", async () => {
    const remote = summaryOf(
      detailView({
        url: "https://greeter.agents.example.com",
        studioUrl: "https://greeter.agents.example.com/",
        urlReach: "network",
        urlNote: null,
      }),
    );
    renderDeployments(<DeploymentsListPage />, {
      api: fakeApi({ list: vi.fn(async () => [remote]) }),
    });
    const [row] = await screen.findAllByTestId("deployment-row");
    expect(within(row).getByTestId("deployment-status")).toHaveTextContent(
      "Live",
    );
    expect(within(row).getByTestId("deployment-url")).toHaveAttribute(
      "href",
      "https://greeter.agents.example.com",
    );
  });

  it("judges a reply without urlReach (an older EE) by its address: loopback is host-only", async () => {
    const {
      urlReach: _reach,
      urlNote: _note,
      ...older
    } = summaryOf(detailView());
    renderDeployments(<DeploymentsListPage />, {
      api: fakeApi({ list: vi.fn(async () => [older]) }),
    });
    const [row] = await screen.findAllByTestId("deployment-row");
    expect(within(row).getByTestId("deployment-status")).toHaveTextContent(
      "Running on this host",
    );
    expect(within(row).getByTestId("deployment-url")).not.toHaveAttribute(
      "href",
    );
  });

  it("opens a deployment's page from its row", async () => {
    renderDeployments(<DeploymentsListPage />);
    await userEvent.click((await screen.findAllByTestId("deployment-row"))[0]);
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/deployments/${ID}`,
    );
  });

  it("is empty with one way forward when nothing is deployed", async () => {
    renderDeployments(<DeploymentsListPage />, {
      api: fakeApi({ list: vi.fn(async () => []) }),
    });
    const empty = await screen.findByTestId("deployments-empty");
    expect(empty).toHaveTextContent("No deployments yet");
    await userEvent.click(within(empty).getByTestId("new-deployment"));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/deployments/new",
    );
  });

  it("shows a malformed deployment list as an error, never No deployments yet", async () => {
    const api = fakeApi({
      list: vi.fn().mockRejectedValue(
        new OmegaError({
          state: "error",
          status: 200,
          code: "malformed_response",
        }),
      ),
    });
    renderDeployments(<DeploymentsListPage />, { api });
    const error = await screen.findByTestId("deployment-error-state");
    expect(error).toHaveTextContent("Omega could not complete the request.");
    expect(screen.queryByText("No deployments yet")).not.toBeInTheDocument();
    expect(screen.queryByTestId("deployments-empty")).not.toBeInTheDocument();
    expect(api.list).toHaveBeenCalledTimes(1);
    api.list.mockResolvedValue([]);
    await userEvent.click(
      within(error).getByRole("button", { name: "Try again" }),
    );
    expect(await screen.findByTestId("deployments-empty")).toBeInTheDocument();
  });

  it("does not offer New deployment when the first list read fails", async () => {
    renderDeployments(<DeploymentsListPage />, {
      api: fakeApi({
        list: vi.fn().mockRejectedValue(refetchErrors[0]),
      }),
    });
    expect(
      await screen.findByTestId("deployment-error-state"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("deployments-empty")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "New deployment" }),
    ).not.toBeInTheDocument();
  });

  it.each(refetchErrors)(
    "keeps New deployment available after an empty list's $code refetch failure",
    async (error) => {
      const api = fakeApi({ list: vi.fn().mockResolvedValue([]) });
      const { queryClient } = renderDeployments(<DeploymentsListPage />, {
        api,
      });
      expect(
        await screen.findByTestId("deployments-empty"),
      ).toBeInTheDocument();
      await failRefetch(
        queryClient,
        deploymentKeys.list(workspace.currentWorkspaceId),
        api.list,
        error,
      );
      expect(
        await screen.findByTestId("deployment-error-state"),
      ).toHaveTextContent(error.message);
      expect(screen.queryByTestId("deployments-empty")).not.toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", { name: "New deployment" }),
      );
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/deployments/new",
      );
    },
  );

  it("shows a credentials error instead of silently omitting Secrets and can retry", async () => {
    const api = fakeApi({
      credentials: vi.fn().mockRejectedValue(
        new OmegaError({
          state: "error",
          status: null,
          code: "malformed_response",
        }),
      ),
    });
    renderDeployments(<DeploymentsListPage />, { api });
    const error = await screen.findByTestId("deployment-error-state");
    expect(error).toHaveTextContent("Omega could not complete the request.");
    expect(await screen.findAllByTestId("deployment-row")).toHaveLength(1);
    expect(screen.queryByText("No deployments yet")).not.toBeInTheDocument();
    expect(api.credentials).toHaveBeenCalledTimes(1);
    api.credentials.mockResolvedValue([]);
    await userEvent.click(
      within(error).getByRole("button", { name: "Try again" }),
    );
    expect(await screen.findByTestId("open-secrets")).toBeInTheDocument();
    expect(
      screen.queryByTestId("deployment-error-state"),
    ).not.toBeInTheDocument();
  });

  it.each(refetchErrors)(
    "replaces a successful empty deployment list with the error after a $code refetch failure",
    async (error) => {
      const api = fakeApi({ list: vi.fn().mockResolvedValue([]) });
      const { queryClient } = renderDeployments(<DeploymentsListPage />, {
        api,
      });
      expect(await screen.findByTestId("deployments-empty")).toHaveTextContent(
        "No deployments yet",
      );
      await failRefetch(
        queryClient,
        deploymentKeys.list(workspace.currentWorkspaceId),
        api.list,
        error,
      );
      expect(
        await screen.findByTestId("deployment-error-state"),
      ).toHaveTextContent(error.message);
      expect(screen.queryByTestId("deployments-empty")).not.toBeInTheDocument();
      expect(screen.queryByText("No deployments yet")).not.toBeInTheDocument();
    },
  );

  it.each(refetchErrors)(
    "keeps cached deployment rows after a $code refetch failure",
    async (error) => {
      const api = fakeApi();
      const { queryClient } = renderDeployments(<DeploymentsListPage />, {
        api,
      });
      expect(await screen.findAllByTestId("deployment-row")).toHaveLength(1);
      await failRefetch(
        queryClient,
        deploymentKeys.list(workspace.currentWorkspaceId),
        api.list,
        error,
      );
      await waitFor(() => {
        expect(screen.getByTestId("deployment-row")).toHaveTextContent(
          "greeter",
        );
        expect(
          screen.queryByTestId("deployment-error-state"),
        ).not.toBeInTheDocument();
        expect(
          screen.queryByTestId("deployments-empty"),
        ).not.toBeInTheDocument();
      });
    },
  );

  it.each(refetchErrors)(
    "shows the credentials error after an empty vault's $code refetch failure",
    async (error) => {
      const api = fakeApi();
      const { queryClient } = renderDeployments(<DeploymentsListPage />, {
        api,
      });
      expect(await screen.findByTestId("open-secrets")).toBeInTheDocument();
      expect(await screen.findAllByTestId("deployment-row")).toHaveLength(1);
      await failRefetch(
        queryClient,
        deploymentKeys.credentials(workspace.currentWorkspaceId),
        api.credentials,
        error,
      );
      expect(
        await screen.findByTestId("deployment-error-state"),
      ).toHaveTextContent(error.message);
      expect(screen.queryByText("No secrets yet")).not.toBeInTheDocument();
      expect(screen.queryByTestId("deployments-empty")).not.toBeInTheDocument();
      expect(screen.getByTestId("deployment-row")).toHaveTextContent("greeter");
    },
  );

  it.each(refetchErrors)(
    "keeps Secrets available without an error tile after a populated vault's $code refetch failure",
    async (error) => {
      const api = fakeApi({
        credentials: vi.fn().mockResolvedValue([
          {
            credentialId: "cred_existing",
            name: "EXISTING_KEY",
            status: "active",
          },
        ]),
      });
      const { queryClient } = renderDeployments(<DeploymentsListPage />, {
        api,
      });
      expect(await screen.findByTestId("open-secrets")).toBeInTheDocument();
      await failRefetch(
        queryClient,
        deploymentKeys.credentials(workspace.currentWorkspaceId),
        api.credentials,
        error,
      );
      await waitFor(() => {
        expect(screen.getByTestId("open-secrets")).toBeInTheDocument();
        expect(
          screen.queryByTestId("deployment-error-state"),
        ).not.toBeInTheDocument();
      });
      await userEvent.click(screen.getByTestId("open-secrets"));
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/deployments/secrets",
      );
    },
  );

  it("names EE's refusal of delegated sessions instead of blaming the role", async () => {
    const api = fakeApi({
      list: vi.fn(async () => {
        throw new OmegaError({
          state: "refused",
          status: 403,
          code: "host_delegation_scope_denied",
        });
      }),
    });
    renderDeployments(<DeploymentsListPage />, { api });
    expect(
      await screen.findByText("Deployments can't be opened in this workspace"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("deployment-row")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "New deployment" }),
    ).not.toBeInTheDocument();
  });

  it("says when deployments are switched off, in EE's words", async () => {
    const api = fakeApi({
      list: vi.fn(async () => {
        throw new OmegaError({
          state: "unavailable",
          status: 503,
          code: "deployments_unavailable",
          message: "Project deployments are off on this control plane.",
        });
      }),
    });
    renderDeployments(<DeploymentsListPage />, { api });
    expect(
      await screen.findByText("Deployments aren't enabled here"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Project deployments are off on this control plane."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "New deployment" }),
    ).not.toBeInTheDocument();
  });

  it("says the account is throttled at once, without retrying first", async () => {
    const api = fakeApi({
      list: vi.fn(async () => {
        throw new OmegaError({
          state: "unavailable",
          status: null,
          code: "session_exchange_throttled",
          message:
            "Your account opened Omega too many times in the last minute. Try again in a few seconds.",
        });
      }),
    });
    renderDeployments(<DeploymentsListPage />, { api });
    expect(
      await screen.findByText(
        "Your account opened Omega too many times in the last minute. Try again in a few seconds.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Omega isn't responding")).toBeInTheDocument();
    expect(api.list).toHaveBeenCalledTimes(1);
  });

  it("waits for the Omega session and shows its refusal", async () => {
    Object.assign(session, {
      state: "refused",
      code: "viewer",
      message: "Viewers cannot use Omega.",
    });
    const api = fakeApi();
    renderDeployments(<DeploymentsListPage />, { api });
    expect(
      await screen.findByText("Viewers cannot use Omega."),
    ).toBeInTheDocument();
    expect(api.list).not.toHaveBeenCalled();
  });
});
