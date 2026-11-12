import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

const nav = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => nav.navigate,
}));
const envs = vi.hoisted(() => ({ rows: [], answer: null }));
vi.mock("src/api/simulate-environments/environments", () => ({
  useMyEnvironments: () => envs.answer ?? { data: { rows: envs.rows } },
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
const dataset = vi.hoisted(() => ({ columns: [] }));
vi.mock("src/api/develop/develop-detail", () => ({
  useDatasetColumnConfig: () => dataset.columns,
  getDatasetQueryOptions: (id) => ({
    queryKey: ["dataset", id],
    queryFn: () => Promise.resolve({}),
  }),
}));

const { default: axios } = await import("src/utils/axios");
const { default: CreateEnvironmentTestDialog } = await import(
  "../CreateEnvironmentTestDialog"
);

const ENV = "11111111-1111-1111-1111-111111111111";
const IMPORT = "22222222-2222-2222-2222-222222222222";

const renderDialog = (props = {}) => {
  const onClose = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <CreateEnvironmentTestDialog
        open
        onClose={onClose}
        projectId="p1"
        sourceKind="session"
        ids={["s1", "s2"]}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onClose };
};

const pickEnvironment = () =>
  fireEvent.change(screen.getByTestId("evidence-environment"), {
    target: { value: ENV },
  });

beforeEach(() => {
  axios.post.mockReset();
  nav.navigate.mockReset();
  envs.rows = [{ id: ENV, name: "Refunds" }];
  envs.answer = null;
  dataset.columns = [
    { field: "col-chat", headerName: "chat", dataType: "text" },
    { field: "col-expected", headerName: "expected", dataType: "text" },
    { field: "col-audio", headerName: "recording", dataType: "audio" },
  ];
});

const renderRows = () =>
  renderDialog({
    projectId: undefined,
    datasetId: "d1",
    sourceKind: "dataset",
    ids: ["r1", "r2"],
  });

const pick = (testId, value) =>
  fireEvent.change(screen.getByTestId(testId), { target: { value } });

describe("CreateEnvironmentTestDialog", () => {
  it("imports into the picked environment and opens its review", async () => {
    axios.post.mockResolvedValue({ data: { id: IMPORT, cases: [] } });
    const { onClose } = renderDialog();
    expect(
      screen.getByText(/2 sessions from this project/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create environment test" }),
    ).toBeDisabled();

    pickEnvironment();
    await userEvent.click(
      screen.getByRole("button", { name: "Create environment test" }),
    );

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(axios.post).toHaveBeenCalledWith(
      `/simulate/api/harness-environments/${ENV}/evidence-imports/`,
      { project_id: "p1", source_kind: "session", ids: ["s1", "s2"] },
    );
    const [to] = nav.navigate.mock.calls.at(-1);
    expect(to).toContain(ENV);
    expect(to).toContain("scenarios");
    expect(to).toMatch(new RegExp(`[?&]import=${IMPORT}$`));
  });

  it("keeps the dialog and says why when the import is refused", async () => {
    axios.post.mockRejectedValue({
      statusCode: 400,
      message: "These sessions are not in this project.",
    });
    const { onClose } = renderDialog();
    pickEnvironment();
    await userEvent.click(
      screen.getByRole("button", { name: "Create environment test" }),
    );

    expect(
      await screen.findByText("These sessions are not in this project."),
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(nav.navigate).not.toHaveBeenCalled();
  });

  it("says there is nothing to import into without an environment", () => {
    envs.rows = [];
    renderDialog();
    expect(
      screen.getByText("No environment to import into yet. Create one first."),
    ).toBeInTheDocument();
  });

  it("says it is loading, not that there is none, before the list answers", () => {
    envs.answer = { data: undefined, isError: false };
    renderDialog();
    expect(
      screen.getByRole("progressbar", { name: "Loading your environments…" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No environment to import into/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Create environment test" }),
    ).toBeDisabled();
  });

  it("says the list failed, not that there is none", () => {
    const refetch = vi.fn();
    envs.answer = { data: undefined, isError: true, refetch };
    renderDialog();
    expect(
      screen.getByText(/Couldn't load your environments/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No environment to import into/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("imports dataset rows through the picked columns, not as production unsaid", async () => {
    axios.post.mockResolvedValue({ data: { id: IMPORT, cases: [] } });
    const { onClose } = renderRows();
    expect(screen.getByText(/2 rows from this dataset/)).toBeInTheDocument();
    expect(
      screen.getByText(/Audio-only rows are not supported yet/),
    ).toBeInTheDocument();

    pickEnvironment();
    // No conversation column yet: nothing to read the rows through.
    expect(
      screen.getByRole("button", { name: "Create environment test" }),
    ).toBeDisabled();
    pick("evidence-conversation-column", "col-chat");
    await userEvent.click(
      screen.getByRole("button", { name: "Create environment test" }),
    );

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(axios.post).toHaveBeenCalledWith(
      `/simulate/api/harness-environments/${ENV}/evidence-imports/`,
      {
        source_kind: "dataset",
        dataset_id: "d1",
        ids: ["r1", "r2"],
        columns: { conversation: "col-chat", labels: [], expected: null },
        production: false,
      },
    );
    expect(nav.navigate.mock.calls.at(-1)[0]).toMatch(
      new RegExp(`[?&]import=${IMPORT}$`),
    );
  });

  it("sends the expected outcome column, and production only when ticked", async () => {
    axios.post.mockResolvedValue({ data: { id: IMPORT, cases: [] } });
    renderRows();
    pickEnvironment();
    pick("evidence-conversation-column", "col-chat");
    pick("evidence-expected-column", "col-expected");
    await userEvent.click(
      screen.getByRole("checkbox", { name: "These rows come from production" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create environment test" }),
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post.mock.calls[0][1]).toMatchObject({
      columns: { conversation: "col-chat", expected: "col-expected" },
      production: true,
    });
  });

  it("never offers an audio column as the conversation", () => {
    renderRows();
    pickEnvironment();
    pick("evidence-conversation-column", "col-audio");
    expect(
      screen.getByRole("button", { name: "Create environment test" }),
    ).toBeDisabled();
  });
});
