import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithRouter, screen, within } from "src/utils/test-utils";
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
    const openSampleButton = screen.getByRole("button", {
      name: /open sample trace/i,
    });
    expect(openSampleButton).toHaveAttribute(
      "data-tour-anchor",
      "sample_project_button",
    );
    await userEvent.click(openSampleButton);
    await userEvent.click(
      screen.getByRole("link", { name: /connect real observability/i }),
    );

    expect(onOpenSample).toHaveBeenCalledTimes(1);
    expect(onConnectRealData).toHaveBeenCalledTimes(1);
  });

  it("prioritizes real setup after the sample trace is reviewed", () => {
    renderWithRouter(
      <SampleProjectPanel
        sampleProject={sampleProject}
        activationStage="connect_real_data"
        selectedGoal="explore_sample_data"
        realSetupHref="/dashboard/observe?setup=true&source=sample_trace_review&tour_anchor=sample_connect_real_data_button&journey_step=connect_real_data"
        onOpenSample={vi.fn()}
        onConnectRealData={vi.fn()}
        onHideSample={vi.fn()}
      />,
    );

    const panel = screen.getByTestId("sample-project-panel");
    const controls = Array.from(panel.querySelectorAll("a, button")).map(
      (control) => control.textContent,
    );

    expect(
      screen.getByText("Connect the same loop to real data"),
    ).toBeVisible();
    expect(
      within(panel).getByRole("link", { name: /connect real observability/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/observe?setup=true&source=sample_trace_review&tour_anchor=sample_connect_real_data_button&journey_step=connect_real_data",
    );
    expect(
      within(panel).getByRole("link", { name: /connect real observability/i }),
    ).toHaveAttribute("data-tour-anchor", "sample_connect_real_data_button");
    expect(
      within(panel).getByRole("button", { name: /open sample trace/i }),
    ).toHaveAttribute("data-tour-anchor", "sample_trace_link");
    expect(controls.slice(0, 2)).toEqual([
      "Connect real observability",
      "Open sample trace",
    ]);
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
