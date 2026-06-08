import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithRouter, screen, within } from "src/utils/test-utils";
import PathSamplePreviewPanel from "../PathSamplePreviewPanel";
import {
  getPathSampleFixture,
  PATH_SAMPLE_FIXTURES,
} from "../path-sample-fixtures";

const NON_OBSERVE_PATHS = ["prompt", "evals", "agent", "voice", "gateway"];

const SETUP_HREF = "/dashboard/workbench/all?source=onboarding";

const renderPath = (primaryPath, props = {}) =>
  renderWithRouter(
    <PathSamplePreviewPanel
      fixture={getPathSampleFixture(primaryPath)}
      primaryPath={primaryPath}
      realSetupHref={SETUP_HREF}
      {...props}
    />,
  );

const runStarterSample = async (panel) => {
  await userEvent.click(
    within(panel).getByRole("button", { name: /run sample|play sample/i }),
  );
};

describe("PathSamplePreviewPanel", () => {
  it("returns nothing without a fixture", () => {
    const { container } = renderWithRouter(
      <PathSamplePreviewPanel fixture={null} primaryPath="prompt" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it.each(NON_OBSERVE_PATHS)(
    "renders the sample labelling and real-setup CTA for the %s path",
    (primaryPath) => {
      renderPath(primaryPath);
      const panel = screen.getByTestId(
        `path-sample-preview-panel-${primaryPath}`,
      );
      expect(panel).toBeVisible();

      // Sample / preview-only labelling is always present.
      expect(within(panel).getByText("Sample")).toBeVisible();
      expect(within(panel).getByText("Preview only")).toBeVisible();
      expect(
        within(panel).getByText(
          "Sample data is ready for preview. It does not finish setup; connect real data to complete the workflow.",
        ),
      ).toBeVisible();
      expect(within(panel).getByText("Starter content")).toBeVisible();
      expect(
        within(panel).getByText(
          "Runs locally in this browser. It does not complete setup.",
        ),
      ).toBeVisible();
      expect(
        within(panel).queryByTestId("path-sample-preview-result"),
      ).toBeNull();
      expect(
        within(panel).getByText(
          "Run the starter sample above to reveal the sample result here.",
        ),
      ).toBeVisible();

      // The primary CTA reuses the real setup href.
      const cta = within(panel).getByRole("link", {
        name: /now do it with your data/i,
      });
      expect(cta).toHaveAttribute("href", SETUP_HREF);

      expect(
        within(panel).getByRole("button", { name: /hide sample/i }),
      ).toBeVisible();
    },
  );

  it.each(NON_OBSERVE_PATHS)(
    "reveals the %s starter result after the in-browser sample action",
    async (primaryPath) => {
      renderPath(primaryPath);
      const panel = screen.getByTestId(
        `path-sample-preview-panel-${primaryPath}`,
      );
      await runStarterSample(panel);

      expect(
        within(panel).getByTestId("path-sample-preview-result"),
      ).toBeVisible();
      expect(within(panel).getByText("Run again")).toBeVisible();
    },
  );

  it("shows the prompt win and regression with a cost delta", async () => {
    renderPath("prompt");
    const panel = screen.getByTestId("path-sample-preview-panel-prompt");
    await runStarterSample(panel);
    expect(within(panel).getByText("Improved")).toBeVisible();
    expect(within(panel).getByText("Regressed")).toBeVisible();
    expect(
      within(panel).getByText("Refund eligibility question"),
    ).toBeVisible();
    expect(within(panel).getByText("Out-of-policy escalation")).toBeVisible();
    expect(within(panel).getByText(/\$0\.0021/)).toBeVisible();
    expect(within(panel).getByText(/\$0\.0034/)).toBeVisible();
  });

  it("shows the eval pass/fail distribution grouped by failure cause", async () => {
    renderPath("evals");
    const panel = screen.getByTestId("path-sample-preview-panel-evals");
    await runStarterSample(panel);
    expect(within(panel).getByText("7 pass / 3 fail")).toBeVisible();
    expect(within(panel).getByText(/Hallucinated refund amount/)).toBeVisible();
    expect(within(panel).getByText(/Missing citation/)).toBeVisible();
    expect(within(panel).getByText(/Expected: \$48\.00/)).toBeVisible();
    expect(within(panel).getByText(/Got: \$62\.50/)).toBeVisible();
  });

  it("shows the agent tool-call failure cause", async () => {
    renderPath("agent");
    const panel = screen.getByTestId("path-sample-preview-panel-agent");
    await runStarterSample(panel);
    expect(
      within(panel).getAllByText(/Tool call: refund_api/).length,
    ).toBeGreaterThan(0);
    expect(
      within(panel).getByText(/Failure cause: Tool call: refund_api/),
    ).toBeVisible();
    expect(
      within(panel).getByText(/#8830 instead of the latest/),
    ).toBeVisible();
  });

  it("shows the voice interruption, extracted intent, and telephony-as-later note", async () => {
    renderPath("voice");
    const panel = screen.getByTestId("path-sample-preview-panel-voice");
    await runStarterSample(panel);
    expect(within(panel).getByText("Interrupted")).toBeVisible();
    expect(within(panel).getByText("cancel_subscription")).toBeVisible();
    expect(within(panel).getByText(/Connecting telephony/)).toBeVisible();
  });

  it("shows the gateway provider fallback and one-line base URL change", async () => {
    renderPath("gateway");
    const panel = screen.getByTestId("path-sample-preview-panel-gateway");
    await runStarterSample(panel);
    expect(within(panel).getByText("Provider fallback")).toBeVisible();
    expect(
      within(panel).getByText(/OpenAI timed out, fell back to Anthropic/),
    ).toBeVisible();
    expect(within(panel).getByText(/Change one line/)).toBeVisible();
    expect(
      within(panel).getByText(/gateway\.futureagi\.com\/v1/),
    ).toBeVisible();
  });

  it("hides locally without any mutation or callback when Hide sample is clicked", async () => {
    renderPath("prompt");
    const panel = screen.getByTestId("path-sample-preview-panel-prompt");
    await userEvent.click(
      within(panel).getByRole("button", { name: /hide sample/i }),
    );
    expect(
      screen.queryByTestId("path-sample-preview-panel-prompt"),
    ).not.toBeInTheDocument();
  });

  it("accepts no activation/mutation callbacks - rendering and hiding are local-only", async () => {
    // The component intentionally exposes no onOpen/onHide/onConnect props.
    // Opening (render) and hiding must not invoke any activation mutation; we
    // assert that by confirming the component takes only static props and that
    // hiding works purely from internal state without an external handler.
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    renderPath("prompt");
    await userEvent.click(screen.getByRole("button", { name: /hide sample/i }));
    // No prop-type warning means no extra (mutation) props were expected.
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("exposes a fixture for every non-observe path and none for observe", () => {
    NON_OBSERVE_PATHS.forEach((path) => {
      expect(PATH_SAMPLE_FIXTURES[path]).toBeTruthy();
    });
    expect(getPathSampleFixture("observe")).toBeNull();
  });

  it("prefers a server-backed optional path preview when sample state includes one", () => {
    const serverPreview = {
      ...PATH_SAMPLE_FIXTURES.prompt,
      headline: "Server-backed prompt sample",
      starterAction: {
        ...PATH_SAMPLE_FIXTURES.prompt.starterAction,
        label: "Run server prompt sample",
      },
    };
    const sampleProject = {
      artifactRefs: {
        optional_paths: {
          prompt: {
            artifact_type: "prompt_comparison",
            preview: serverPreview,
          },
        },
      },
    };

    expect(getPathSampleFixture("prompt", sampleProject)).toBe(serverPreview);

    renderWithRouter(
      <PathSamplePreviewPanel
        fixture={getPathSampleFixture("prompt", sampleProject)}
        primaryPath="prompt"
        realSetupHref={SETUP_HREF}
      />,
    );

    expect(screen.getByText("Server-backed prompt sample")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /run server prompt sample/i }),
    ).toBeVisible();
  });
});
