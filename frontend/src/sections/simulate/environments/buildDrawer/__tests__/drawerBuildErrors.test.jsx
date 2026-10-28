import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  waitFor,
} from "src/utils/test-utils";

/*
  A refused or failed create, through the drawer's forms: the server's words
  show, Build stays offered, a retry replays the same request key and a new
  preflight makes a new build with a new key. Ported from the Build tab's
  panel tests when the panels were removed.
*/

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const { enqueueSnackbar } = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", async () => ({
  ...(await vi.importActual("notistack")),
  enqueueSnackbar,
}));

let keySeq = 0;
vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: vi.fn(() => `idem-${(keySeq += 1)}`),
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

const { preflightHarnessJob, createHarnessJob } = await import(
  "src/api/harness/harness"
);
const { default: HostedForm } = await import("../forms/HostedForm");
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
const box = (name) => screen.getByRole("textbox", { name });
const runPreflight = () =>
  fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
const buildBtn = () =>
  screen.getByRole("button", {
    name: /Build environment|Connect and build environment/,
  });

beforeEach(() => {
  keySeq = 0;
  resetEnvironmentsStore();
  navigate.mockReset();
  enqueueSnackbar.mockReset();
  preflightHarnessJob.mockReset();
  preflightHarnessJob.mockResolvedValue(PASS);
  createHarnessJob.mockReset();
});

describe("a refused or failed create in the drawer", () => {
  it("shows why a private repository was refused at Build", async () => {
    const message =
      "Private GitHub repositories can't be versioned yet; use a public repository or upload the folder";
    createHarnessJob.mockRejectedValue({
      statusCode: 422,
      error: "github_private_unsupported",
      message,
    });
    render(<RepoForm />);
    change(box("Repository link"), "https://github.com/acme/agent");
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());

    fireEvent.click(buildBtn());

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(message, {
        variant: "error",
      }),
    );
    expect(navigate).not.toHaveBeenCalled();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
  });

  it("builds with the key write-only and retries a failed create under the same request key", async () => {
    createHarnessJob.mockRejectedValueOnce({
      error: "agent_preflight_failed",
      message: "The agent's credentials didn't pass their check",
      statusCode: 422,
    });
    render(<HostedForm item={itemOf("vapi")} />);
    change(screen.getAllByPlaceholderText("Paste the key")[0], "sk-x");
    change(box("Assistant ID"), "asst_x");
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "The agent's credentials didn't pass their check",
        { variant: "error" },
      ),
    );
    expect(createHarnessJob.mock.calls[0][0].credential_values).toEqual({
      VAPI_API_KEY: "sk-x",
    });

    createHarnessJob.mockRejectedValueOnce({
      detail: "Hosted harness scheduler is unavailable",
      statusCode: 503,
    });
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());
    await waitFor(() => expect(createHarnessJob).toHaveBeenCalledTimes(2));
    const [first, retry] = createHarnessJob.mock.calls.map((call) => call[1]);
    expect(retry).toBe(first);

    // A new preflight is a new build, so it gets a new key.
    createHarnessJob.mockResolvedValue({ job: { job_id: "job-plat" } });
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    change(screen.getAllByPlaceholderText("Paste the key")[0], "sk-y");
    runPreflight();
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    fireEvent.click(buildBtn());
    await waitFor(() => expect(createHarnessJob).toHaveBeenCalledTimes(3));
    expect(createHarnessJob.mock.calls[2][1]).not.toBe(first);
    expect(createHarnessJob.mock.calls[2][0].credential_values).toEqual({
      VAPI_API_KEY: "sk-y",
    });
  });
});
