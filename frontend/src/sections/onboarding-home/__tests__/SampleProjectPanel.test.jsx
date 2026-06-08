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
    expect(screen.getByText("Sample trace")).toBeVisible();
    expect(screen.getByText("Preview sample trace")).toBeVisible();
    expect(screen.getByTestId("sample-project-preview-points")).toBeVisible();
    const glimpse = screen.getByTestId("sample-trace-glimpse");
    expect(glimpse).toBeVisible();
    expect(within(glimpse).getByText("Inside the sample trace")).toBeVisible();
    expect(
      within(glimpse).getByText(
        /these fields fill in.*from your own production traces/i,
      ),
    ).toBeVisible();
    expect(within(glimpse).getByText("Input and output")).toBeVisible();
    expect(within(glimpse).getByText("Quality check result")).toBeVisible();
    expect(screen.getByText("Preview only")).toBeVisible();
    expect(
      screen.getByText(
        "Sample data is ready for preview. It does not finish setup; connect real data to complete the workflow.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Issue to review")).toBeVisible();
    expect(screen.getByText("Real setup")).toBeVisible();
    expect(
      screen.getByText(
        "Connect real data to create checks from your own traces",
      ),
    ).toBeVisible();
    const controls = Array.from(
      screen.getByTestId("sample-project-panel").querySelectorAll("a, button"),
    ).map((control) => control.textContent);
    const openSampleButton = screen.getByRole("button", {
      name: /open sample trace/i,
    });
    expect(openSampleButton).toHaveAttribute(
      "data-tour-anchor",
      "sample_project_button",
    );
    expect(controls.slice(0, 2)).toEqual([
      "Continue trace setup",
      "Open sample trace",
    ]);
    await userEvent.click(openSampleButton);
    await userEvent.click(
      screen.getByRole("link", { name: /continue trace setup/i }),
    );

    expect(onOpenSample).toHaveBeenCalledTimes(1);
    expect(onConnectRealData).toHaveBeenCalledTimes(1);
  });

  it("does not present invented values as a real captured trace", () => {
    renderWithRouter(
      <SampleProjectPanel
        sampleProject={sampleProject}
        activationStage="waiting_for_first_trace_sample_available"
        selectedGoal="monitor_production_ai_app"
        onOpenSample={vi.fn()}
        onConnectRealData={vi.fn()}
        onHideSample={vi.fn()}
      />,
    );

    const glimpse = screen.getByTestId("sample-trace-glimpse");
    // The glimpse must describe trace structure, never fabricated specifics
    // (model name, span count, latency, score, prompt/response, or ticket).
    expect(within(glimpse).queryByText(/Captured trace/i)).toBeNull();
    expect(glimpse.textContent).not.toMatch(/gpt-4o/i);
    expect(glimpse.textContent).not.toMatch(/\d+\s*ms/i);
    expect(glimpse.textContent).not.toMatch(/score\s*0?\.\d+/i);
    expect(glimpse.textContent).not.toMatch(/double-charged/i);
    expect(glimpse.textContent).not.toMatch(/missing-context/i);
  });

  it("keeps sample preview primary only for the preview-only sample goal", () => {
    renderWithRouter(
      <SampleProjectPanel
        sampleProject={sampleProject}
        activationStage="review_sample_signal"
        selectedGoal="explore_sample_data"
        onOpenSample={vi.fn()}
        onConnectRealData={vi.fn()}
        onHideSample={vi.fn()}
      />,
    );

    const controls = Array.from(
      screen.getByTestId("sample-project-panel").querySelectorAll("a, button"),
    ).map((control) => control.textContent);

    expect(controls.slice(0, 2)).toEqual([
      "Open sample trace",
      "Connect real data",
    ]);
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
      screen.getByText("Connect the same workflow to real data"),
    ).toBeVisible();
    expect(
      within(panel).getByRole("link", { name: /connect real data/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/observe?setup=true&source=sample_trace_review&tour_anchor=sample_connect_real_data_button&journey_step=connect_real_data",
    );
    expect(
      within(panel).getByRole("link", { name: /connect real data/i }),
    ).toHaveAttribute("data-tour-anchor", "sample_connect_real_data_button");
    expect(
      within(panel).getByRole("button", { name: /open sample trace/i }),
    ).toHaveAttribute("data-tour-anchor", "sample_trace_link");
    expect(controls.slice(0, 2)).toEqual([
      "Connect real data",
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
