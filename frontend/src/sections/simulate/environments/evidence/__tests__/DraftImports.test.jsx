import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const { default: DraftImports } = await import("../DraftImports");

const ENV = "11111111-1111-1111-1111-111111111111";
const LIST = `/simulate/api/harness-environments/${ENV}/evidence-imports/`;
const item = (overrides = {}) => ({
  id: "22222222-2222-2222-2222-222222222222",
  source_kind: "session",
  source_name: "Support agent",
  source_available: true,
  case_count: 3,
  added_count: 0,
  status: "waiting",
  created_at: new Date().toISOString(),
  can_discard: true,
  ...overrides,
});

let listed;
const renderStrip = (onResume = vi.fn()) => {
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
      <DraftImports envId={ENV} onResume={onResume} />
    </QueryClientProvider>,
  );
  return { onResume };
};

beforeEach(() => {
  axios.get.mockReset();
  axios.delete.mockReset();
  listed = [item()];
  axios.get.mockImplementation((url) =>
    url === LIST
      ? Promise.resolve({ data: listed })
      : Promise.reject(new Error(`unexpected GET ${url}`)),
  );
});

describe("DraftImports", () => {
  it("shows nothing while no import is unfinished", async () => {
    listed = [];
    renderStrip();
    await waitFor(() => expect(axios.get).toHaveBeenCalledWith(LIST));
    expect(screen.queryByTestId("draft-imports")).toBeNull();
  });

  it("names each import's source and status, and resumes it", async () => {
    listed = [
      item(),
      item({
        id: "33333333-3333-3333-3333-333333333333",
        source_kind: "dataset",
        source_name: "Support evals",
        source_available: false,
        case_count: 1,
        status: "partly_added",
      }),
      item({
        id: "44444444-4444-4444-4444-444444444444",
        source_kind: "transcript",
        source_name: "Refund call",
        case_count: 2,
        status: "drafted",
      }),
      item({
        id: "55555555-5555-5555-5555-555555555555",
        source_kind: "transcript",
        source_name: null,
        case_count: 1,
        status: "blocked",
      }),
    ];
    const { onResume } = renderStrip();

    const strip = await screen.findByTestId("draft-imports");
    expect(strip).toHaveTextContent("Draft imports");
    // Nothing was saved: the time is when it was imported.
    expect(
      within(screen.getByTestId(`draft-import-${listed[0].id}`)).getByText(
        /^3 sessions from Support agent · waiting to be drafted · imported /,
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId(`draft-import-${listed[1].id}`)).getByText(
        /^1 row from Support evals \(source deleted\) · partly added · /,
      ),
    ).toBeInTheDocument();
    // A transcript import is named by its first transcript.
    expect(
      within(screen.getByTestId(`draft-import-${listed[2].id}`)).getByText(
        /^2 transcripts: Refund call and 1 more · drafted, not added · /,
      ),
    ).toBeInTheDocument();
    // Only blocked cases left: still listed, with the way to retry.
    expect(
      within(screen.getByTestId(`draft-import-${listed[3].id}`)).getByText(
        /^1 transcript · blocked: import again to retry · /,
      ),
    ).toBeInTheDocument();

    await userEvent.click(
      within(screen.getByTestId(`draft-import-${listed[1].id}`)).getByRole(
        "button",
        { name: "Resume" },
      ),
    );
    expect(onResume).toHaveBeenCalledWith(listed[1].id);
  });

  it("discards an import never added after a confirm, and lists again", async () => {
    axios.delete.mockResolvedValue({ data: undefined });
    renderStrip();
    const row = await screen.findByTestId(`draft-import-${listed[0].id}`);

    await userEvent.click(within(row).getByRole("button", { name: "Discard" }));
    const dialog = screen.getByRole("dialog");
    // The confirm names the import it discards.
    expect(dialog).toHaveTextContent("Discard “3 sessions from Support agent”?");
    expect(dialog).toHaveTextContent(
      "Its cases are removed, and nothing joins the suite. The sources stay in Observe.",
    );
    expect(axios.delete).not.toHaveBeenCalled();
    listed = [];
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Discard" }),
    );

    await waitFor(() =>
      expect(axios.delete).toHaveBeenCalledWith(`${LIST}${item().id}/`),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("draft-imports")).toBeNull(),
    );
  });

  it("says a partly added import keeps what was added", async () => {
    listed = [item({ case_count: 3, added_count: 2, status: "partly_added" })];
    renderStrip();
    const row = await screen.findByTestId(`draft-import-${listed[0].id}`);

    await userEvent.click(within(row).getByRole("button", { name: "Discard" }));

    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Its 1 case not added is discarded. The 2 already added stay in the suite.",
    );
  });

  it.each([
    [
      { source_kind: "transcript", source_name: null, case_count: 1 },
      "The transcripts' stored text is deleted with it.",
    ],
    [
      { source_kind: "dataset", source_name: "Support evals" },
      "The rows stay in Support evals.",
    ],
  ])("says what a discard does to its sources: %o", async (kind, says) => {
    listed = [item(kind)];
    renderStrip();
    const row = await screen.findByTestId(`draft-import-${listed[0].id}`);

    await userEvent.click(within(row).getByRole("button", { name: "Discard" }));

    expect(screen.getByRole("dialog")).toHaveTextContent(says);
    expect(screen.getByRole("dialog")).not.toHaveTextContent("stay in Observe");
  });

  it("cannot discard while the builder drafts", async () => {
    listed = [item({ status: "drafting", can_discard: false })];
    renderStrip();
    const row = await screen.findByTestId(`draft-import-${listed[0].id}`);

    expect(within(row).getByRole("button", { name: "Discard" })).toBeDisabled();
  });
});
