import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Hoisted so the vi.mock factories below can reference them.
const { useParamsMock, getMock } = vi.hoisted(() => ({
  useParamsMock: vi.fn(() => ({})),
  getMock: vi.fn(),
}));

vi.mock("react-router", async (orig) => ({
  ...(await orig()),
  useParams: () => useParamsMock(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => getMock(...args) },
  endpoints: {
    project: { getUserExampleCode: () => "/projects/get_code_example/" },
  },
}));

// Stub the presentational children — we only care about the query.
vi.mock("src/sections/project/NewProject/InstructionCodeCopy", () => ({
  default: () => null,
}));
vi.mock("src/sections/project/NewProject/InstructionTitle", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop/AddDatasetDrawer/AddDatasetStyle", () => ({
  CustomTab: () => null,
  CustomTabs: () => null,
  TabWrapper: () => null,
}));

import UsersEmptyScreen from "../UsersEmptyScreen";

const renderScreen = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <UsersEmptyScreen />
    </QueryClientProvider>,
  );
};

describe("UsersEmptyScreen example-code query (TH-6081)", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue({ data: { result: {} } });
    useParamsMock.mockReturnValue({});
  });

  it("fetches the example code scoped to the project when on a project page", async () => {
    useParamsMock.mockReturnValue({ observeId: "proj-1" });
    renderScreen();
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    expect(getMock).toHaveBeenCalledWith("/projects/get_code_example/", {
      params: { project_id: "proj-1" },
    });
  });

  it("still fetches on the cross-project page (no observeId) — the query is not gated behind a project", async () => {
    useParamsMock.mockReturnValue({});
    renderScreen();
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    expect(getMock).toHaveBeenCalledWith("/projects/get_code_example/", {
      params: { project_id: undefined },
    });
  });
});
