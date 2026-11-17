import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { variablesOf } from "src/sections/common/EvalTypes/dagModel";
import DagRunPanel from "../DagRunPanel";
import DagTestPanel from "../../types/DagTestPanel";
import DagCheckFields from "../../types/DagCheckFields";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    develop: {
      getDatasetDetail: () => "/table/",
      w7: { dagExecuteAdhoc: "/adhoc/" },
    },
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws-a" }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds", name: "Replies" }],
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const child = "0b8e8f8e-6f55-4a39-9c7c-1d2b9f0c1a11";
const dag = {
  root: "n1",
  output_type: "score",
  nodes: [
    {
      id: "n1",
      kind: "eval",
      template_id: child,
      answers: [
        { label: "Pass", next: "a" },
        { label: "Fail", next: "a" },
      ],
    },
    { id: "a", kind: "leaf", label: "Near", score: 0.499 },
  ],
};
function mount(ui) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {ui}
    </QueryClientProvider>,
  );
}
async function pick(label, option) {
  fireEvent.mouseDown(
    screen.getByRole("combobox", { name: (name) => name.startsWith(label) }),
  );
  fireEvent.click(
    await screen.findByRole("option", { name: option, exact: true }),
  );
}
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("resolved tree inputs", () => {
  it("requires resolved child keys in typed tests before allowing execution", async () => {
    let resolve;
    axios.post.mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    mount(<DagTestPanel dag={dag} threshold={0.5} />);
    fireEvent.click(screen.getByRole("button", { name: "Type values" }));
    fireEvent.change(screen.getByLabelText("Sample output"), {
      target: { value: "answer" },
    });
    expect(
      screen.getByRole("button", { name: "Test", exact: true }),
    ).toBeDisabled();
    await waitFor(() => expect(resolve).toBeTypeOf("function"));
    resolve({ data: { result: { required_keys: ["output", "expected"] } } });
    const expected = await screen.findByLabelText("{{expected}}");
    expect(
      screen.getByRole("button", { name: "Test", exact: true }),
    ).toBeDisabled();
    fireEvent.change(expected, { target: { value: "reference" } });
    axios.post.mockResolvedValue({
      data: {
        result: { path: [], reason: "Scored", score: 0.499, passed: false },
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Test", exact: true }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenLastCalledWith("/adhoc/", {
        dag,
        output: "answer",
        pass_threshold: 0.5,
        values: { expected: "reference" },
      }),
    );
  });

  it("maps child keys and tool calls into dataset runs", async () => {
    const withTool = {
      ...dag,
      nodes: [
        ...dag.nodes,
        {
          id: "t",
          kind: "check",
          check: { type: "tool_called", on: "calls", value: "lookup" },
        },
      ],
    };
    axios.post.mockResolvedValueOnce({
      data: { result: { required_keys: ["output", "expected", "calls"] } },
    });
    axios.get.mockResolvedValue({
      data: {
        result: {
          column_config: [
            { id: "reply-col", name: "reply" },
            { id: "expected-col", name: "reference" },
            { id: "calls-col", name: "calls" },
          ],
        },
      },
    });
    mount(<DagRunPanel dag={withTool} templateId={child} />);
    await screen.findByRole("combobox", {
      name: (name) => name.startsWith("{{expected}}"),
    });
    await pick("Dataset", "Replies");
    await pick("{{output}}", "reply");
    await pick("{{expected}}", "reference");
    expect(
      screen.getByRole("button", { name: "Run on dataset" }),
    ).toBeDisabled();
    await pick("{{calls}}", "calls");
    axios.post.mockResolvedValue({ data: { result: { n: 1 } } });
    fireEvent.click(screen.getByRole("button", { name: "Run on dataset" }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenLastCalledWith(
        `/model-hub/eval-templates/${child}/dag/runs/`,
        {
          dataset_id: "ds",
          mapping: {
            output: "reply-col",
            expected: "expected-col",
            calls: "calls-col",
          },
        },
      ),
    );
  });

  it("keeps execution disabled when the child cannot be resolved", async () => {
    axios.post.mockRejectedValue({
      response: { data: { errors: ["not an eval in this workspace"] } },
    });
    mount(<DagTestPanel dag={dag} />);
    expect(
      await screen.findByText(/not an eval in this workspace/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Test", exact: true }),
    ).toBeDisabled();
  });

  it("declares the default and explicit tool-call inputs and authors the mapping", async () => {
    expect(
      variablesOf({
        nodes: [{ kind: "check", check: { type: "tool_called" } }],
      }),
    ).toEqual(["tool_calls"]);
    expect(
      variablesOf({
        nodes: [{ kind: "check", check: { type: "tool_called", on: "calls" } }],
      }),
    ).toEqual(["calls"]);
    const onCheck = vi.fn();
    mount(
      <DagCheckFields
        node={{ check: { type: "contains", on: "output" } }}
        onCheck={onCheck}
      />,
    );
    await pick("Check", "Tool called");
    expect(onCheck).toHaveBeenLastCalledWith({
      type: "tool_called",
      on: "tool_calls",
    });
  });

  it("supplies typed tool-call JSON to the real test request", async () => {
    const toolTree = {
      ...dag,
      nodes: [
        {
          ...dag.nodes[0],
          kind: "check",
          check: { type: "tool_called", value: "lookup" },
        },
        dag.nodes[1],
      ],
    };
    mount(<DagTestPanel dag={toolTree} threshold={0.5} />);
    fireEvent.click(screen.getByRole("button", { name: "Type values" }));
    fireEvent.change(screen.getByLabelText("Sample output"), {
      target: { value: "answer" },
    });
    fireEvent.change(screen.getByLabelText("{{tool_calls}}"), {
      target: { value: '[{"name":"lookup"}]' },
    });
    axios.post.mockResolvedValue({
      data: { result: { path: [], reason: "Scored" } },
    });
    fireEvent.click(screen.getByRole("button", { name: "Test", exact: true }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenLastCalledWith("/adhoc/", {
        dag: toolTree,
        output: "answer",
        pass_threshold: 0.5,
        values: { tool_calls: '[{"name":"lookup"}]' },
      }),
    );
  });
});
