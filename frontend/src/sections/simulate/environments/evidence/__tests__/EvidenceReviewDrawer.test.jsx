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
  transcript: {
    ...part("present"),
    turns: [
      { role: "user", text: "Refund my order", trace_id: "t1" },
      { role: "assistant", text: "Done", trace_id: "t1" },
    ],
  },
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
    // The design system's skeleton keeps the layout while it loads.
    expect(
      screen.getByRole("progressbar", { name: "Loading this import…" }),
    ).toBeInTheDocument();
    const card = await screen.findByTestId("evidence-case-c1");
    // Named by what the user asked, never by the source's id.
    expect(
      within(card).getByRole("heading", { name: "Refund my order" }),
    ).toBeInTheDocument();
    expect(card).not.toHaveTextContent("session-1");
    // Each reason is on the card, not behind a hover; step 2 explains the
    // starting state once.
    expect(card).toHaveTextContent("Has: Transcript; Labels");
    expect(card).toHaveTextContent(
      "Missing: Tool calls (No tool spans in this session); Tool inputs; Tool outputs; Starting state",
    );
    expect(card).not.toHaveTextContent("Observe never records it");
    expect(screen.getByText(/2 values of personal data/)).toBeInTheDocument();
    // Production evidence carries no simulated label.
    expect(screen.queryByTestId("evidence-simulated")).toBeNull();
    expect(within(card).queryByText(/^Simulated /)).toBeNull();
  });

  it("shows a long conversation's first and last turns and counts the rest", async () => {
    const turn = (role, text) => ({ role, text, trace_id: "t1" });
    evidence = bundle({
      cases: [
        item({
          transcript: {
            turns: [
              turn("assistant", "Hello, how can I help?"),
              turn(
                "user",
                `I was charged twice ${"for the same order ".repeat(8)}`,
              ),
              turn("assistant", "Let me look."),
              turn("user", "Thanks"),
              turn("assistant", "Refunded."),
            ],
          },
        }),
      ],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    // The first user message, cut at a word.
    expect(
      within(card).getByText(
        "I was charged twice for the same order for the same order for the same order for the same…",
      ),
    ).toBeInTheDocument();
    expect(card).toHaveTextContent("Agent: Hello, how can I help?");
    expect(card).toHaveTextContent("Agent: Refunded.");
    expect(card).not.toHaveTextContent("Let me look.");
    // The turns between open on request, and close again.
    await userEvent.click(
      within(card).getByRole("button", { name: "Show 3 more turns" }),
    );
    expect(card).toHaveTextContent("Agent: Let me look.");
    expect(card).toHaveTextContent("User: Thanks");
    await userEvent.click(
      within(card).getByRole("button", { name: "Show fewer turns" }),
    );
    expect(card).not.toHaveTextContent("Let me look.");
  });

  it("says each reason once: parts that share one are joined", async () => {
    const same = part("missing", "No tool calls were recorded.");
    evidence = bundle({
      cases: [
        item({ tool_calls: same, tool_inputs: same, tool_outputs: same }),
      ],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(card).toHaveTextContent(
      "Missing: Tool calls, Tool inputs and Tool outputs (No tool calls were recorded); Starting state",
    );
  });

  it("keeps Draft and Save in the drawer's footer, the next action primary", async () => {
    renderDrawer();
    const footer = await screen.findByTestId("evidence-review-footer");
    expect(
      within(footer).getByRole("button", { name: "Draft 1 scenario" }),
    ).toHaveClass("MuiButton-contained");
    expect(
      within(footer).getByRole("button", { name: "Save 0 to the suite" }),
    ).toHaveClass("MuiButton-outlined");
  });

  it("sets every waiting case at once, and drafts with that choice", async () => {
    evidence = bundle({ cases: [item(), item({ id: "c2" })] });
    axios.post.mockResolvedValue({ data: bundle({ drafting: true }) });
    renderDrawer();
    await screen.findByTestId("evidence-case-c2");
    await userEvent.click(
      screen.getByRole("combobox", { name: /^Set every waiting case to/ }),
    );
    await userEvent.click(
      screen.getByRole("option", {
        name: "Keep this success as a regression test",
      }),
    );
    for (const id of ["c1", "c2"]) {
      expect(
        within(screen.getByTestId(`evidence-case-${id}`)).getByRole("combobox"),
      ).toHaveTextContent("Keep this success as a regression test");
    }
    await userEvent.click(
      screen.getByRole("button", { name: "Draft 2 scenarios" }),
    );
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(`${BASE}draft/`, {
        case_ids: ["c1", "c2"],
        shows: { c1: "success", c2: "success" },
      }),
    );
  });

  it("names a case with no recorded conversation by its kind", async () => {
    evidence = bundle({
      source_kind: "call",
      cases: [item({ transcript: { turns: [] } })],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(
      within(card).getByText("Call with no recorded conversation"),
    ).toBeInTheDocument();
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

  it("makes no choice for a source that records no outcome", async () => {
    evidence = bundle({
      cases: [
        item({ labels: part("missing", "Nobody labelled this source.") }),
      ],
    });
    axios.post.mockResolvedValue({ data: bundle({ drafting: true }) });
    renderDrawer();
    const choice = await screen.findByRole("combobox", {
      name: /^What should this case's scenario be\?/,
    });
    expect(choice).toHaveTextContent(
      "Not set: the builder judges from the evidence",
    );
    expect(
      screen.getByText(/^Nothing here records how it ended/),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Draft 1 scenario" }),
    );
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(`${BASE}draft/`, {
        case_ids: ["c1"],
        shows: {},
      }),
    );
  });

  it("asks what the case's scenario should be, and drafts the chosen regression test", async () => {
    axios.post.mockResolvedValue({ data: bundle({ drafting: true }) });
    renderDrawer();
    const choice = await screen.findByRole("combobox", {
      name: /^What should this case's scenario be\?/,
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
      name: /^What should this case's scenario be\?/,
    });
    expect(choice).toHaveTextContent("Reproduce the failure as a test");
    await userEvent.click(choice);
    const options = screen
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toEqual([
      "Not set: the builder judges from the evidence",
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
    // The mark is fixed at import; the notice says how to change it.
    expect(screen.getByTestId("evidence-transcript")).toHaveTextContent(
      "Not marked as production, so these become tests, not regression tests. To change it, discard this and import again.",
    );
    expect(
      screen.getByText(
        "1 value of personal data was replaced with placeholders when the transcripts were stored.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/^A transcript doesn't hold the seed records/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Observe doesn't record/)).toBeNull();
    expect(
      screen.getByRole("combobox", {
        name: /^What should this case's scenario be\?/,
      }),
    ).toHaveTextContent("Reproduce the failure as a test");
  });

  it("reviews dataset rows as the person's, with each row's expected outcome", async () => {
    evidence = bundle({
      source_kind: "dataset",
      dataset_id: "d1",
      // A project on the import gives a row no Observe link: Observe never held it.
      project_id: "33333333-3333-3333-3333-333333333333",
      production: false,
      cases: [
        item({
          source_id: "row-1",
          name: "Row from Support evals",
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
    // Titled by what the user asked; its dataset beneath, with a way to open
    // it, and never the row id.
    expect(
      within(card).getByRole("heading", { name: "Refund my order" }),
    ).toBeInTheDocument();
    expect(card).toHaveTextContent("Row from Support evals · Open in dataset");
    expect(
      within(card).getByRole("link", { name: "Open in dataset" }),
    ).toHaveAttribute("href", "/dashboard/develop/d1?tab=data");
    expect(within(card).getAllByRole("link")).toHaveLength(1);
    expect(card).not.toHaveTextContent("row-1");
    expect(within(card).getByText("Dataset row")).toBeInTheDocument();
    expect(card).toHaveTextContent("Has: Transcript; Labels; Expected outcome");
    expect(
      within(card).getByTestId("evidence-expected-outcome"),
    ).toHaveTextContent("Expected outcome: Refuse the second refund.");
    // A row's label columns are context, not an outcome: no choice is made for
    // the person, and what the builder makes follows the expected outcome.
    const choice = within(card).getByRole("combobox", {
      name: /^What should this case's scenario be\?/,
    });
    expect(choice).toHaveTextContent(
      "Not set: the builder judges from the evidence",
    );
    expect(
      within(card).getByText(
        "Nothing here records how it ended. Keeps: what the row shows. Makes: checks from the expected outcome column.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(choice);
    await userEvent.click(
      screen.getByRole("option", { name: "Reproduce the failure as a test" }),
    );
    expect(
      within(card).getByText(
        /^Keeps: the situation.*Makes: what should have happened, from the expected outcome column\.$/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("1 row from a dataset")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-dataset")).toHaveTextContent(
      "Not marked as production, so these become tests, not regression tests.",
    );
    // Rows are read live, so redaction is what it will replace, not what it did.
    expect(
      screen.getByText(/will be replaced with placeholders/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/^A dataset row doesn't hold the seed records/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("evidence-transcript")).toBeNull();
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
      "They are yours, not Observe's.",
    );
    expect(
      screen.getByRole("combobox", {
        name: /^What should this case's scenario be\?/,
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
        name: /^What should this case's scenario be\?/,
      }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Save 0 to the suite" }),
    ).toBeDisabled();
    expect(
      screen.getByText(/^Up to 90 minutes; you can close this meanwhile\./),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Draft scenarios first, then add them here."),
    ).toBeNull();
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
    // Nothing to add, and the disabled add says why.
    expect(
      screen.getByRole("button", { name: "Save 0 to the suite" }),
    ).toBeDisabled();
    expect(
      screen.getByText("Draft scenarios first, then add them here."),
    ).toBeInTheDocument();
  });

  it("says in words how many more sources hold a case", async () => {
    evidence = bundle({ cases: [item({ also_from: 2 })] });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(
      within(card).getByText("Also from 2 more sources"),
    ).toBeInTheDocument();
  });

  it("shows a case imported before as already imported, opening that import, with no ids", async () => {
    const EARLIER = "33333333-3333-3333-3333-333333333333";
    const ORIGINAL = "44444444-4444-4444-4444-444444444444";
    evidence = bundle({
      cases: [
        item({
          status: "duplicate",
          duplicate_of: {
            import_id: EARLIER,
            case_id: ORIGINAL,
            scenario_name: "Refund asked twice",
          },
        }),
      ],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(within(card).getByText("Already imported")).toBeInTheDocument();
    expect(
      within(card).getByText(
        /Imported before; its scenario is Refund asked twice\./,
      ),
    ).toBeInTheDocument();
    expect(
      within(card).getByRole("link", { name: "Open the existing case" }),
    ).toHaveAttribute(
      "href",
      `/dashboard/simulate/environments/${ENV}?tab=scenarios&import=${EARLIER}`,
    );
    expect(card.textContent).not.toContain(EARLIER);
    expect(card.textContent).not.toContain(ORIGINAL);
    // It is proposed in that import: nothing here waits to be drafted.
    expect(
      screen.getByText("No case is waiting to be drafted."),
    ).toBeInTheDocument();
  });

  it("names a session's source and opens its traces in Observe", async () => {
    const PROJECT = "33333333-3333-3333-3333-333333333333";
    evidence = bundle({
      project_id: PROJECT,
      cases: [
        item({
          source_id: "3f2a91c0-aaaa-bbbb-cccc-dddddddddddd",
          started_at: "2026-11-10T09:00:00Z",
        }),
      ],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(within(card).getByText(/Session 3f2a91c0/)).toBeInTheDocument();
    const link = within(card).getByRole("link", { name: "Open in Observe" });
    const href = link.getAttribute("href");
    expect(href).toContain(`/dashboard/observe/${PROJECT}/llm-tracing`);
    expect(decodeURIComponent(href)).toContain(
      "3f2a91c0-aaaa-bbbb-cccc-dddddddddddd",
    );
  });

  it("opens a call's trace in Observe", async () => {
    const PROJECT = "33333333-3333-3333-3333-333333333333";
    evidence = bundle({
      project_id: PROJECT,
      source_kind: "call",
      cases: [item({ source_id: "9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e" })],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(within(card).getByText(/Call 9b8c7d6e/)).toBeInTheDocument();
    expect(
      within(card).getByRole("link", { name: "Open in Observe" }),
    ).toHaveAttribute(
      "href",
      `/dashboard/observe/${PROJECT}/trace/9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e`,
    );
  });

  it("names a source Observe no longer keeps, without a link", async () => {
    evidence = bundle({
      project_id: "33333333-3333-3333-3333-333333333333",
      cases: [item({ source_available: false, status: "unavailable" })],
    });
    renderDrawer();
    const card = await screen.findByTestId("evidence-case-c1");
    expect(within(card).getByText(/Session session-/)).toBeInTheDocument();
    expect(
      within(card).queryByRole("link", { name: "Open in Observe" }),
    ).not.toBeInTheDocument();
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
        item({
          status: "drafted",
          scenario_key: "evidence-abc123abc123",
          scenario_name: "Refund a double charge",
          new_checks: [{ name: "refund_issued", what: "a refund was issued" }],
        }),
      ],
    });
    axios.post.mockResolvedValue({
      data: {
        saved: true,
        evidence_import: bundle({ cases: [item({ status: "added" })] }),
      },
    });
    renderDrawer();
    // The scenario's title and what each new check checks, not their ids.
    const card = await screen.findByTestId("evidence-case-c1");
    expect(card).toHaveTextContent("Scenario: Refund a double charge");
    expect(card).toHaveTextContent(
      "New check added by this import: a refund was issued",
    );
    expect(card).not.toHaveTextContent("evidence-abc123abc123");
    expect(card).not.toHaveTextContent("refund_issued");
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
      /Until you save, Epoch can't write new variations\./,
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
      "1 case is in the unsaved changes. Press Save in the draft bar to make it live. Until you save, Epoch can't write new variations.",
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

  describe("opens a resumed import at the step it reached", () => {
    const scrolled = vi.fn();
    beforeEach(() => {
      scrolled.mockReset();
      Element.prototype.scrollIntoView = function scrollIntoView() {
        scrolled(this.textContent);
      };
    });

    it.each([
      [
        "drafted cases wait to be added",
        { cases: [item({ status: "drafted" })] },
        /^4\. Add to the suite/,
      ],
      [
        "drafting shows its progress",
        { drafting: true },
        /^3\. Drafted scenarios/,
      ],
      [
        "some added, the rest to draft",
        { cases: [item({ status: "added" }), item({ id: "c2" })] },
        /^3\. Drafted scenarios/,
      ],
    ])("%s", async (_name, overrides, step) => {
      evidence = bundle(overrides);
      renderDrawer();
      await screen.findByTestId("evidence-case-c1");

      await waitFor(() => expect(scrolled).toHaveBeenCalledTimes(1));
      expect(scrolled.mock.calls[0][0]).toMatch(step);
    });

    it("a waiting import starts at the top", async () => {
      renderDrawer();
      await screen.findByTestId("evidence-case-c1");

      expect(scrolled).not.toHaveBeenCalled();
    });
  });

  it("says when the import is gone, and offers no retry", async () => {
    axios.get.mockImplementation((url) =>
      url === BASE ? Promise.reject({ statusCode: 404 }) : answer(url),
    );
    renderDrawer();
    expect(
      await screen.findByText("This import is not available."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("says a failed load and retries it", async () => {
    let calls = 0;
    axios.get.mockImplementation((url) => {
      if (url !== BASE) return answer(url);
      calls += 1;
      return calls === 1
        ? Promise.reject({ statusCode: 500 })
        : Promise.resolve({ data: evidence });
    });
    renderDrawer();
    expect(
      await screen.findByText(
        "Couldn't load this import. Try again in a moment.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("evidence-case-c1")).toBeInTheDocument();
  });
});
