import React from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import GettingStarted from "../GettingStarted";
import { onboardingSteps } from "../steps";

const STORAGE_KEY = "agentcc_getting_started_dismissed";

describe("onboardingSteps", () => {
  it("F3 keeps provider onboarding when deployment is available but the organization is disabled", () => {
    const [models] = onboardingSteps({
      managedOffered: true,
      managedOn: false,
    });
    expect(models.title).toBe("Add a provider");
    expect(models.href).toBe("/dashboard/gateway/providers");
    expect(models.description).not.toMatch(
      /Future AGI credits|every catalogue/,
    );
    render(<GettingStarted managedOffered managedOn={false} />);
    expect(
      screen.getByRole("link", { name: "Add a provider" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Create an API key")).toBeInTheDocument();
  });
  it("has no step that is always done: no 'Gateway connected'", () => {
    const steps = onboardingSteps({});
    expect(steps.map((step) => step.id)).toEqual(["models", "key", "request"]);
  });

  it("counts managed models as a model source where the deployment offers them", () => {
    const [models] = onboardingSteps({
      providerCount: 0,
      managedOn: true,
      managedOffered: true,
    });
    expect(models.done).toBe(true);
    expect(models.title).toBe("Add a provider or use Future AGI credits");
    expect(models.description).toBe(
      "Use Future AGI credits for credit-eligible catalog models, or add your own provider keys under Providers.",
    );
    expect(models.description).not.toMatch(/managed/i);
    const [none] = onboardingSteps({
      providerCount: 0,
      managedOn: false,
      managedOffered: true,
    });
    expect(none.done).toBe(false);
  });

  // Every hosted organization today: no managed models to turn on, and no
  // catalog to open.
  it("asks for a provider where the deployment offers no managed models", () => {
    const [models] = onboardingSteps({ providerCount: 0, managedOn: true });
    expect(models).toEqual(
      expect.objectContaining({
        title: "Add a provider",
        cta: "Add a provider",
        href: "/dashboard/gateway/providers",
        done: false,
      }),
    );
    expect(models.description).not.toMatch(/managed/i);
    expect(models.title + models.description).not.toMatch(/Future AGI/);
    expect(onboardingSteps({ providerCount: 1 })[0].done).toBe(true);
  });

  it("marks the first request done from any request ever, not the last 24 hours", () => {
    const steps = onboardingSteps({ requestCount: 1 });
    expect(steps.find((step) => step.id === "request").done).toBe(true);
  });

  it("links each step to where it is done", () => {
    const steps = onboardingSteps({});
    expect(steps.find((step) => step.id === "key").href).toBe(
      "/dashboard/gateway/keys?create=1",
    );
    expect(
      onboardingSteps({ managedOffered: true, managedOn: true }).find(
        (step) => step.id === "models",
      ).href,
    ).toBe("/dashboard/gateway/models");
  });
});

// Try it on a model page sends a request through the gateway from the
// console, with no API key, and the request shows up in Logs: the first
// request step offers it for the first model one of the org's providers
// serves.
describe("onboardingSteps: Try a model", () => {
  const TRY = "/dashboard/gateway/models/openai/gpt-4o?tab=try";

  it("offers Try a model on the first request step when a model is served", () => {
    const request = onboardingSteps({ tryModelHref: TRY }).find(
      (step) => step.id === "request",
    );
    expect(request.tryModel).toEqual({ cta: "Try a model", href: TRY });
    expect(request.description).toBe(
      "Use the Endpoint card's snippet, or try a model here in the console; the request shows up in Logs within seconds.",
    );
  });

  it("offers no Try a model when no provider serves a model", () => {
    const request = onboardingSteps({}).find((step) => step.id === "request");
    expect(request.tryModel).toBeNull();
    expect(request.description).toBe(
      "Use the Endpoint card's snippet; the request shows up in Logs within seconds.",
    );
  });
});

describe("GettingStarted", () => {
  beforeEach(() => {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // storage may be unavailable
    }
  });

  it("shows progress and the next step's action", () => {
    render(
      <GettingStarted
        providerCount={2}
        managedOn={false}
        keyCount={0}
        requestCount={0}
      />,
    );
    expect(screen.getByText("1 of 3 done")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create a key" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/keys?create=1",
    );
  });

  it("links Try a model on the first request step while it is not done, beside Open Logs", () => {
    const href = "/dashboard/gateway/models/openai/gpt-4o?tab=try";
    render(
      <GettingStarted
        providerCount={1}
        keyCount={1}
        requestCount={0}
        tryModelHref={href}
      />,
    );
    expect(screen.getByRole("link", { name: "Try a model" })).toHaveAttribute(
      "href",
      href,
    );
    expect(screen.getByRole("link", { name: "Open Logs" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/logs",
    );
  });

  // Try it needs no API key, so the link is there before a key exists.
  it("links Try a model before an API key exists", () => {
    const href = "/dashboard/gateway/models/openai/gpt-4o?tab=try";
    render(
      <GettingStarted
        providerCount={1}
        keyCount={0}
        requestCount={0}
        tryModelHref={href}
      />,
    );
    expect(screen.getByRole("link", { name: "Create a key" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Try a model" })).toHaveAttribute(
      "href",
      href,
    );
  });

  it("offers no Try a model without a served model, or once a request was sent", () => {
    const { unmount } = render(
      <GettingStarted providerCount={0} keyCount={1} requestCount={0} />,
    );
    expect(screen.queryByRole("link", { name: "Try a model" })).toBeNull();
    unmount();
    render(
      <GettingStarted
        providerCount={1}
        keyCount={0}
        requestCount={3}
        tryModelHref="/dashboard/gateway/models/openai/gpt-4o?tab=try"
      />,
    );
    expect(screen.queryByRole("link", { name: "Try a model" })).toBeNull();
  });

  it("disappears once everything is done", () => {
    const { container } = render(
      <GettingStarted
        providerCount={2}
        managedOn
        keyCount={1}
        requestCount={10}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("can be dismissed, and stays dismissed", async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <GettingStarted providerCount={0} keyCount={0} requestCount={0} />,
    );
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/of 3 done/)).toBeNull();
    unmount();
    render(<GettingStarted providerCount={0} keyCount={0} requestCount={0} />);
    expect(screen.queryByText(/of 3 done/)).toBeNull();
  });

  it("renders nothing while its counts are still loading", () => {
    const { container } = render(<GettingStarted loading />);
    expect(container).toBeEmptyDOMElement();
  });
});
