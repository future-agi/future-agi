import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

const selection = vi.hoisted(() => ({ selectAll: false, toggledNodes: [] }));
const dialog = vi.hoisted(() => ({ props: null }));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ dataset: "d1" }),
  useNavigate: () => vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  endpoints: { develop: {} },
}));
vi.mock("../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    refreshGrid: vi.fn(),
    gridApi: { current: { getGridOption: () => ({ totalRowCount: 40 }) } },
  }),
}));
vi.mock("../states", () => ({
  useDevelopSelectedRowsStoreShallow: (pick) =>
    pick({ ...selection, resetSelectedRows: vi.fn() }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("../DataTab/DataMenuList/DataMenuList", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/simulate/environments/evidence/CreateEnvironmentTestDialog",
  () => ({
    default: (props) => {
      dialog.props = props;
      return <div data-testid="create-environment-test" />;
    },
  }),
);

const { default: DevelopDataSelectionActive } = await import(
  "./DevelopDataSelectionActive"
);

const renderBar = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DevelopDataSelectionActive />
    </QueryClientProvider>,
  );

const action = () =>
  screen.getByRole("button", { name: "Create environment test" });

beforeEach(() => {
  selection.selectAll = false;
  selection.toggledNodes = ["r1", "r2"];
  dialog.props = null;
});

describe("DevelopDataSelectionActive: Create environment test", () => {
  it("opens the import for the picked rows of this dataset", async () => {
    renderBar();

    await userEvent.click(action());

    expect(screen.getByTestId("create-environment-test")).toBeInTheDocument();
    expect(dialog.props).toMatchObject({
      datasetId: "d1",
      sourceKind: "dataset",
      ids: ["r1", "r2"],
    });
  });

  it("is off under Select all, whose list names the rows left out", () => {
    selection.selectAll = true;
    renderBar();

    expect(action()).toBeDisabled();
  });

  it("is off above the backend's limit", () => {
    selection.toggledNodes = Array.from({ length: 21 }, (_, n) => `r${n}`);
    renderBar();

    expect(action()).toBeDisabled();
  });
});
