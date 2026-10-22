import PropTypes from "prop-types";
import React, { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";

import MetricInputs from "../MetricInputs";
import {
  evaluatorBinding,
  evaluatorDrift,
  initialForm,
  metricRequestBody,
  weightedInputProblems,
} from "../metricForm";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    derivedMetrics: {
      evaluators: "/tracer/derived-metrics/evaluators/",
    },
  },
}));

const PROJECT = "11111111-1111-4111-8111-111111111111";
const RESOLVED = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  name: "Resolution evaluator",
  config_hash: "1".repeat(64),
};
const ESCALATED = {
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  name: "Escalation evaluator",
  config_hash: "2".repeat(64),
};

const listing = (results, truncated = false) => ({
  data: { results, truncated },
});

const evalForm = () => {
  const form = initialForm();
  form.project_id = PROJECT;
  form.weighting = "inverse_probability";
  form.inputs = form.inputs.map((input) => ({
    ...input,
    source: "eval",
    config_id: "",
    config_hash: "",
  }));
  return form;
};

// Holds the whole form so the request body reflects exactly what a click wrote.
function Harness({ initial = evalForm(), disabled = false, onForm }) {
  const [form, setForm] = useState(initial);
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  onForm?.(form);
  return (
    <QueryClientProvider client={client}>
      <MetricInputs
        inputs={form.inputs}
        projectId={form.project_id}
        disabled={disabled}
        onChange={(inputs) => setForm((current) => ({ ...current, inputs }))}
      />
    </QueryClientProvider>
  );
}

Harness.propTypes = {
  initial: PropTypes.object,
  disabled: PropTypes.bool,
  onForm: PropTypes.func,
};

const choose = async (label, name) => {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(
    await screen.findByRole("option", { name: new RegExp(name) }),
  );
};

describe("evaluator picker", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.get.mockResolvedValue(listing([RESOLVED, ESCALATED]));
  });

  it("loads the project's evaluators and fills BOTH id and persisted hash", async () => {
    let latest;
    render(<Harness onForm={(form) => (latest = form)} />);
    await choose("Input 1 evaluator", "Resolution evaluator");
    await choose("Input 2 evaluator", "Escalation evaluator");

    expect(mocks.get).toHaveBeenCalledWith(
      "/tracer/derived-metrics/evaluators/",
      expect.objectContaining({
        params: { project_id: PROJECT, search: "", limit: 50 },
      }),
    );
    const body = metricRequestBody(latest, null);
    expect(body.inputs.map((input) => input.config_id)).toEqual([
      RESOLVED.id,
      ESCALATED.id,
    ]);
    expect(body.inputs.map((input) => input.config_hash)).toEqual([
      RESOLVED.config_hash,
      ESCALATED.config_hash,
    ]);
    // A weighted metric authored through the picker has nothing left to warn about.
    expect(weightedInputProblems(latest)).toEqual([]);
  });

  it("shows each evaluator's definition prefix so same-named evaluators differ", async () => {
    mocks.get.mockResolvedValue(
      listing([
        RESOLVED,
        { ...RESOLVED, id: ESCALATED.id, config_hash: "3".repeat(64) },
      ]),
    );
    render(<Harness />);
    fireEvent.mouseDown(screen.getByLabelText("Input 1 evaluator"));
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Resolution evaluatordefinition 11111111",
      "Resolution evaluatordefinition 33333333",
    ]);
  });

  it("searches on the server instead of filtering the first page client-side", async () => {
    render(<Harness />);
    const input = screen.getByLabelText("Input 1 evaluator");
    await userEvent.type(input, "escal");
    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith(
        "/tracer/derived-metrics/evaluators/",
        expect.objectContaining({
          params: { project_id: PROJECT, search: "escal", limit: 50 },
        }),
      ),
    );
  });

  it("clearing the selection clears both bound values", async () => {
    let latest;
    const bound = evalForm();
    bound.inputs[0] = { ...bound.inputs[0], ...evaluatorBinding(RESOLVED) };
    render(<Harness initial={bound} onForm={(form) => (latest = form)} />);
    expect(
      await screen.findByDisplayValue("Resolution evaluator"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getAllByLabelText("Clear")[0]);
    await waitFor(() => expect(latest.inputs[0].config_id).toBe(""));
    expect(latest.inputs[0].config_hash).toBe("");
    expect(weightedInputProblems(latest)).toHaveLength(2);
  });

  it("is disabled until a project is chosen and never queries without one", () => {
    const form = evalForm();
    form.project_id = "";
    render(<Harness initial={form} />);
    expect(screen.getByLabelText("Input 1 evaluator")).toBeDisabled();
    expect(screen.getAllByText("Select a project first.")).toHaveLength(2);
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("is read-only for a viewer", async () => {
    render(<Harness disabled />);
    expect(screen.getByLabelText("Input 1 evaluator")).toBeDisabled();
    expect(screen.getByLabelText("Input 1 evaluator ID")).toBeDisabled();
  });

  it("says when the list is truncated and keeps working when it cannot load", async () => {
    mocks.get.mockResolvedValue(listing([RESOLVED], true));
    const { unmount } = render(<Harness />);
    expect(
      (await screen.findAllByText(/Showing the first 50 matches/)).length,
    ).toBeGreaterThan(0);
    unmount();

    mocks.get.mockRejectedValue(new Error("network"));
    let latest;
    render(<Harness onForm={(form) => (latest = form)} />);
    expect(
      (await screen.findAllByText(/Evaluators could not be loaded/)).length,
    ).toBeGreaterThan(0);
    // The advanced fields are the fallback, and they still write the contract.
    fireEvent.click(
      screen.getByRole("button", {
        name: /Advanced: show evaluator ID and hash for input 1/,
      }),
    );
    fireEvent.change(screen.getByLabelText("Input 1 evaluator ID"), {
      target: { value: RESOLVED.id },
    });
    fireEvent.change(screen.getByLabelText("Input 1 evaluator hash"), {
      target: { value: RESOLVED.config_hash },
    });
    expect(metricRequestBody(latest, null).inputs[0]).toMatchObject({
      config_id: RESOLVED.id,
      config_hash: RESOLVED.config_hash,
    });
  });

  it("opens the advanced fields for a legacy input bound without a hash", () => {
    const legacy = evalForm();
    legacy.weighting = "none";
    legacy.inputs[0] = { ...legacy.inputs[0], config_id: RESOLVED.id };
    render(<Harness initial={legacy} />);
    expect(
      screen.getByRole("button", {
        name: /Advanced: hide evaluator ID and hash for input 1/,
      }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Input 1 evaluator ID")).toHaveValue(
      RESOLVED.id,
    );
    expect(screen.getByLabelText("Input 1 evaluator hash")).toHaveValue("");
  });

  it("keeps a bound evaluator selected when the listing does not include it", async () => {
    mocks.get.mockResolvedValue(listing([ESCALATED]));
    let latest;
    const bound = evalForm();
    bound.inputs[0] = { ...bound.inputs[0], ...evaluatorBinding(RESOLVED) };
    render(<Harness initial={bound} onForm={(form) => (latest = form)} />);
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    // Without a listed name the control shows the bound id, never a blank value,
    // and the binding itself is untouched.
    expect(screen.getByLabelText("Input 1 evaluator")).toHaveValue(RESOLVED.id);
    fireEvent.mouseDown(screen.getByLabelText("Input 1 evaluator"));
    await waitFor(() =>
      expect(
        screen.getAllByRole("option").map((option) => option.textContent),
      ).toEqual([
        `${RESOLVED.id}definition 11111111`,
        "Escalation evaluatordefinition 22222222",
      ]),
    );
    expect(latest.inputs[0]).toMatchObject(evaluatorBinding(RESOLVED));
    // No drift warning is possible without the listed definition to compare.
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("labels a legacy binding with no hash instead of an empty definition", async () => {
    const legacy = evalForm();
    legacy.weighting = "none";
    legacy.inputs[0] = { ...legacy.inputs[0], config_id: ESCALATED.id };
    mocks.get.mockResolvedValue(listing([RESOLVED]));
    render(<Harness initial={legacy} />);
    fireEvent.mouseDown(screen.getByLabelText("Input 1 evaluator"));
    const options = await screen.findAllByRole("option");
    expect(options[0]).toHaveTextContent(
      `${ESCALATED.id}legacy unversioned results`,
    );
  });

  it("warns when a bound evaluator's definition has moved on", async () => {
    const stale = evalForm();
    stale.inputs[0] = {
      ...stale.inputs[0],
      ...evaluatorBinding({ ...RESOLVED, config_hash: "9".repeat(64) }),
    };
    render(<Harness initial={stale} />);
    expect(await screen.findByRole("note")).toHaveTextContent(
      /definition changed after the input was bound/,
    );
    expect(screen.getAllByRole("note")).toHaveLength(1);
  });
});

describe("weighted input advisory", () => {
  it("only applies to inverse-probability metrics", () => {
    const form = evalForm();
    form.weighting = "none";
    expect(weightedInputProblems(form)).toEqual([]);
  });

  it("names each evaluator input that has no hash, because that reads only legacy results", () => {
    const form = evalForm();
    form.inputs[0] = { ...form.inputs[0], ...evaluatorBinding(RESOLVED) };
    const problems = weightedInputProblems(form);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('"escalated"');
    expect(problems[0]).toContain("legacy unversioned results");
  });

  it("requires an evaluation input at all for weighting", () => {
    const form = initialForm();
    form.weighting = "inverse_probability";
    expect(weightedInputProblems(form)).toEqual([
      "Inverse-probability weighting needs at least one evaluation input.",
    ]);
  });

  it("detects drift only when a hash is bound and differs", () => {
    const bound = { config_id: RESOLVED.id, config_hash: RESOLVED.config_hash };
    expect(evaluatorDrift(bound, RESOLVED)).toBe(false);
    expect(
      evaluatorDrift(bound, { ...RESOLVED, config_hash: "0".repeat(64) }),
    ).toBe(true);
    expect(evaluatorDrift({ ...bound, config_hash: "" }, RESOLVED)).toBe(false);
    expect(evaluatorDrift(bound, undefined)).toBe(false);
  });
});
