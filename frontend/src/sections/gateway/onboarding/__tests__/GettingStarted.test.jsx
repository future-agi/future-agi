import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import GettingStarted from "../GettingStarted";
import { onboardingSteps } from "../steps";
import { useRequestCount } from "../useRequestCount";

const state = vi.hoisted(() => ({ organizationId: "org-a", orgLevel: 15, get: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: state.organizationId, orgLevel: state.orgLevel }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: state.get },
  endpoints: { gateway: { requestLogs: "/request-logs" } },
}));

const ready = { source: "ready", key: "ready", managed: "not-ready" };
const TRY = "/dashboard/gateway/models/openai/gpt-4o?tab=try";
// R-LIT synthetic: a caller's reason Try a model is disabled.
const UNAVAILABLE_REASON = "Use the Endpoint card's snippet instead.";
let orgNumber = 0;
beforeEach(() => {
  window.localStorage.clear();
  state.organizationId = `org-test-${++orgNumber}`;
  state.orgLevel = 15;
  state.get.mockReset();
});
afterEach(() => vi.restoreAllMocks());

describe("onboardingSteps", () => {
  it("has only actionable steps, with a successful request milestone", () => {
    const steps = onboardingSteps({});
    expect(steps.map((step) => step.id)).toEqual(["models", "key", "request"]);
    expect(steps[2].title).toBe("First successful request");
  });

  it("does not infer usable credits from enablement alone", () => {
    const [models] = onboardingSteps({ managedOn: true, managedOffered: true });
    expect(models.done).toBe(false);
  });

  it("lets represented readiness override legacy row counts", () => {
    const steps = onboardingSteps({
      providerCount: 3, keyCount: 2, managedOn: true, managedOffered: true,
      readiness: { source: "not-ready", key: "unknown", managed: "not-ready" },
    });
    expect(steps[0].done).toBe(false);
    expect(steps[1].done).toBe(false);
    expect(steps[1].status).toBe("unknown");
  });

  it("retains existing count-only callers and key deep links", () => {
    const steps = onboardingSteps({ providerCount: 1, keyCount: 1, requestCount: 1 });
    expect(steps.every((step) => step.done)).toBe(true);
    expect(steps[1].href).toBe("/dashboard/gateway/keys?create=1");
  });

  it("does not offer credits when they are unavailable or disabled", () => {
    for (const values of [{ managedOn: false, managedOffered: true }, { managedOn: true, managedOffered: false }]) {
      const [models] = onboardingSteps(values);
      expect(models.title).toBe("Add a provider");
      expect(models.description).not.toMatch(/Future AGI credits/);
    }
  });

  it("offers the catalog only with confirmed usable credits", () => {
    const [models] = onboardingSteps({ readiness: { ...ready, managed: "ready" } });
    expect(models.done).toBe(true);
    expect(models.href).toBe("/dashboard/gateway/models");
  });

  it.each([undefined, null, NaN, -1, 0.5, "1"])("keeps invalid request counts unknown: %j", (requestCount) => {
    const request = onboardingSteps({ requestCount })[2];
    expect(request.done).toBe(false);
    expect(request.status).toBe("unknown");
  });

  it("prefers query errors over a retained previous success", () => {
    const request = onboardingSteps({ requestCount: 4, requestQuery: { data: 4, isError: true } })[2];
    expect(request.done).toBe(false);
    expect(request.status).toBe("unknown");
  });

  it("distinguishes console admin guidance from the member copy-and-run route", () => {
    const admin = onboardingSteps({
      tryModelHref: TRY,
      canRunConsole: true,
      tryItAvailable: true,
    })[2];
    expect(admin.tryModel).toEqual({ cta: "Try a model", href: TRY });
    expect(admin.description).toMatch(/Owner or Admin/);
    const member = onboardingSteps({
      tryModelHref: TRY,
      canRunConsole: false,
      tryItAvailable: true,
    })[2];
    expect(member.tryModel).toEqual({
      cta: "Try a model",
      href: TRY,
      disabled: true,
      reason: "Only an organization Owner or Admin can run models here.",
    });
    expect(onboardingSteps({ canRunConsole: false })[2].tryModel).toBeNull();
    expect(member.description).toMatch(/personal API key/);
    expect(member.description).toMatch(/Endpoint/);
  });

  // Try a model opens the model's Try it tab, so it is a link only where the
  // gateway serves Try it. Elsewhere it stays in the step, disabled, with the
  // caller's reason, for every role, and the step points at the snippet.
  it("keeps Try a model disabled with the caller's reason where Try it does not run", () => {
    for (const canRunConsole of [true, false]) {
      const request = onboardingSteps({
        tryModelHref: TRY,
        canRunConsole,
        tryItAvailable: false,
        tryItUnavailableReason: UNAVAILABLE_REASON,
      })[2];
      expect(request.tryModel, String(canRunConsole)).toEqual({
        cta: "Try a model",
        href: TRY,
        disabled: true,
        reason: UNAVAILABLE_REASON,
      });
      expect(request.description).toMatch(/personal API key/);
      expect(request.description).not.toMatch(/console/);
    }
  });

  it("offers Try a model as a link only when the caller says Try it runs", () => {
    const request = onboardingSteps({
      tryModelHref: TRY,
      canRunConsole: true,
    })[2];
    expect(request.tryModel).toEqual({
      cta: "Try a model",
      href: TRY,
      disabled: true,
      reason: "Try it is not available in this release.",
    });
    expect(request.description).not.toMatch(/console/);
  });
});

describe("GettingStarted", () => {
  it("shows progress and the next step's action", () => {
    render(<GettingStarted providerCount={2} keyCount={0} requestCount={0} />);
    expect(screen.getByText("1 of 3 done")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create a key" })).toHaveAttribute("href", "/dashboard/gateway/keys?create=1");
  });

  it("offers console Try for Owner/Admin before an application key exists", () => {
    render(
      <GettingStarted
        providerCount={1}
        keyCount={0}
        requestCount={0}
        tryModelHref={TRY}
        tryItAvailable
      />,
    );
    expect(screen.getByRole("link", { name: "Try a model" })).toHaveAttribute(
      "href",
      TRY,
    );
    expect(
      screen.getByRole("link", { name: "Create a key" }),
    ).toBeInTheDocument();
  });

  it("shows Owner/Admin Try a model disabled with the reason beside it where Try it does not run", () => {
    render(
      <GettingStarted
        providerCount={1}
        keyCount={0}
        requestCount={0}
        tryModelHref={TRY}
        tryItAvailable={false}
        tryItUnavailableReason={UNAVAILABLE_REASON}
      />,
    );
    expect(screen.queryByRole("link", { name: "Try a model" })).toBeNull();
    const tryModel = screen.getByRole("button", { name: "Try a model" });
    expect(tryModel).toBeDisabled();
    expect(tryModel).toHaveAccessibleDescription(UNAVAILABLE_REASON);
    expect(screen.getByText(UNAVAILABLE_REASON)).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Create a key" }),
    ).toBeInTheDocument();
  });

  it("gives members a personal-key path and shows Try a model disabled with the reason", () => {
    state.orgLevel = 3;
    render(
      <GettingStarted
        readiness={ready}
        requestCount={0}
        tryModelHref={TRY}
        tryItAvailable
      />,
    );
    expect(screen.queryByRole("link", { name: "Try a model" })).toBeNull();
    const tryModel = screen.getByRole("button", { name: "Try a model" });
    expect(tryModel).toBeDisabled();
    expect(tryModel).toHaveAccessibleDescription(
      "Only an organization Owner or Admin can run models here.",
    );
    expect(
      screen.getByText(
        "Only an organization Owner or Admin can run models here.",
      ),
    ).toBeVisible();
    expect(screen.getByText(/personal API key/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Logs" })).toBeInTheDocument();
  });

  it("respects the caller's explicit console permission", () => {
    render(
      <GettingStarted
        canRunConsole={false}
        readiness={ready}
        requestCount={0}
        tryModelHref={TRY}
        tryItAvailable
      />,
    );
    expect(screen.queryByRole("link", { name: "Try a model" })).toBeNull();
    expect(screen.getByRole("button", { name: "Try a model" })).toBeDisabled();
  });

  it("keeps failed-only calls incomplete, then completes after Check again without sending a request", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    let successful = 0;
    state.get.mockImplementation(async (_url, { params }) => ({
      data: { count: params.min_status_code === 200 && params.is_error === false ? successful : 5 },
    }));
    function ConnectedSetup() {
      const query = useRequestCount();
      return <GettingStarted readiness={ready} requestQuery={query} onCheckAgain={() => query.refetch()} />;
    }
    const user = userEvent.setup();
    const view = render(<QueryClientProvider client={client}><ConnectedSetup /></QueryClientProvider>);
    try {
      expect(await screen.findByText("2 of 3 done")).toBeInTheDocument();
      expect(await screen.findByRole("button", { name: "Check again" })).toBeEnabled();
      successful = 1;
      await user.click(screen.getByRole("button", { name: "Check again" }));
      expect(await screen.findByRole("button", { name: "Show setup" })).toBeInTheDocument();
      expect(state.get).toHaveBeenCalledTimes(2);
      await user.click(screen.getByRole("button", { name: "Show setup" }));
      expect(screen.getByText("3 of 3 done")).toBeInTheDocument();
    } finally {
      view.unmount();
      client.clear();
    }
  });

  it("keeps errors and malformed counts visible and retryable, even with retained success", async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    const { rerender } = render(<GettingStarted readiness={ready} requestQuery={{ isError: true, data: 7 }} onCheckAgain={retry} />);
    expect(screen.getByText("2 of 3 done")).toBeInTheDocument();
    expect(screen.getByText(/Could not check successful requests/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(retry).toHaveBeenCalledTimes(1);
    rerender(<GettingStarted readiness={ready} requestCount={NaN} onCheckAgain={retry} />);
    expect(screen.getByText("2 of 3 done")).toBeInTheDocument();
    expect(screen.getByText(/Could not check successful requests/)).toBeInTheDocument();
  });

  it("disables Check again while the request read is in flight", () => {
    render(<GettingStarted readiness={ready} requestQuery={{ isFetching: true }} onCheckAgain={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Checking/ })).toBeDisabled();
  });

  it("keeps a restore action after completion and dismissal", async () => {
    const user = userEvent.setup();
    render(<GettingStarted readiness={ready} requestCount={1} />);
    expect(screen.queryByText(/of 3 done/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show setup" }));
    expect(screen.getByText("3 of 3 done")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.getByRole("button", { name: "Show setup" })).toBeInTheDocument();
  });

  it("scopes dismissal to each organization and restores it with Show setup", async () => {
    const user = userEvent.setup();
    const orgA = state.organizationId;
    const { rerender, unmount } = render(<GettingStarted />);
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    state.organizationId = `${orgA}-b`;
    rerender(<GettingStarted />);
    expect(screen.getByText("Get started")).toBeInTheDocument();
    state.organizationId = orgA;
    rerender(<GettingStarted />);
    expect(screen.queryByText("Get started")).toBeNull();
    unmount();
    render(<GettingStarted />);
    await user.click(screen.getByRole("button", { name: "Show setup" }));
    expect(screen.getByText("Get started")).toBeInTheDocument();
  });

  it("does not migrate the legacy global dismissal to any new organization", () => {
    window.localStorage.setItem("agentcc_getting_started_dismissed", "1");
    const { rerender } = render(<GettingStarted />);
    expect(screen.getByText("Get started")).toBeInTheDocument();
    state.organizationId += "-new";
    rerender(<GettingStarted />);
    expect(screen.getByText("Get started")).toBeInTheDocument();
  });

  it("keeps preferences in memory when storage throws, including across remounts", async () => {
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => { throw new Error("Storage blocked"); });
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("Storage blocked"); });
    expect(() => window.localStorage.getItem("probe")).toThrow("Storage blocked");
    expect(() => window.localStorage.setItem("probe", "1")).toThrow("Storage blocked");
    const user = userEvent.setup();
    const orgA = state.organizationId;
    const { rerender, unmount } = render(<GettingStarted />);
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    state.organizationId += "-b";
    rerender(<GettingStarted />);
    expect(screen.getByText("Get started")).toBeInTheDocument();
    state.organizationId = orgA;
    rerender(<GettingStarted />);
    expect(screen.getByRole("button", { name: "Show setup" })).toBeInTheDocument();
    unmount();
    render(<GettingStarted />);
    await user.click(screen.getByRole("button", { name: "Show setup" }));
    expect(screen.getByText("Get started")).toBeInTheDocument();
  });

  it("does not carry an unscoped dismissal into a loaded organization", async () => {
    state.organizationId = null;
    const user = userEvent.setup();
    const { rerender } = render(<GettingStarted />);
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    state.organizationId = `loaded-${orgNumber}`;
    rerender(<GettingStarted />);
    expect(screen.getByText("Get started")).toBeInTheDocument();
  });

  it("keeps the backward-compatible initial loading gate", () => {
    const { container } = render(<GettingStarted loading />);
    expect(container).toBeEmptyDOMElement();
  });
});
