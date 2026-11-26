import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return { ...actual, enqueueSnackbar: vi.fn() };
});

vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

const { preflightHarnessJob, storeHarnessSecretValues, createHarnessJob } =
  await import("src/api/harness/harness");
const { enqueueSnackbar } = await import("notistack");
const { default: HostedForm } = await import("../forms/HostedForm");
const { default: LiveKitForm } = await import("../forms/LiveKitForm");
const { CATALOG_ITEMS } = await import("../buildDrawerCatalog");
const { resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);

const PASS = {
  ready_to_submit: true,
  credentials: {
    scanned_files: 2,
    detected_connectors: [],
    requirements: [],
    credential_choices: [],
    probe: [],
  },
};
const UI_ONLY_NOTICE = /This flow isn't wired to the API yet/;

const itemOf = (id) => CATALOG_ITEMS.find((it) => it.id === id);
const render = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return baseRender(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
};
const box = (name) => screen.getByRole("textbox", { name });
const type = (name, value) =>
  fireEvent.change(box(name), { target: { value } });
const typeSecret = (placeholder, value) =>
  fireEvent.change(screen.getAllByPlaceholderText(placeholder)[0], {
    target: { value },
  });
const click = (role, name) => fireEvent.click(screen.getByRole(role, { name }));
const runPreflight = () => click("button", "Run preflight");
// A live preflight: what the harness was sent.
async function livePreflight() {
  const before = preflightHarnessJob.mock.calls.length;
  runPreflight();
  await waitFor(() =>
    expect(preflightHarnessJob).toHaveBeenCalledTimes(before + 1),
  );
  return preflightHarnessJob.mock.calls[before];
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  resetEnvironmentsStore();
  enqueueSnackbar.mockReset();
  preflightHarnessJob.mockReset();
  preflightHarnessJob.mockResolvedValue(PASS);
  storeHarnessSecretValues.mockReset();
  storeHarnessSecretValues.mockImplementation(async (values) => ({
    secret_refs: Object.fromEntries(
      Object.keys(values).map((k) => [k, `ref-${k}`]),
    ),
  }));
  createHarnessJob.mockReset();
  createHarnessJob.mockResolvedValue({ job: { job_id: "job-1" } });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Vapi and Retell: observability, UI only", () => {
  it("turns on and off with the design's copy, and is never sent", async () => {
    render(<HostedForm item={itemOf("retell")} />);
    typeSecret("Paste the key", "rk-1");
    type("Agent ID", "Agent_1");
    const toggle = screen.getByRole("checkbox", {
      name: "Enable observability",
    });
    expect(toggle).not.toBeChecked();
    expect(
      screen.getByText(
        "Off. Its production calls stay out of Observe. Turn it on later from the agent's Next steps.",
      ),
    ).toBeInTheDocument();
    const row = toggle.closest(".MuiStack-root");
    expect(within(row).getByText("UI only")).toBeInTheDocument();
    const off = await livePreflight();

    fireEvent.click(toggle);
    expect(toggle).toBeChecked();
    expect(
      screen.getByText(/Pull its production calls into Observe project/),
    ).toBeInTheDocument();
    expect(screen.getByText("agent-1")).toBeInTheDocument();
    expect(screen.getByText(/created when you connect/)).toBeInTheDocument();
    expect(screen.queryByText(UI_ONLY_NOTICE)).toBeNull();
    click("button", "Re-run");
    await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalledTimes(2));
    expect(preflightHarnessJob.mock.calls[1]).toEqual(off);
  });

  it("says conversations for a chat agent", () => {
    render(<HostedForm item={itemOf("retell")} />);
    click("button", /^Chat/);
    typeSecret("Paste the key", "rk-1");
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Enable observability" }),
    );
    expect(
      screen.getByText(/Pull its production conversations into Observe/),
    ).toBeInTheDocument();
  });
});

describe("LiveKit: room config and sessions, UI only", () => {
  const fill = () => {
    type("LiveKit server URL", "wss://demo.livekit.cloud");
    typeSecret("Paste the key", "lk-key");
    typeSecret("Paste the secret", "lk-secret");
    type("Agent name", "returns-agent");
    type("System prompt", "You handle returns.");
  };

  it("opens from its link, checks the JSON and the count, and sends neither", async () => {
    render(<LiveKitForm />);
    fill();
    const plain = await livePreflight();
    cleanup();
    preflightHarnessJob.mockClear();

    render(<LiveKitForm />);
    fill();
    expect(
      screen.queryByRole("textbox", { name: "Room config JSON" }),
    ).toBeNull();
    click("button", "Room config and sessions at once");
    expect(
      within(
        screen.getByRole("button", { name: "Hide room config and sessions" })
          .parentElement,
      ).getByText("UI only"),
    ).toBeInTheDocument();
    expect(box("Max sessions at once")).toHaveValue("5");
    type("Room config JSON", "{not json");
    expect(screen.getByText("Not valid JSON.")).toBeInTheDocument();
    type("Room config JSON", '{"metadata": {"locale": "en-US"}}');
    expect(screen.queryByText("Not valid JSON.")).toBeNull();
    type("Max sessions at once", "0");
    expect(screen.getByText("Between 1 and 100.")).toBeInTheDocument();
    type("Max sessions at once", "12a");
    expect(box("Max sessions at once")).toHaveValue("12");
    expect(screen.queryByText(UI_ONLY_NOTICE)).toBeNull();

    expect(await livePreflight()).toEqual(plain);

    click("button", "Hide room config and sessions");
    await waitFor(() =>
      expect(
        screen.queryByRole("textbox", { name: "Room config JSON" }),
      ).toBeNull(),
    );
  });
});
