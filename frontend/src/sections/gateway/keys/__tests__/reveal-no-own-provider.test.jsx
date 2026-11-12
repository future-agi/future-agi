import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import OneTimeKeyReveal from "../OneTimeKeyReveal";

// A new key with no access lists, in an organization with
// no own-provider model, must not be told its access lists exclude a route.

const state = vi.hoisted(() => ({
  providers: [],
  managedStatus: { data: undefined },
}));

vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({
    data: { providers: state.providers },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("../../models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => state.managedStatus,
  useManagedCatalog: () => ({ data: undefined }),
}));

// R-LIT synthetic: a visibly fake public gateway URL (RFC 2606 .test host).
const PUBLIC_URL = "https://gateway.example.test/v1";
const CREDITS_READY = { available: true, enabled: true, balance_usd: "10" };
const ACCESS_LISTS_COPY = /this key's access lists/;

function renderReveal(reveal = {}) {
  render(
    <OneTimeKeyReveal
      reveal={{
        name: "ci",
        secret: "fresh-key-fixture-only",
        allowedModels: [],
        allowedProviders: [],
        ...reveal,
      }}
      baseUrl={PUBLIC_URL}
      requestBaseUrl={PUBLIC_URL}
      gatewayId="default"
      onDone={vi.fn()}
    />,
  );
  return screen.getByRole("dialog", { name: "Copy your new key" });
}

describe("key reveal without own-provider models", () => {
  beforeEach(() => {
    state.providers = [];
    state.managedStatus = { data: undefined };
  });

  it("points a credit organization to credit-eligible models, not the key's access lists", () => {
    state.managedStatus = { data: CREDITS_READY };
    const reveal = within(renderReveal());
    expect(reveal.queryByText(ACCESS_LISTS_COPY)).toBeNull();
    expect(
      reveal.getByText(
        "Your organization has no own-provider models ready yet. Pick a credit-eligible model on Models, or add a provider.",
      ),
    ).toBeInTheDocument();
    expect(reveal.getByRole("link", { name: "Discover models" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/models",
    );
  });

  it("asks for a provider, not an access-list change, when credits are not ready", () => {
    state.managedStatus = { data: { available: false, enabled: false } };
    const reveal = within(renderReveal());
    expect(reveal.queryByText(ACCESS_LISTS_COPY)).toBeNull();
    expect(reveal.queryByText(/credit-eligible/)).toBeNull();
    expect(
      reveal.getByText(/Your organization has no own-provider models ready yet\. Add and apply a provider/),
    ).toBeInTheDocument();
  });

  it("still names the access lists when they exclude every configured route", () => {
    state.managedStatus = { data: CREDITS_READY };
    state.providers = [
      { name: "team", pushed: true, healthy: true, request_count: 20, models: ["model-b"] },
    ];
    const reveal = within(renderReveal({ allowedProviders: ["missing"] }));
    expect(reveal.getByText(ACCESS_LISTS_COPY)).toBeInTheDocument();
    expect(reveal.queryByText(/no own-provider models/)).toBeNull();
  });
});
