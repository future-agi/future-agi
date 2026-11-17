/* eslint-disable react/prop-types -- picker stand-ins render only their label */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import { createCachePrimaryFilter } from "../../common";
import LeftControl from "../LeftControl";

// The graph's property picker. A score eval draws its average over time, and
// the graph read takes no number to compare scores with, so picking one
// offers no operator or value box. An eval with classes still offers its
// class picker.

const h = vi.hoisted(() => ({ evals: [] }));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ observeId: "project-1" }),
}));
vi.mock("src/api/project/llm-tracing", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetTraceEvals: () => ({ data: h.evals }),
}));
vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ user: { id: "user-1" } }),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(() => Promise.resolve({ data: {} })) },
}));
// Each picker shows only its label, so the test reads which ones are offered.
vi.mock("src/components/FromSearchSelectField", async (importOriginal) => ({
  ...(await importOriginal()),
  FormSearchSelectFieldControl: ({ label }) => <div>{`Picker: ${label}`}</div>,
  FormSearchSelectFieldState: ({ label }) => <div>{`Picker: ${label}`}</div>,
}));

const scoreEval = {
  id: "eval-score",
  name: "Helpfulness",
  output_type: "score",
};
const classEval = {
  id: "eval-tone",
  name: "Tone",
  output_type: "choices",
  choices: ["Calm", "Curt"],
};

// The picker opens on the property this person last chose for the project.
const openPickerOn = (evalItem) => {
  h.evals = [evalItem];
  createCachePrimaryFilter("user-1", "project-1", `eval_${evalItem.id}`);
  const onGraphConfigChange = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <LeftControl
        onGraphConfigChange={onGraphConfigChange}
        selectedGraphEvals={[]}
        selectedGraphProperty=""
        setSelectedGraphEvals={vi.fn()}
        setSelectedGraphProperty={vi.fn()}
        setSelectedGraphAttributes={vi.fn()}
      />
    </QueryClientProvider>,
  );
  return { onGraphConfigChange };
};

const scoreGraphRequest = {
  id: "eval-score",
  type: "EVAL",
  output_type: "float",
};

describe("Graph property picker", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("still asks for a score eval's graph when it is picked", async () => {
    const { onGraphConfigChange } = openPickerOn(scoreEval);

    await waitFor(() =>
      expect(onGraphConfigChange).toHaveBeenLastCalledWith(scoreGraphRequest),
    );
  });

  it("offers no operator or value box for a score eval", async () => {
    const { onGraphConfigChange } = openPickerOn(scoreEval);

    await waitFor(() =>
      expect(onGraphConfigChange).toHaveBeenLastCalledWith(scoreGraphRequest),
    );
    expect(screen.getByText("Picker: Property")).toBeInTheDocument();
    expect(screen.queryByText("Picker: Operator")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Value")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("still offers the class picker for an eval with classes", async () => {
    openPickerOn(classEval);

    expect(await screen.findByText("Picker: Class")).toBeInTheDocument();
    expect(screen.queryByText("Picker: Operator")).not.toBeInTheDocument();
  });
});
