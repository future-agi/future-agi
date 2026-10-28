import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, within, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderWithRouter } from "src/utils/test-utils";
import AddScenariosDrawer from "../../AddScenariosDrawer";

const extendHarnessJob = vi.fn();
vi.mock("src/api/harness/harness", async (orig) => ({
  ...(await orig()),
  extendHarnessJob: (...a) => extendHarnessJob(...a),
}));
const injectComposerScaffold = vi.fn();
vi.mock(
  "src/sections/simulate/environments/buildEnvironment/console/composerScaffoldBus",
  () => ({
    injectComposerScaffold: (...a) => injectComposerScaffold(...a),
    subscribeComposerScaffold: () => () => {},
  }),
);
const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (orig) => ({
  ...(await orig()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
  useSnackbar: () => ({ enqueueSnackbar }),
}));
// The routes with their own data have their own tests.
vi.mock("../ErrorFeedRoute", () => ({
  default: () => <div>error-feed-route</div>,
}));
vi.mock("../FailingCasesRoute", () => ({
  default: () => <div>failing-route</div>,
}));
vi.mock("../ProductionRoute", () => ({
  default: () => <div>production-route</div>,
}));
vi.mock("../DatasetRoute", () => ({ default: () => <div>dataset-route</div> }));
vi.mock("../ScriptRoute", () => ({ default: () => <div>script-route</div> }));

const onClose = vi.fn();
const renderDrawer = () =>
  renderWithRouter(
    <QueryClientProvider client={new QueryClient()}>
      <AddScenariosDrawer
        open
        onClose={onClose}
        env={{ id: "job-1", surface: "voice" }}
        envState={{}}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  extendHarnessJob.mockReset().mockResolvedValue({});
  injectComposerScaffold.mockClear();
  enqueueSnackbar.mockClear();
  onClose.mockClear();
});

describe("Add scenarios drawer", () => {
  it("leads with Generate, From Error Feed, From failing cases and From chat", () => {
    renderDrawer();
    ["Generate", "From Error Feed", "From failing cases", "From chat"].forEach(
      (t) =>
        expect(screen.getByRole("button", { name: t })).toBeInTheDocument(),
    );
  });

  it("Generate: Smoke 5, Standard 10, Deep audit 48; count capped at 50", () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(screen.getByText("~5")).toBeInTheDocument();
    expect(screen.getByText("~10")).toBeInTheDocument();
    expect(screen.getByText("~48")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Generate 10 scenarios" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("How many scenarios"), {
      target: { value: "120" },
    });
    expect(screen.getByText(/Capped at 50/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Generate 50 scenarios" }),
    ).toBeInTheDocument();
  });

  it("Generate calls extend with {count, guidance}; Focus goes in as guidance", async () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    fireEvent.click(screen.getByRole("radio", { name: /Smoke/ }));
    fireEvent.change(screen.getByLabelText("Focus"), {
      target: { value: "angry callers with payment failures" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Generate 5 scenarios" }),
    );
    await waitFor(() => expect(extendHarnessJob).toHaveBeenCalled());
    const [id, payload] = extendHarnessJob.mock.calls[0];
    expect(id).toBe("job-1");
    expect(payload.count).toBe(5);
    expect(payload.guidance).toBe("angry callers with payment failures");
    expect(payload.client_request_id).toBeTruthy();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("red-team packs add one guidance sentence per ticked pack; must-pass is a soon chip", async () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    fireEvent.click(screen.getByRole("radio", { name: /Smoke/ }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: /Safety & red-team tests/ }),
    );
    expect(screen.getByText("Must-pass soon")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /Prompt injection/ }));
    expect(
      screen.getByRole("button", { name: "Generate 15 scenarios" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Generate 15 scenarios" }),
    );
    await waitFor(() => expect(extendHarnessJob).toHaveBeenCalled());
    const { count, guidance } = extendHarnessJob.mock.calls[0][1];
    expect(count).toBe(15);
    expect(guidance.match(/Include 5 scenarios where/g)).toHaveLength(2);
    expect(guidance).toMatch(/Prompt injection/i);
  });

  it("says generated scenarios arrive in the list directly", () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(
      screen.getByText(/arrive in the list as they're written/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/proposals/)).toBeNull();
  });

  it("From chat hands the text to the builder composer", () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "From chat" }));
    fireEvent.change(
      screen.getByPlaceholderText(/Describe the callers or situations/),
      {
        target: { value: "Callers disputing a double charge" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Write them in chat" }));
    expect(injectComposerScaffold).toHaveBeenCalledWith(
      "Add scenarios. Callers disputing a double charge",
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("mounts the Error Feed and failing-cases routes", () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "From Error Feed" }));
    expect(screen.getByText("error-feed-route")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "From failing cases" }));
    expect(screen.getByText("failing-route")).toBeInTheDocument();
  });

  it("Production, Dataset and Script open under More sources", () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "More sources" }));
    fireEvent.click(screen.getByText("Production, word for word"));
    expect(screen.getByText("production-route")).toBeInTheDocument();
    fireEvent.click(screen.getByText("A dataset"));
    expect(screen.getByText("dataset-route")).toBeInTheDocument();
    fireEvent.click(screen.getByText("A script"));
    expect(screen.getByText("script-route")).toBeInTheDocument();
  });

  it("uses no em dashes", () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: /Safety & red-team tests/ }),
    );
    const drawer = screen.getByRole("presentation");
    expect(within(drawer).queryByText(/—/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/—/);
  });
});
