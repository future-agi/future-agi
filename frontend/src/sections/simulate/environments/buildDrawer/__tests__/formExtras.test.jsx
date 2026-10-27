import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

/*
  The parts of Vel's forms with no API yet: each works on sample data, a
  UI-only choice never reaches the harness, and switching back sends exactly
  what the live form sent before.
*/

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
const { default: PhoneForm } = await import("../forms/PhoneForm");
const { default: RepoForm } = await import("../forms/RepoForm");
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
const wait = (ms) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });
const pickOption = (combobox, option) => {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: combobox }));
  fireEvent.click(screen.getByRole("option", { name: option }));
};

// A live preflight: what the harness was sent.
async function livePreflight() {
  const before = preflightHarnessJob.mock.calls.length;
  runPreflight();
  await waitFor(() =>
    expect(preflightHarnessJob).toHaveBeenCalledTimes(before + 1),
  );
  return preflightHarnessJob.mock.calls[before];
}

// A UI-only preflight and build: sample checks, a toast, no harness call.
async function sampleRunAndBuild() {
  const before = preflightHarnessJob.mock.calls.length;
  const secretsBefore = storeHarnessSecretValues.mock.calls.length;
  runPreflight();
  await wait(1000);
  expect(await screen.findByText("Ready to build")).toBeInTheDocument();
  click("button", /Connect and build environment/);
  expect(enqueueSnackbar).toHaveBeenCalledWith(
    expect.stringMatching(/^UI only/),
    expect.anything(),
  );
  expect(preflightHarnessJob).toHaveBeenCalledTimes(before);
  expect(storeHarnessSecretValues).toHaveBeenCalledTimes(secretsBefore);
  expect(createHarnessJob).not.toHaveBeenCalled();
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

describe("GitHub: Connect GitHub, UI only", () => {
  it("installs the app, picks a repository and an agent from its scan", async () => {
    render(<RepoForm />);
    expect(screen.queryByText(UI_ONLY_NOTICE)).toBeNull();
    click("radio", "Connect GitHub");
    expect(
      screen.getByRole("radio", { name: "Connect GitHub" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(UI_ONLY_NOTICE)).toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: "Repository link" }),
    ).toBeNull();
    expect(
      screen.getByText("Install the GitHub App to pick the agent"),
    ).toBeInTheDocument();

    click("button", "Install GitHub App");
    expect(
      screen.getByText("· from GitHub (acme-inc and acme-labs)"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Repository" }),
    ).toHaveTextContent("Choose a repository");

    pickOption("Repository", /acme-inc\/billing-copilot/);
    expect(screen.getByText(/for agents…/)).toBeInTheDocument();
    expect(
      screen.getByText("Wait for the repository to be read"),
    ).toBeInTheDocument();
    await wait(1300);
    expect(
      screen.getByText("We found 2 agents. Which one do you mean?"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Choose the agent or name its entry file"),
    ).toBeInTheDocument();

    click("radio", "Dispute handler");
    expect(
      screen.getByRole("radio", { name: "Dispute handler" }),
    ).toHaveAttribute("aria-checked", "true");
    await sampleRunAndBuild();
  });

  it("names an entry file instead, searches the list and changes the branch", async () => {
    render(<RepoForm />);
    click("radio", "Connect GitHub");
    click("button", "Install GitHub App");
    pickOption("Repository", /acme-inc\/support-voice-agent/);
    await wait(1300);
    fireEvent.change(box("Search the agents found"), {
      target: { value: "warranty" },
    });
    expect(
      screen.getByRole("radio", { name: "Warranty claims" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("radio", { name: "Shipping updates" }),
    ).toBeNull();
    expect(screen.getByText("1 of 7")).toBeInTheDocument();

    click("button", "Not in the list? Name its entry file");
    type("Entry file", "agents/custom/agent.py");
    expect(
      screen.queryByText("Choose the agent or name its entry file"),
    ).toBeNull();

    click("button", "Change");
    pickOption("Branch", /develop/);
    expect(screen.getByText(/for agents…/)).toBeInTheDocument();
    await wait(1300);
    expect(screen.getByText("develop")).toBeInTheDocument();
  });

  it("asks for the entry file when nothing is found, and retries a failed read", async () => {
    render(<RepoForm />);
    click("radio", "Connect GitHub");
    click("button", "Install GitHub App");
    pickOption("Repository", /acme-inc\/web-frontend/);
    await wait(1300);
    expect(screen.getByText(/No agent found in/)).toBeInTheDocument();
    expect(box("Entry file")).toBeInTheDocument();

    pickOption("Repository", /acme-labs\/notebooks/);
    await wait(1300);
    expect(screen.getByText(/Couldn't read/)).toBeInTheDocument();
    click("button", "Retry");
    await wait(1300);
    expect(screen.getByText(/No agent found in/)).toBeInTheDocument();
  });

  it("never reaches the harness, and pasting the link again sends what it sent before", async () => {
    render(<RepoForm />);
    type("Repository link", "https://github.com/acme/agent");
    const first = await livePreflight();

    click("radio", "Connect GitHub");
    click("button", "Install GitHub App");
    pickOption("Repository", /acme-inc\/travel-chat/);
    await wait(1300);
    click("radio", "Rebooking assistant");
    await sampleRunAndBuild();

    click("radio", "Paste a repo link");
    expect(screen.queryByText(UI_ONLY_NOTICE)).toBeNull();
    expect(box("Repository link")).toHaveValue("https://github.com/acme/agent");
    expect(await livePreflight()).toEqual(first);
  });
});

describe("GitHub: a private repository by access token, UI only", () => {
  it("asks for the token, then the app path is live again", async () => {
    render(<RepoForm />);
    type("Repository link", "https://github.com/acme/agent");
    click("button", "Private");
    type("GitHub App installation ID", "42");
    const first = await livePreflight();

    click("radio", "Use an access token");
    expect(screen.getByText(UI_ONLY_NOTICE)).toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: "GitHub App installation ID" }),
    ).toBeNull();
    expect(screen.getByText("Add the access token")).toBeInTheDocument();
    typeSecret("Paste the token", "ghp_sample");
    await sampleRunAndBuild();

    click("radio", "Install the GitHub App");
    expect(screen.queryByText(UI_ONLY_NOTICE)).toBeNull();
    expect(box("GitHub App installation ID")).toHaveValue("42");
    expect(await livePreflight()).toEqual(first);
  });

  it("is where Can't install the app leads", () => {
    render(<RepoForm />);
    click("radio", "Connect GitHub");
    click("button", /Can't install the app\?/);
    expect(
      screen.getByRole("radio", { name: "Paste a repo link" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: "Private" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("radio", { name: "Use an access token" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Access token or deploy key")).toBeInTheDocument();
  });
});

describe("Vapi: chat, UI only", () => {
  it("asks for the key and the assistant ID, with no contact, and voice is live again", async () => {
    render(<HostedForm item={itemOf("vapi")} />);
    typeSecret("Paste the key", "vk-1");
    type("Assistant ID", "asst_1");
    const first = await livePreflight();

    click("button", /^Chat/);
    expect(screen.getByText(UI_ONLY_NOTICE)).toBeInTheDocument();
    expect(screen.getByText("Vapi API key")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Phone" })).toBeNull();
    expect(screen.getByText("Add the assistant ID")).toBeInTheDocument();
    type("Assistant ID", "asst_chat");
    await sampleRunAndBuild();

    click("button", /^Voice/);
    expect(screen.queryByText(UI_ONLY_NOTICE)).toBeNull();
    type("Assistant ID", "asst_1");
    expect(await livePreflight()).toEqual(first);
  });
});

describe("Vapi and Retell: observability, UI only", () => {
  it("turns on and off with Vel's copy, and is never sent", async () => {
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

describe("Phone number: outbound, UI only", () => {
  it("names the caller ID, gives the test number, and inbound is live again", async () => {
    render(<PhoneForm />);
    type("Number to call", "4155550142");
    type("Its instructions", "You book tables.");
    const first = await livePreflight();

    click("button", "Outbound");
    expect(screen.getByText(UI_ONLY_NOTICE)).toBeInTheDocument();
    expect(box("Number it calls from")).toHaveValue("4155550142");
    expect(
      screen.getByText(
        "Its caller ID, how we know a call on the test number is your agent.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Test number your agent calls"),
    ).toBeInTheDocument();
    expect(screen.getByText("+1 415 555 0186")).toBeInTheDocument();
    expect(
      screen.getByText("Point your agent's outbound calls here for a run."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Agent speaks first" }),
    ).toBeChecked();
    await sampleRunAndBuild();

    click("button", "Inbound");
    expect(screen.queryByText(UI_ONLY_NOTICE)).toBeNull();
    expect(box("Number to call")).toHaveValue("4155550142");
    expect(
      screen.getByRole("checkbox", { name: "Agent speaks first" }),
    ).not.toBeChecked();
    expect(await livePreflight()).toEqual(first);
  });
});

describe("Phone number: steps, UI only", () => {
  it("writes the instructions as steps, adds and removes them, and one prompt is live again", async () => {
    render(<PhoneForm />);
    type("Number to call", "4155550142");
    type("Its instructions", "You book tables.");
    const first = await livePreflight();

    click("button", "Steps");
    expect(screen.getByText(UI_ONLY_NOTICE)).toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: "Its instructions" }),
    ).toBeNull();
    expect(box("Step 1 name")).toHaveValue("Greet and identify");
    expect(box("Step 2 name")).toHaveValue("");
    expect(screen.queryByRole("button", { name: /Remove step/ })).toBeNull();
    expect(
      screen.getByText(/Each step becomes one of the agent's members/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Name each step and say what it does"),
    ).toBeInTheDocument();

    click("button", "Add step");
    expect(box("Step 3 name")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Remove step/ })).toHaveLength(
      3,
    );
    click("button", "Remove step 3");
    expect(screen.queryByRole("textbox", { name: "Step 3 name" })).toBeNull();

    type("Step 1 instructions", "Greet the caller and take their name.");
    type("Step 2 name", "Book the table");
    type("Step 2 instructions", "Find a slot and confirm it.");
    type("Step 2 then", "End the call");
    await sampleRunAndBuild();

    click("button", "One prompt");
    expect(screen.queryByText(UI_ONLY_NOTICE)).toBeNull();
    expect(box("Its instructions")).toHaveValue("You book tables.");
    expect(await livePreflight()).toEqual(first);
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
    expect(screen.getByText("UI only")).toBeInTheDocument();
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
