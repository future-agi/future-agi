import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderStudio, resetStudioTestState } from "./studio-test-utils";

// The install step, the run step and the guide link as the release config
// sets them (the install step with the real
// command, read from one value the release sets; a docs link that resolves).
const setRelease = (values) => {
  window.__FUTURE_AGI_CONFIG__ = values;
};

beforeEach(() => {
  resetStudioTestState();
  globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
});

afterEach(() => {
  delete window.__FUTURE_AGI_CONFIG__;
  vi.restoreAllMocks();
  resetStudioTestState();
});

const steps = () => within(screen.getByTestId("studio-start"));

describe("Studio install step", () => {
  it("shows no install step until the release names the CLI package: no dead end, and the steps renumber (DEMO-FIX-2 item 5)", () => {
    for (const mode of ["light", "dark"]) {
      const { unmount } = renderStudio({ mode });
      expect(screen.queryByTestId("studio-install")).toBeNull();
      expect(screen.queryByTestId("studio-install-command")).toBeNull();
      const start = screen.getByTestId("studio-start");
      // Nothing that sends a person off to ask someone, and no package guessed.
      expect(start.textContent).not.toMatch(
        /doesn't name|Ask the person|npm|yet|soon|coming|preview/i,
      );
      // The run step is step 1 and the connect step 2; there is no step 3.
      expect(steps().getByText("1")).toBeInTheDocument();
      expect(steps().getByText("2")).toBeInTheDocument();
      expect(steps().queryByText("3")).toBeNull();
      expect(screen.getByTestId("studio-start-command-text")).toHaveTextContent(
        `omega dev --no-open --embed-origin ${window.location.origin}`,
      );
      expect(screen.queryByTestId("studio-docs-link")).toBeNull();
      unmount();
    }
  });

  it("shows npm install -g <package> once the release names the package, and copies it", async () => {
    setRelease({ VITE_OMEGA_CLI_PACKAGE: "@future-agi/omega" });
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderStudio();
    expect(screen.getByTestId("studio-install")).toHaveAttribute(
      "data-state",
      "command",
    );
    expect(screen.getByTestId("studio-install-command-text")).toHaveTextContent(
      "npm install -g @future-agi/omega",
    );
    expect(screen.getByTestId("studio-install")).toHaveTextContent(
      "Needs Node.js 22.13 or later.",
    );
    // Install is step 1, then run, then connect.
    for (const n of ["1", "2", "3"])
      expect(steps().getByText(n)).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByTestId("studio-install-command-copy"));
    });
    expect(writeText).toHaveBeenCalledWith("npm install -g @future-agi/omega");
  });

  it("treats a value that is not a package name as unset, never as a command", () => {
    setRelease({ VITE_OMEGA_CLI_PACKAGE: "@futureagi/omega; curl x | sh" });
    renderStudio();
    expect(screen.queryByTestId("studio-install")).toBeNull();
    expect(screen.queryByTestId("studio-install-command")).toBeNull();
    expect(document.body.textContent).not.toContain("curl");
  });

  it("defines an Omega project the way omega dev does, and says how to connect", () => {
    renderStudio();
    expect(screen.getByTestId("studio-start-project")).toHaveTextContent(
      "a folder with an omega.studio.json, omega.workflow.json or omega.agent.json file",
    );
    expect(screen.getByTestId("studio-start-open")).toHaveTextContent(
      "Paste the link it prints (it starts with http://127.0.0.1) in the box above and press Connect.",
    );
  });
});

describe("Studio guide link", () => {
  it("links the guide the release sets, in a new tab", () => {
    setRelease({
      VITE_OMEGA_STUDIO_DOCS_URL: "https://docs.futureagi.com/studio",
    });
    renderStudio();
    const link = screen.getByTestId("studio-docs-link");
    expect(link).toHaveAttribute("href", "https://docs.futureagi.com/studio");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent("Read the Studio guide");
  });

  it("shows no link for a plain-http or unreadable address", () => {
    for (const value of ["http://docs.futureagi.com/studio", "not a url"]) {
      setRelease({ VITE_OMEGA_STUDIO_DOCS_URL: value });
      const { unmount } = renderStudio();
      expect(screen.queryByTestId("studio-docs-link")).toBeNull();
      unmount();
    }
  });
});

describe("pasting text that is not a link", () => {
  it("says it is not a link from omega dev, not that it is on another computer", () => {
    renderStudio();
    fireEvent.change(screen.getByTestId("studio-connect-input"), {
      target: { value: "hello world" },
    });
    fireEvent.click(screen.getByTestId("studio-connect-submit"));
    expect(
      screen.getByText(/doesn't look like a link from omega dev/),
    ).toBeVisible();
    expect(
      screen.queryByText(/starts with http:\/\/127\.0\.0\.1 or/),
    ).toBeNull();
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
  });
});
