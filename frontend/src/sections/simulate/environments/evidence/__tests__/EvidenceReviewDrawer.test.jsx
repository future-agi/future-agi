import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

const snackbar = vi.hoisted(() => ({ enqueue: vi.fn() }));
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: snackbar.enqueue }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const { default: EvidenceReviewDrawer } = await import(
  "../EvidenceReviewDrawer"
);

const ENV = "11111111-1111-1111-1111-111111111111";
const IMPORT = "22222222-2222-2222-2222-222222222222";
const BASE = `/simulate/api/harness-environments/${ENV}/evidence-imports/${IMPORT}/`;

const part = (status, reason = "") => ({ status, reason });
const parts = {
  transcript: part("present"),
  tool_calls: part("missing", "No tool spans in this session."),
  tool_inputs: part("missing"),
  tool_outputs: part("missing"),
  starting_state: part("missing", "Observe never records it."),
  labels: part("present"),
};
const item = (overrides = {}) => ({
  id: "c1",
  position: 0,
  source_id: "session-1",
  status: "pending",
  reason: "",
  source_available: true,
  changed_since_import: false,
  scenario_key: null,
  new_checks: [],
  transcript: {
    turns: [
      { role: "user", text: "Refund my order", trace_id: "t1" },
      { role: "assistant", text: "Done", trace_id: "t1" },
    ],
  },
  ...parts,
  ...overrides,
});
const bundle = (overrides = {}) => ({
  id: IMPORT,
  source_kind: "session",
  simulated: false,
  production: true,
  drafting: false,
  cases: [item()],
  redaction: { method: "presidio", reason: "", findings: [], total: 2 },
  ...overrides,
});

let drafts;
let evidence;
const answer = (url) => {
  if (url === BASE) return Promise.resolve({ data: evidence });
  if (url.endsWith("/drafts/")) return Promise.resolve({ data: drafts });
  return Promise.reject(new Error(`unexpected GET ${url}`));
};

const renderDrawer = ({ onClose = () => {} } = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <EvidenceReviewDrawer
        envId={ENV}
        importId={IMPORT}
        open
        onClose={onClose}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  axios.get.mockReset();
  axios.post.mockReset();
  snackbar.enqueue.mockReset();
  drafts = {};
  evidence = bundle();
  axios.get.mockImplementation(answer);
});

describe("EvidenceReviewDrawer", () => {
  it("says what each source holds and lacks, and what redaction replaces", async () => {
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(within(card).getByText("Transcript: present")).toBeInTheDocument();
    expect(within(card).getByText("Tool calls: missing")).toBeInTheDocument();
    expect(
      within(card).getByText("Starting state: missing"),
    ).toBeInTheDocument();
    expect(screen.getByText(/2 values of personal data/)).toBeInTheDocument();
    // Production evidence carries no simulated label.
    expect(screen.queryByTestId("evidence-simulated")).toBeNull();
    expect(within(card).queryByText(/^Simulated /)).toBeNull();
  });

  it("warns when only basic redaction ran", async () => {
    evidence = bundle({
      redaction: {
        method: "basic",
        reason: "Names are not redacted on this deployment.",
        findings: [],
        total: 0,
      },
    });
    renderDrawer();
    expect(
      await screen.findByText("Names are not redacted on this deployment."),
    ).toBeInTheDocument();
  });

  it("drafts the waiting cases with what each record shows", async () => {
    axios.post.mockResolvedValue({ data: bundle({ drafting: true }) });
    renderDrawer();
    const button = await screen.findByRole("button", {
      name: "Draft 1 scenario",
    });
    await userEvent.click(button);
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(`${BASE}draft/`, {
        case_ids: ["c1"],
        shows: { c1: "failure" },
      }),
    );
  });

  it("asks what the new scenarios should be, and drafts the chosen regression test", async () => {
    axios.post.mockResolvedValue({ data: bundle({ drafting: true }) });
    renderDrawer();
    const choice = await screen.findByRole("combobox", {
      name: /^What should the new scenarios be\?/,
    });
    expect(choice).toHaveTextContent(
      "Reproduce the failure as a regression test",
    );
    expect(
      screen.getByText(/^Keeps: the situation, what the user said/),
    ).toBeInTheDocument();
    await userEvent.click(choice);
    await userEvent.click(
      screen.getByRole("option", {
        name: "Keep this success as a regression test",
      }),
    );
    expect(
      screen.getByText(/^Keeps: the workflow that worked, step by step/),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Draft 1 scenario" }),
    );
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(`${BASE}draft/`, {
        case_ids: ["c1"],
        shows: { c1: "success" },
      }),
    );
  });

  it("labels a Simulator project's calls as simulated, not production", async () => {
    evidence = bundle({
      source_kind: "call",
      simulated: true,
      production: false,
      cases: [item(), item({ id: "c2", source_id: "call-2" })],
    });
    renderDrawer();
    expect(await screen.findByTestId("evidence-simulated")).toHaveTextContent(
      "These calls come from a Simulator project",
    );
    for (const id of ["c1", "c2"]) {
      expect(
        within(screen.getByTestId(`evidence-case-${id}`)).getByText(
          "Simulated call, not production",
        ),
      ).toBeInTheDocument();
    }
    // Simulated evidence makes tests, never regression tests.
    const [choice] = screen.getAllByRole("combobox", {
      name: /^What should the new scenarios be\?/,
    });
    expect(choice).toHaveTextContent("Reproduce the failure as a test");
    await userEvent.click(choice);
    const options = screen
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toEqual([
      "Reproduce the failure as a test",
      "Keep this success as a test",
      "A test from partial evidence",
    ]);
  });

  it("labels a transcript as the person's, never as Observe's, and makes tests", async () => {
    evidence = bundle({
      source_kind: "transcript",
      production: false,
      redaction: { method: "presidio", reason: "", findings: [], total: 1 },
      cases: [
        item({
          source_id: "transcript-1",
          name: "Escalation <EMAIL_ADDRESS>",
          tool_calls: part("missing", "A transcript carries no tool calls."),
        }),
      ],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(
      within(card).getByText("Escalation <EMAIL_ADDRESS>"),
    ).toBeInTheDocument();
    expect(within(card).getByText("Your transcript")).toBeInTheDocument();
    expect(within(card).queryByText(/^Simulated /)).toBeNull();
    expect(screen.getByText("1 transcript you added")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-transcript")).toHaveTextContent(
      "You did not mark these transcripts as production",
    );
    expect(
      screen.getByText(
        "1 value of personal data was replaced with tokens when the transcripts were stored.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/^A transcript never records the records/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Observe never records/)).toBeNull();
    expect(
      screen.getByRole("combobox", {
        name: /^What should the new scenarios be\?/,
      }),
    ).toHaveTextContent("Reproduce the failure as a test");
  });

  it("reviews dataset rows as the person's, with each row's expected outcome", async () => {
    evidence = bundle({
      source_kind: "dataset",
      production: false,
      cases: [
        item({
          source_id: "row-1",
          tool_calls: part("missing", "A dataset row carries no tool calls."),
          expected_outcome: {
            ...part("present"),
            text: "Refuse the second refund.",
          },
        }),
      ],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(within(card).getByText("Dataset row")).toBeInTheDocument();
    expect(
      within(card).getByText("Expected outcome: present"),
    ).toBeInTheDocument();
    expect(
      within(card).getByTestId("evidence-expected-outcome"),
    ).toHaveTextContent("Expected outcome: Refuse the second refund.");
    expect(screen.getByText("1 row from a dataset")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-dataset")).toHaveTextContent(
      "You did not mark these rows as production",
    );
    // Rows are read live, so redaction is what it will replace, not what it did.
    expect(
      screen.getByText(/will be replaced with tokens/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/^A dataset row never records the records/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("evidence-transcript")).toBeNull();
    expect(
      screen.getByRole("combobox", {
        name: /^What should the new scenarios be\?/,
      }),
    ).toHaveTextContent("Reproduce the failure as a test");
  });

  it("says a deleted row's provenance is the dataset's, not Observe's", async () => {
    evidence = bundle({
      source_kind: "dataset",
      cases: [
        item({
          status: "unavailable",
          reason: "The row or its dataset was deleted.",
          source_available: false,
        }),
      ],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(
      within(card).getByText(/the dataset no longer holds this row/),
    ).toBeInTheDocument();
    expect(within(card).queryByText(/Observe no longer keeps/)).toBeNull();
  });

  it("shows no expected outcome on an Observe source", async () => {
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(within(card).queryByText(/^Expected outcome/)).toBeNull();
  });

  it("a transcript said to be from production makes regression tests", async () => {
    evidence = bundle({
      source_kind: "transcript",
      production: true,
      cases: [item({ source_id: "transcript-1", name: "Escalation 7" })],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(
      within(card).getByText("Your transcript, from production"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("evidence-transcript")).toHaveTextContent(
      "They are your transcripts, not recordings Observe made.",
    );
    expect(
      screen.getByRole("combobox", {
        name: /^What should the new scenarios be\?/,
      }),
    ).toHaveTextContent("Reproduce the failure as a regression test");
  });

  it("while drafting: says so, takes no draft or add, and locks the record choice", async () => {
    evidence = bundle({ drafting: true });
    renderDrawer();
    expect(await screen.findByTestId("evidence-drafting")).toHaveTextContent(
      "Drafting 1 imported case",
    );
    expect(screen.queryByRole("button", { name: /^Draft / })).toBeNull();
    expect(
      screen.queryByRole("combobox", {
        name: /^What should the new scenarios be\?/,
      }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Save 0 to the suite" }),
    ).toBeDisabled();
  });

  it("shows a blocked case as blocked, with its reason", async () => {
    evidence = bundle({
      cases: [
        item({
          status: "blocked",
          reason: "The draft changed the environment beyond new checks.",
        }),
      ],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(within(card).getByText("Blocked")).toBeInTheDocument();
    expect(
      within(card).getByText(
        "The draft changed the environment beyond new checks.",
      ),
    ).toBeInTheDocument();
    expect(within(card).queryByText(/Added|Drafted/)).toBeNull();
    expect(
      screen.getByText("No case is waiting to be drafted."),
    ).toBeInTheDocument();
  });

  it("says when Observe no longer keeps a source", async () => {
    evidence = bundle({
      cases: [item({ status: "unavailable", source_available: false })],
    });
    renderDrawer();
    expect(
      await screen.findByText(/Provenance unavailable/),
    ).toBeInTheDocument();
  });

  it("saves drafted cases to the suite when nothing else is unsaved", async () => {
    evidence = bundle({
      cases: [
        item({ status: "drafted", scenario_key: "evidence-abc123abc123" }),
      ],
    });
    axios.post.mockResolvedValue({
      data: {
        saved: true,
        evidence_import: bundle({ cases: [item({ status: "added" })] }),
      },
    });
    renderDrawer();
    expect(
      await screen.findByText("evidence-abc123abc123"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("evidence-beside")).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Save 1 to the suite" }),
    );
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        `${BASE}add/`,
        expect.objectContaining({
          case_ids: ["c1"],
          expected_drafts: {
            environment: { id: null, revision: 0 },
            suite: { id: null, revision: 0 },
          },
        }),
      ),
    );
    await waitFor(() =>
      expect(snackbar.enqueue).toHaveBeenCalledWith(
        "1 case added to the suite",
        {
          variant: "success",
        },
      ),
    );
  });

  it("adds beside open changes, then points to the draft bar's Save", async () => {
    drafts = { suite: { id: "s1", label: "draft", revision: 4 } };
    evidence = bundle({
      cases: [
        item({ status: "drafted", scenario_key: "evidence-abc123abc123" }),
      ],
    });
    axios.post.mockResolvedValue({
      data: {
        saved: false,
        evidence_import: bundle({
          cases: [
            item({ status: "added", scenario_key: "evidence-abc123abc123" }),
          ],
        }),
      },
    });
    const onClose = vi.fn();
    renderDrawer({ onClose });
    expect(await screen.findByTestId("evidence-beside")).toHaveTextContent(
      /Fix my agent cannot write new variations/,
    );
    expect(screen.queryByRole("button", { name: "Go to Save" })).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Add 1 to unsaved changes" }),
    );
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        `${BASE}add/`,
        expect.objectContaining({
          expected_drafts: {
            environment: { id: null, revision: 0 },
            suite: { id: "s1", revision: 4 },
          },
        }),
      ),
    );
    // The cases are added, not live: the notice stays and leads to Save.
    expect(await screen.findByTestId("evidence-beside")).toHaveTextContent(
      /1 case is in the unsaved changes\. Press Save in the draft bar/,
    );
    await userEvent.click(screen.getByRole("button", { name: "Go to Save" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("explains a refused add", async () => {
    evidence = bundle({
      cases: [item({ status: "drafted", scenario_key: "evidence-x" })],
    });
    axios.post.mockRejectedValue({ statusCode: 409, error: "draft_changed" });
    renderDrawer();
    await userEvent.click(
      await screen.findByRole("button", { name: "Save 1 to the suite" }),
    );
    expect(
      await screen.findByText(
        /The unsaved changes moved since this review opened/,
      ),
    ).toBeInTheDocument();
  });

  it("says when the import is gone", async () => {
    axios.get.mockImplementation((url) =>
      url === BASE ? Promise.reject({ statusCode: 404 }) : answer(url),
    );
    renderDrawer();
    expect(
      await screen.findByText("This import is not available."),
    ).toBeInTheDocument();
  });
});
