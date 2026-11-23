import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BUILD_TAB from "./fixtures/buildTabRequests.json";
import {
  render as baseRender,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "src/utils/test-utils";

/*
  The drawer must send exactly what the old Build tab sent. The tab's requests
  were recorded from its panels (fixtures/buildTabRequests.json), last from
  dev's panels when environments started sending credential values write-only
  with preflight and create instead of exchanging them for secret refs. Each case fills the drawer's form with the values
  the recording used, runs preflight (and, for the repo, the build), and
  compares what it sends with the recording.
*/

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
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

const {
  preflightHarnessJob,
  storeHarnessSecretValues,
  createHarnessJob,
  uploadHarnessSource,
} = await import("src/api/harness/harness");
const { default: RepoForm } = await import("../forms/RepoForm");
const { default: HostedForm } = await import("../forms/HostedForm");
const { default: LiveKitForm } = await import("../forms/LiveKitForm");
const { default: PhoneForm } = await import("../forms/PhoneForm");
const { default: UploadForm } = await import("../forms/UploadForm");
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

const itemOf = (id) => CATALOG_ITEMS.find((it) => it.id === id);
const render = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return baseRender(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
};
const change = (el, value) => fireEvent.change(el, { target: { value } });
const byPlaceholder = (p) => screen.getAllByPlaceholderText(p)[0];
const box = (name) => screen.getByRole("textbox", { name });

// Runs one side and returns what it sent.
async function capture(ui, fill, { build = false } = {}) {
  resetEnvironmentsStore();
  preflightHarnessJob.mockClear();
  storeHarnessSecretValues.mockClear();
  createHarnessJob.mockClear();
  const view = render(ui);
  await fill(view);
  fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
  await waitFor(() => expect(preflightHarnessJob).toHaveBeenCalledTimes(1));
  if (build) {
    const go = await screen.findByRole("button", {
      name: /Build environment|Connect and build environment/,
    });
    await waitFor(() => expect(go).toBeEnabled());
    fireEvent.click(go);
    await waitFor(() => expect(createHarnessJob).toHaveBeenCalledTimes(1));
  }
  const sent = {
    preflight: preflightHarnessJob.mock.calls[0],
    secrets: storeHarnessSecretValues.mock.calls,
    create: createHarnessJob.mock.calls[0],
  };
  cleanup();
  return sent;
}

beforeEach(() => {
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
  uploadHarnessSource.mockReset();
  uploadHarnessSource.mockResolvedValue({
    source_id: "src-1",
    file_count: 1,
    total_bytes: 12,
    name: "agent",
  });
});
afterEach(cleanup);

describe("the drawer sends what the Build tab sent", () => {
  it("GitHub: private repo with every field, through to the build", async () => {
    const values = {
      repo: "https://github.com/acme/agent",
      branch: "release",
      entry: "src/main.py",
      install: "42",
      env: "OPENAI_API_KEY=sk-test\nLOG_LEVEL=info",
      egress: "api.example.com",
      scenarios: "25",
      parallel: "3",
    };
    const drawer = await capture(
      <RepoForm />,
      () => {
        change(box("Repository link"), values.repo);
        change(box("Branch or tag"), values.branch);
        change(box("Entry path (optional)"), values.entry);
        fireEvent.click(screen.getByRole("button", { name: "Private" }));
        change(box("GitHub App installation ID"), values.install);
        change(box("Environment values"), values.env);
        change(box("Additional egress domains"), values.egress);
        change(box("Scenarios to generate"), values.scenarios);
        fireEvent.click(
          screen.getByRole("checkbox", { name: "Enable observability" }),
        );
        change(box("Parallel worlds"), values.parallel);
      },
      { build: true },
    );
    expect(drawer.preflight).toEqual(BUILD_TAB.github.preflight);
    expect(drawer.secrets).toEqual(BUILD_TAB.github.secrets);
    expect(drawer.create).toEqual(BUILD_TAB.github.create);
  });

  it("Upload: folder, entry file and values", async () => {
    const file = new File(["print(1)"], "main.py", { type: "text/x-python" });
    Object.defineProperty(file, "webkitRelativePath", {
      value: "agent/main.py",
    });
    const pick = (container) =>
      fireEvent.change(
        container.querySelector('input[type="file"][multiple]'),
        { target: { files: [file] } },
      );
    const drawer = await capture(<UploadForm />, async ({ container }) => {
      pick(container);
      await waitFor(() => expect(box("Entry file")).toBeInTheDocument());
      await waitFor(() => expect(uploadHarnessSource).toHaveBeenCalled());
      change(box("Additional egress domains"), "api.example.com");
    });
    expect(drawer.preflight).toEqual(BUILD_TAB.upload.preflight);
  });

  it("Vapi: voice over the phone, outbound, speaking first, with a repo", async () => {
    const drawer = await capture(<HostedForm item={itemOf("vapi")} />, () => {
      change(byPlaceholder("Paste the key"), "vk-1");
      change(box("Assistant ID"), "asst_1");
      fireEvent.click(screen.getByRole("button", { name: "Phone" }));
      change(box("Contact Number"), "4155550142");
      fireEvent.click(screen.getByRole("checkbox", { name: "Inbound Calls" }));
      fireEvent.click(
        screen.getByRole("checkbox", { name: "Agent speaks first" }),
      );
      change(box("GitHub repo (optional)"), "https://github.com/acme/agent");
    });
    expect(drawer.preflight).toEqual(BUILD_TAB.vapi.preflight);
    expect(drawer.secrets).toEqual(BUILD_TAB.vapi.secrets);
  });

  it("Retell AI: voice on the web", async () => {
    const drawer = await capture(<HostedForm item={itemOf("retell")} />, () => {
      change(byPlaceholder("Paste the key"), "rk-1");
      change(box("Agent ID"), "agent_1");
    });
    expect(drawer.preflight).toEqual(BUILD_TAB.retellVoice.preflight);
    expect(drawer.secrets).toEqual(BUILD_TAB.retellVoice.secrets);
  });

  it("Retell AI: chat", async () => {
    const drawer = await capture(<HostedForm item={itemOf("retell")} />, () => {
      fireEvent.click(screen.getByRole("button", { name: /^Chat/ }));
      change(byPlaceholder("Paste the key"), "rk-2");
      change(box("Chat agent ID"), "agent_2");
    });
    expect(drawer.preflight).toEqual(BUILD_TAB.retellChat.preflight);
  });

  it("LiveKit: URL, keys, agent name, prompt, speaking first", async () => {
    const drawer = await capture(<LiveKitForm />, () => {
      change(box("LiveKit server URL"), "wss://demo.livekit.cloud");
      change(byPlaceholder("Paste the key"), "lk-key");
      change(byPlaceholder("Paste the secret"), "lk-secret");
      change(box("Agent name"), "returns-agent");
      change(box("System prompt"), "You handle returns.");
      fireEvent.click(
        screen.getByRole("checkbox", { name: "Agent speaks first" }),
      );
    });
    expect(drawer.preflight).toEqual(BUILD_TAB.livekit.preflight);
    expect(drawer.secrets).toEqual(BUILD_TAB.livekit.secrets);
  });

  it("Phone number (the Build tab's Others)", async () => {
    const drawer = await capture(<PhoneForm />, () => {
      change(box("Number to call"), "4155550142");
      change(box("Its instructions"), "You book tables.");
      fireEvent.click(
        screen.getByRole("checkbox", { name: "Agent speaks first" }),
      );
    });
    expect(drawer.preflight).toEqual(BUILD_TAB.phone.preflight);
  });
});
