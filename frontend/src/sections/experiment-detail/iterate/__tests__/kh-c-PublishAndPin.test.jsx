// K-3 (BL-06): Publish stays on screen, disabled, and says why.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { palette } from "src/theme/palette";
import PublishAndPin from "../PublishAndPin";

vi.mock("src/api/agent-playground/versions", () => ({
  useActivateVersion: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("src/utils/axios", () => ({
  default: { put: vi.fn(() => Promise.resolve({ data: {} })) },
  endpoints: { develop: { datasetRuns: { baseline: (id) => `/b/${id}/` } } },
}));

const theme = createTheme({ palette: palette("light") });
const ui = (props) => (
  <QueryClientProvider client={new QueryClient()}>
    <ThemeProvider theme={theme}>
      <PublishAndPin
        datasetId="ds"
        cand={{ run_id: "i", target: { version_id: "v31" } }}
        candLabel="#2I"
        baseId="b"
        baseLabel="#2B"
        version="v3.1"
        outcome="6 tasks got better, 0 got worse."
        {...props}
      />
    </ThemeProvider>
  </QueryClientProvider>
);

describe("PublishAndPin with a gate", () => {
  it("is disabled with the reason as its description and on hover", async () => {
    render(
      ui({
        disabled: true,
        reason: "Waiting for 7 tries · Publish when the verdict is in",
        gate: "waiting",
      }),
    );
    const btn = screen.getByTestId("iterate-banner-publish");
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("data-gate", "waiting");
    expect(btn.textContent).toBe("Publish v3.1 and set as baseline");
    const id = btn.getAttribute("aria-describedby");
    expect(id).toBeTruthy();
    expect(document.getElementById(id).textContent).toBe(
      "Waiting for 7 tries · Publish when the verdict is in",
    );
    fireEvent.mouseOver(btn.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Waiting for 7 tries · Publish when the verdict is in",
    );
  });

  it("ready: enabled, no description, and it opens the confirm", () => {
    render(ui({ gate: "ready" }));
    const btn = screen.getByTestId("iterate-banner-publish");
    expect(btn).toBeEnabled();
    expect(btn).toHaveAttribute("data-gate", "ready");
    expect(btn).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByTestId("iterate-banner-publish-reason")).toBeNull();
    fireEvent.click(btn);
    expect(screen.getByTestId("iterate-publish-dialog")).toHaveTextContent(
      "Publish v3.1?",
    );
  });

  it("a reason on an enabled button is not shown", () => {
    render(ui({ reason: "stale reason" }));
    expect(screen.getByTestId("iterate-banner-publish")).not.toHaveAttribute(
      "aria-describedby",
    );
  });
});
