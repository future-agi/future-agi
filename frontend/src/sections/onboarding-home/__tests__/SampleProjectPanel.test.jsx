import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithRouter, screen } from "src/utils/test-utils";
import SampleProjectPanel from "../components/SampleProjectPanel";

const sampleProject = {
  available: true,
  created: false,
  status: "not_created",
  label: "Sample",
  href: "/dashboard/home?sample=true",
  entryRoute: null,
  entryRoutes: [],
  isHidden: false,
  realSetupHref: "/dashboard/observe?setup=true&source=onboarding",
};

describe("SampleProjectPanel", () => {
  it("renders sample and real setup actions", async () => {
    const onOpenSample = vi.fn();
    const onConnectRealData = vi.fn();

    renderWithRouter(
      <SampleProjectPanel
        sampleProject={sampleProject}
        activationStage="waiting_for_first_trace_sample_available"
        selectedGoal="monitor_production_ai_app"
        onOpenSample={onOpenSample}
        onConnectRealData={onConnectRealData}
        onHideSample={vi.fn()}
      />,
    );

    expect(screen.getByTestId("sample-project-panel")).toBeVisible();
    expect(screen.getByText("Fastest path to Aha")).toBeVisible();
    expect(screen.getByText("Preview the quality loop first")).toBeVisible();
    expect(screen.getByTestId("sample-project-aha-preview")).toBeVisible();
    expect(screen.getByText("Quality issue")).toBeVisible();
    expect(screen.getByText("Turn it into an evaluator")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: /open sample trace/i }),
    );
    await userEvent.click(
      screen.getByRole("link", { name: /connect real observability/i }),
    );

    expect(onOpenSample).toHaveBeenCalledTimes(1);
    expect(onConnectRealData).toHaveBeenCalledTimes(1);
  });

  it("does not render hidden samples", () => {
    renderWithRouter(
      <SampleProjectPanel
        sampleProject={{ ...sampleProject, isHidden: true, status: "hidden" }}
        activationStage="waiting_for_first_trace_sample_available"
      />,
    );

    expect(
      screen.queryByTestId("sample-project-panel"),
    ).not.toBeInTheDocument();
  });
});
