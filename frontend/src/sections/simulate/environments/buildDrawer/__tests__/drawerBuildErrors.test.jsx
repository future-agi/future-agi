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
const { default: RepoForm } = await import("../forms/RepoForm");
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

});
