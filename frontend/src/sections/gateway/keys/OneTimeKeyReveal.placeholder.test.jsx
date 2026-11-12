import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import OneTimeKeyReveal from "./OneTimeKeyReveal";

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({ isLoading: false, data: { providers: [] } }),
}));
// The reveal reads managed-credit status; these
// placeholder checks run with none, as reveal-no-own-provider.test.jsx does.
vi.mock("../models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({ data: undefined }),
  useManagedCatalog: () => ({ data: undefined }),
}));

// R-LIT classified (UI copy): the note a fresh key's first request carries
// when it is built on the placeholder, and that placeholder.
const PLACEHOLDER_URL = "https://YOUR_GATEWAY_URL/v1";
const NOTE =
  "The base URL and request here use the placeholder https://YOUR_GATEWAY_URL/v1. Replace it with your gateway's address before you use them.";
// R-LIT synthetic: a one-time reveal with no real secret.
const reveal = {
  name: "ci-reader",
  secret: "fa-test-reveal-value",
  gatewayKeyId: "key-1",
  notes: [],
};

function renderReveal(props) {
  return render(
    <OneTimeKeyReveal
      reveal={reveal}
      baseUrl=""
      gatewayId="gw-1"
      onDone={() => {}}
      {...props}
    />,
  );
}

// A fresh key's first request is shown on the placeholder when the console
// has no confirmed public gateway URL, and says so; a confirmed address needs
// no note.
describe("OneTimeKeyReveal: the first request's base URL", () => {
  it("says when the request is built on the placeholder", () => {
    renderReveal({ requestBaseUrl: PLACEHOLDER_URL, requestBaseUrlNote: NOTE });
    expect(screen.getByText(NOTE)).toBeInTheDocument();
    expect(
      screen.queryByText(/A copy-ready request needs a confirmed gateway URL/),
    ).toBeNull();
  });

  it("shows the Base URL row on the placeholder, with the note, even with no request to show", () => {
    renderReveal({
      baseUrl: PLACEHOLDER_URL,
      requestBaseUrl: PLACEHOLDER_URL,
      requestBaseUrlNote: NOTE,
      gatewayId: null,
    });
    expect(screen.getByText("Base URL (OpenAI-compatible)")).toBeInTheDocument();
    expect(screen.getByText(PLACEHOLDER_URL, { selector: "code" })).toBeInTheDocument();
    expect(screen.getByText(NOTE)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/localhost|127\.0\.0\.1/);
  });

  it("adds no note on a confirmed address", () => {
    renderReveal({
      requestBaseUrl: "https://gateway.example.test/v1",
      requestBaseUrlNote: "",
    });
    expect(screen.queryByText(NOTE)).toBeNull();
  });
});
