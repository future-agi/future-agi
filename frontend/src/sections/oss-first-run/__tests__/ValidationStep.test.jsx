import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import ValidationStep from "../ValidationStep";
import { LAUNCH_MODE } from "../constants";

const mockRefetch = vi.fn();
let mockData = null;

vi.mock("src/api/ossSetup/oss-setup", () => ({
  useSetupChecks: () => ({
    data: mockData,
    isError: false,
    refetch: mockRefetch,
    errorUpdatedAt: 0,
  }),
}));

const theme = createTheme({ palette: { amber: { 600: "#CA8A04" } } });

const check = (over) => ({
  id: "database",
  label: "Core application database",
  status: "passed",
  required: true,
  detail: "",
  fix: "",
  docs_url: "",
  ...over,
});

const STORAGE_DOWN = check({
  id: "storage",
  label: "Object storage service",
  status: "failed",
  detail: "Dataset uploads, exports and media will fail",
  fix: "Start it: `docker compose up -d minio`.",
  docs_url: "https://example.test/object-storage",
});

function renderStep(
  mode = LAUNCH_MODE.LIVE,
  onSwitchMode = vi.fn(),
  { authenticated = false } = {},
) {
  render(
    <ThemeProvider theme={theme}>
      <ValidationStep
        mode={mode}
        onSwitchMode={onSwitchMode}
        onContinue={vi.fn()}
        authenticated={authenticated}
      />
    </ThemeProvider>,
  );
  return onSwitchMode;
}

const SSL_LOCAL = check({
  id: "ssl",
  label: "SSL/TLS certificate",
  status: "skipped",
  required: false,
  detail:
    "Not needed on a local install: it is only reachable from this machine or a private network",
});

beforeEach(() => {
  vi.clearAllMocks();
  mockData = {
    status: "issues",
    mode: "live",
    checks: [check(), STORAGE_DOWN],
  };
});

describe("ValidationStep", () => {
  it("offers no way back once a launch mode is picked", async () => {
    renderStep();
    await waitFor(() =>
      expect(screen.getByText("Object storage service")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
  });

  it("shows a one line remedy for a down check, linked to its docs", async () => {
    renderStep();
    const link = await screen.findByRole("link", {
      name: "Object storage service",
    });
    expect(link).toHaveAttribute("href", "https://example.test/object-storage");
    expect(
      await screen.findByText(/docker compose up -d minio/),
    ).toBeInTheDocument();
  });

  it("carries the remedy for a warning too, not only a failure", async () => {
    mockData = {
      status: "ok",
      mode: "experiment",
      checks: [
        check(),
        { ...STORAGE_DOWN, status: "warning", required: false },
      ],
    };
    renderStep(LAUNCH_MODE.EXPERIMENT);
    expect(
      await screen.findByText(/docker compose up -d minio/),
    ).toBeInTheDocument();
  });

  it("offers Test flight when a live launch is blocked, and switches mode", async () => {
    const onSwitchMode = renderStep();
    const button = await screen.findByRole("button", {
      name: "Continue with Test flight",
    });
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.click(button);
    expect(onSwitchMode).toHaveBeenCalledWith(LAUNCH_MODE.EXPERIMENT);
  });

  it("offers the way back to Production from a test flight", async () => {
    mockData = { status: "ok", mode: "experiment", checks: [check()] };
    const onSwitchMode = renderStep(LAUNCH_MODE.EXPERIMENT);
    const button = await screen.findByRole("button", {
      name: "Switch to Production",
    });
    fireEvent.click(button);
    expect(onSwitchMode).toHaveBeenCalledWith(LAUNCH_MODE.LIVE);
  });

  it("opens the operator's own mail client, not Gmail on the web", async () => {
    renderStep();
    const support = await screen.findByRole("link", {
      name: "Contact support",
    });
    expect(support.getAttribute("href")).toMatch(
      /^mailto:support@futureagi\.com\?/,
    );
  });

  it("names the setup that is running", async () => {
    mockData = { ...mockData, setup: "standalone" };
    renderStep();
    expect(await screen.findByText("Standalone setup.")).toBeInTheDocument();
    expect(screen.getByText(/one app container/)).toBeInTheDocument();
  });

  it("names the distributed setup too", async () => {
    mockData = { ...mockData, setup: "distributed" };
    renderStep();
    expect(await screen.findByText("Distributed setup.")).toBeInTheDocument();
  });

  it("names the Helm setup too", async () => {
    mockData = { ...mockData, setup: "helm" };
    renderStep();
    expect(await screen.findByText("Helm setup.")).toBeInTheDocument();
    expect(screen.getByText(/on Kubernetes/)).toBeInTheDocument();
  });

  it("says nothing about the setup when an older server does not report it", async () => {
    renderStep();
    await screen.findByText("Object storage service");
    expect(screen.queryByTestId("oss-setup-kind")).toBeNull();
  });

  it.each(["nomad", "constructor"])(
    "says nothing about a setup this build does not know (%s)",
    async (setup) => {
      mockData = { ...mockData, setup };
      renderStep();
      await screen.findByText("Object storage service");
      expect(screen.queryByTestId("oss-setup-kind")).toBeNull();
    },
  );

  it("lets a local production launch through with SSL skipped", async () => {
    mockData = {
      status: "ok",
      mode: "live",
      setup: "standalone",
      checks: [check(), SSL_LOCAL],
    };
    renderStep();
    expect(
      await screen.findByText(/Not needed on a local install/),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled(),
    );
    expect(
      screen.queryByRole("button", { name: "Continue with Test flight" }),
    ).toBeNull();
  });

  it("shows the next steps once pre-flight is clear", async () => {
    mockData = { status: "ok", mode: "live", checks: [check(), SSL_LOCAL] };
    renderStep();
    expect(await screen.findByText("Next up")).toBeInTheDocument();
    expect(
      screen.getByText(/Create your account on the next screen/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Open Keys in the sidebar/)).toBeInTheDocument();
    expect(
      screen.getByText("FI_BASE_URL=http://localhost:4318"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tracing guide" })).toHaveAttribute(
      "href",
      "https://docs.futureagi.com/docs/observe",
    );
  });

  it("sends the first trace to the collector the server reports", async () => {
    mockData = {
      status: "ok",
      mode: "live",
      setup: "standalone",
      collector_http_url: "http://localhost:4320",
      checks: [check()],
    };
    renderStep();
    expect(
      await screen.findByText("FI_BASE_URL=http://localhost:4320"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/localhost:4318/)).toBeNull();
  });

  it("does not ask a signed-in operator to create an account", async () => {
    mockData = { status: "ok", mode: "live", checks: [check()] };
    renderStep(LAUNCH_MODE.LIVE, vi.fn(), { authenticated: true });
    expect(
      await screen.findByText("Continue to your workspace."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Create your account/)).toBeNull();
  });

  it("holds the next steps back while a production launch is blocked", async () => {
    renderStep();
    await screen.findByRole("button", { name: "Continue with Test flight" });
    expect(screen.queryByText("Next up")).toBeNull();
  });
});
