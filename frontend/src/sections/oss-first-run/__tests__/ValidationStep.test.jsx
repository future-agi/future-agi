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

function renderStep(mode = LAUNCH_MODE.LIVE, onSwitchMode = vi.fn()) {
  render(
    <ThemeProvider theme={theme}>
      <ValidationStep
        mode={mode}
        onSwitchMode={onSwitchMode}
        onContinue={vi.fn()}
      />
    </ThemeProvider>,
  );
  return onSwitchMode;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockData = { status: "issues", mode: "live", checks: [check(), STORAGE_DOWN] };
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
    expect(await screen.findByText(/docker compose up -d minio/)).toBeInTheDocument();
  });

  it("carries the remedy for a warning too, not only a failure", async () => {
    mockData = {
      status: "ok",
      mode: "experiment",
      checks: [check(), { ...STORAGE_DOWN, status: "warning", required: false }],
    };
    renderStep(LAUNCH_MODE.EXPERIMENT);
    expect(await screen.findByText(/docker compose up -d minio/)).toBeInTheDocument();
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
    const support = await screen.findByRole("link", { name: "Contact support" });
    expect(support.getAttribute("href")).toMatch(/^mailto:support@futureagi\.com\?/);
  });
});
