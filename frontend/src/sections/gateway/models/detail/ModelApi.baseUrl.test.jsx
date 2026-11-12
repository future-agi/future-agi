import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { ACCESS } from "../catalogRows";
import ModelApi from "./ModelApi";

// The personal-key bar reads the auth, organization and workspace contexts.
// This file is about the base URL, so the bar passes its children through
// with no key.
vi.mock("../../overview/GatewaySnippetKey", () => ({
  default: ({ children }) => children(""),
}));

const theme = createTheme({ palette: palette("light"), typography });

// R-LIT synthetic: a visibly fake public gateway URL (RFC 2606 .test host),
// the value AGENTCC_GATEWAY_PUBLIC_URL would give a hosted deployment.
const PUBLIC_URL = "https://gateway.example.test/v1";
// R-LIT synthetic: the local address the platform reports when no public URL
// is set.
const LOCAL_URL = "http://localhost:8090/v1";
// R-LIT synthetic: a model id no catalog lists.
const MODEL = "example-vendor/example-chat-model";
// R-LIT classified (UI copy): the placeholder requests are built on when no
// confirmed public URL is known, and the note shown beside them.
const PLACEHOLDER_URL = "https://YOUR_GATEWAY_URL/v1";
const LOOPBACK = /localhost|127\.0\.0\.1|\[::1\]/i;
const NO_URL_NOTE =
  "The gateway's public URL isn't available from the console right now, so these requests use the placeholder https://YOUR_GATEWAY_URL/v1. Replace it with your gateway's address before you run them, or reload the page to try again.";
// R-LIT classified (UI copy): the note a confirmed self-hosted deployment with
// no public address gets beside the placeholder (the new-key dialog's note).
const PLACEHOLDER_NOTE =
  "The base URL and request here use the placeholder https://YOUR_GATEWAY_URL/v1. Replace it with your gateway's address before you use them.";

// A chat model the organization's own provider serves: it has a request to
// copy.
const row = {
  id: MODEL,
  type: "chat",
  access: ACCESS.OWN_KEY,
  managedOn: false,
  ownRoutes: [
    { sendAs: MODEL, providerId: "example", providerName: "Example" },
  ],
};

function renderApi(props) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <ModelApi row={row} managedOffered={false} {...props} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const LANGUAGES = ["cURL", "Python", "TypeScript"];

// Shows each request language in turn (the first is showing) and hands the
// page's text to `check`.
async function eachLanguage(check) {
  const user = userEvent.setup();
  for (const language of LANGUAGES) {
    if (language !== "cURL") {
      await user.click(screen.getByRole("button", { name: language }));
    }
    check(document.body.textContent, language);
  }
}

// The code block's text in each language (the note beside it also names the
// placeholder, so the page text alone would prove nothing).
async function eachCodeBlock(check) {
  const user = userEvent.setup();
  for (const language of LANGUAGES) {
    if (language !== "cURL") {
      await user.click(screen.getByRole("button", { name: language }));
    }
    check(document.querySelector("pre")?.textContent || "", language);
  }
}

describe("model API tab: the base URL of its requests", () => {
  it("shows a hosted deployment's configured public URL in every language, never the placeholder or localhost", async () => {
    renderApi({
      hosted: true,
      deploymentModeConfirmed: true,
      baseUrl: PUBLIC_URL,
    });
    await eachLanguage((text, language) => {
      expect(text, language).toContain(PUBLIC_URL);
      expect(text, language).not.toMatch(/YOUR_GATEWAY_URL|localhost/i);
    });
    expect(
      screen.getByRole("button", { name: "Copy base URL" }),
    ).toBeInTheDocument();
  });

  it("shows a self-hosted deployment the placeholder in every language, not the address the platform reports", async () => {
    renderApi({
      hosted: false,
      deploymentModeConfirmed: true,
      baseUrl: LOCAL_URL,
    });
    await eachLanguage((text, language) => {
      expect(text, language).toContain(PLACEHOLDER_URL);
      expect(text, language).not.toMatch(/localhost/i);
    });
    expect(screen.queryByRole("button", { name: "Copy base URL" })).toBeNull();
    // The placeholder says what to put in its place, as the new-key dialog
    // does; the console is not missing anything a reload would bring.
    expect(screen.getByText(PLACEHOLDER_NOTE)).toBeInTheDocument();
    expect(screen.queryByText(NO_URL_NOTE)).toBeNull();
  });

  it("gives a self-hosted deployment with an in-cluster address the placeholder and the same note", async () => {
    renderApi({
      hosted: false,
      deploymentModeConfirmed: true,
      baseUrl: "http://agentcc-gateway:8090/v1",
    });
    expect(screen.getByText(PLACEHOLDER_NOTE)).toBeInTheDocument();
    expect(screen.queryByText(NO_URL_NOTE)).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy base URL" })).toBeNull();
    expect(document.body.textContent).not.toContain("agentcc-gateway");
    await eachCodeBlock((code, language) => {
      expect(code, language).toContain(PLACEHOLDER_URL);
    });
  });

  it("shows requests on the marked placeholder when deployment mode is not confirmed", async () => {
    const retryDeploymentMode = vi.fn();
    renderApi({ baseUrl: PUBLIC_URL, retryDeploymentMode });
    expect(screen.getByText(NO_URL_NOTE)).toBeInTheDocument();
    await eachCodeBlock((code, language) => {
      expect(code, language).toContain(PLACEHOLDER_URL);
    });
    expect(document.body.textContent).not.toContain(PUBLIC_URL);
    expect(screen.queryByRole("button", { name: "Copy base URL" })).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Retry deployment mode" }),
    );
    expect(retryDeploymentMode).toHaveBeenCalledTimes(1);
  });

  it("gives a hosted deployment with no public URL the note and requests on the marked placeholder", async () => {
    renderApi({ hosted: true, deploymentModeConfirmed: true, baseUrl: "" });
    expect(screen.getByText(NO_URL_NOTE)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy base URL" })).toBeNull();
    await eachCodeBlock((code, language) => {
      expect(code, language).toContain(PLACEHOLDER_URL);
      expect(code, language).not.toMatch(/localhost/i);
    });
  });

  it.each([
    LOCAL_URL,
    "http://127.0.0.1:8090/v1",
    "http://[::1]:8090/v1",
    PLACEHOLDER_URL,
  ])(
    "builds hosted requests on the marked placeholder, never at %s",
    async (baseUrl) => {
      renderApi({ hosted: true, deploymentModeConfirmed: true, baseUrl });
      expect(screen.getByText(NO_URL_NOTE)).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Copy base URL" }),
      ).toBeNull();
      // No loopback address anywhere on the page, not just in the code.
      expect(document.body.textContent).not.toMatch(LOOPBACK);
      await eachCodeBlock((code, language) => {
        expect(code, language).toContain(PLACEHOLDER_URL);
        expect(code, language).not.toMatch(LOOPBACK);
      });
    },
  );
});
