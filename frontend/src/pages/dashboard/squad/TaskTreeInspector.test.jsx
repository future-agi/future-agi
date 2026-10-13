import { describe, expect, it } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import TaskTreeInspector from "./TaskTreeInspector";

const base = {
  available: true,
  schemaVersion: "omega-run-task-tree/v1",
  runId: "run-1",
  integrity: "verified",
  trees: [
    {
      treeId: "forest-a",
      rootTaskId: "root",
      nodes: [
        {
          id: "root",
          title: "Research request",
          status: "done",
          agentId: "agent-root",
          lineage: { kind: "root", depth: 0 },
          receipt: { hash: "hash-root" },
        },
        {
          id: "child",
          title: "Gather sources",
          status: "working",
          agentId: "agent-child",
          lineage: { kind: "task", depth: 1 },
          dependencies: ["root"],
        },
        {
          id: "grandchild",
          title: "Check sources",
          status: "failed",
          agentId: "agent-grandchild",
          lineage: { kind: "task", depth: 2 },
          dependencies: ["child"],
        },
      ],
    },
  ],
};

describe("TaskTreeInspector", () => {
  it("filters canonical server nodes and focuses lineage without mutations", async () => {
    render(<TaskTreeInspector treeResult={base} runId="run-1" />);
    expect(screen.getByText("Research request")).toBeInTheDocument();
    expect(screen.getByText("Gather sources")).toBeInTheDocument();
    expect(screen.getByText("Check sources")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /Gather sources/ }),
    );
    expect(screen.getByText("Focused task")).toBeInTheDocument();
    expect(
      screen.getByText(/working · depth 1 · subtask · agent agent-child/),
    ).toBeInTheDocument();
    expect(screen.getByText("Dependencies: root")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Status"));
    await userEvent.click(screen.getByRole("option", { name: "failed" }));
    expect(screen.queryByText("Gather sources")).not.toBeInTheDocument();
    expect(screen.getByText("Check sources")).toBeInTheDocument();
  });

  it("supports depth filtering and multiple forests", async () => {
    const result = {
      ...base,
      integrity: "degraded",
      trees: [
        ...base.trees,
        {
          treeId: "forest-b",
          nodes: [
            {
              id: "other-root",
              title: "Other lane",
              status: "queued",
              lineage: { kind: "root", depth: 0 },
            },
          ],
        },
      ],
      unresolvedTaskIds: ["withheld-id"],
      quarantinedTaskCount: 1,
    };
    render(<TaskTreeInspector treeResult={result} runId="run-1" />);
    expect(
      screen.getByText(/server marked this forest incomplete/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/identifiers are intentionally not rendered/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Maximum depth"));
    await userEvent.click(screen.getByRole("option", { name: "Root only" }));
    expect(screen.getByText("Research request")).toBeInTheDocument();
    expect(screen.queryByText("Check sources")).not.toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Forest"));
    await userEvent.click(screen.getByRole("option", { name: "forest-b" }));
    expect(screen.getByText("Other lane")).toBeInTheDocument();
    expect(screen.queryByText("Research request")).not.toBeInTheDocument();
  });

  it("suppresses a foreign or mismatched run and keeps operator routes out of the UI", () => {
    render(
      <TaskTreeInspector
        treeResult={{ ...base, runId: "other-run" }}
        runId="run-1"
      />,
    );
    expect(screen.getByText(/different run/)).toBeInTheDocument();
    expect(screen.queryByText("Research request")).not.toBeInTheDocument();
    expect(screen.queryByText(/Society/)).not.toBeInTheDocument();
  });

  it("shows unavailable responses without fabricating an empty tree", () => {
    render(
      <TaskTreeInspector
        treeResult={{
          available: false,
          reason: "Task tree service unavailable.",
        }}
        runId="run-1"
      />,
    );
    expect(
      screen.getByText("Task tree service unavailable."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/No server-reported task trees/),
    ).not.toBeInTheDocument();
  });
});
