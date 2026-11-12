// DSX launch L5: the dataset drawer's gates on the real backend.
// get_evals_list now returns is_experiment_default, the version that runs,
// the newest version and the pass rule. "Use in new experiments" shows
// wherever the backend reports the field and writes the whole set through
// edit_dataset_behavior; the Scoring editor (direction + threshold) saves
// keys the real backend drops, so it shows in prototype builds only.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";

const proto = vi.hoisted(() => ({ on: false }));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => proto.on,
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ dataset: "ds-1" }),
}));
const http = vi.hoisted(() => ({ put: vi.fn(), get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    default: { ...real.default, put: http.put, get: http.get, post: http.post },
  };
});

import SavedEvalsList from "../SavedEvalsList";
import { endpoints } from "src/utils/axios";

// One get_evals_list item as the real backend answers it after L5.
const fromBackend = (id, name, extra = {}) => ({
  id,
  name,
  eval_type: "llm",
  template_name: name,
  template_id: `tpl-${id}`,
  output_type: "percentage",
  mapping: { output: "model_output" },
  eval_required_keys: ["output"],
  is_experiment_default: false,
  version: "v1",
  version_number: 1,
  pinned_version_number: null,
  latest_version: "v1",
  latest_version_number: 1,
  pass_threshold: 0.7,
  higher_is_better: true,
  ...extra,
});

function Session({ evals }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return (
    <QueryClientProvider client={client}>
      <SavedEvalsList evals={evals} allColumns={[]} />
    </QueryClientProvider>
  );
}
Session.propTypes = { evals: PropTypes.array.isRequired };

const scoringControls = () => [
  ...screen.queryAllByRole("button", { name: "↑ Higher is better" }),
  ...screen.queryAllByLabelText("Pass threshold"),
];

afterEach(() => setLaunchOverrideForTest(null));

beforeEach(() => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: ["dataset.experimentDefaults", "dataset.evalVersionNotice"],
  });
  proto.on = false;
  http.put.mockReset().mockResolvedValue({ data: { status: true } });
  http.post.mockReset().mockResolvedValue({ data: { status: true } });
  http.get.mockReset().mockResolvedValue({
    data: { result: { versions: [{ version_number: 1 }] } },
  });
});

describe("L5 saved evals on the real backend", () => {
  it("shows Use in new experiments and writes the whole set", async () => {
    const user = userEvent.setup();
    const a = fromBackend("e1", "toxicity", { is_experiment_default: true });
    const b = fromBackend("e2", "groundedness");
    render(<Session evals={[a, b]} />);

    const switchB = screen.getByLabelText(
      "Use groundedness in new experiments",
    );
    expect(
      screen.getByLabelText("Use toxicity in new experiments"),
    ).toBeChecked();
    expect(switchB).not.toBeChecked();

    await user.click(switchB);

    await waitFor(() => expect(http.put).toHaveBeenCalledTimes(1));
    expect(http.put).toHaveBeenCalledWith(
      endpoints.develop.updateDataset("ds-1"),
      { experiment_default_eval_ids: ["e1", "e2"] },
    );
  });

  it("never shows the Scoring editor outside the prototype", async () => {
    const user = userEvent.setup();
    render(<Session evals={[fromBackend("e1", "toxicity")]} />);

    await user.click(screen.getAllByText("toxicity")[0]);

    // The row opened (its detail reads Variable Mapping), with no editor.
    expect(screen.getByText("Variable Mapping")).toBeInTheDocument();
    expect(scoringControls()).toEqual([]);
  });

  it("reads the pass rule and versions the backend reports", async () => {
    render(
      <Session
        evals={[
          fromBackend("e1", "toxicity", {
            version: "v1",
            pinned_version_number: 1,
            pinned_version_id: "ver-1",
            latest_version: "v2",
            latest_version_number: 2,
          }),
        ]}
      />,
    );

    expect(
      screen.getByText("↑ Higher is better · Pass ≥ 0.70 · v1 · pinned"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("saved-eval-newer-version")).toHaveTextContent(
      "v1 → v2 available",
    );
  });

  it("an older backend without the field shows neither control", () => {
    const { is_experiment_default: _drop, ...old } = fromBackend("e1", "tox");
    render(<Session evals={[old]} />);

    expect(
      screen.queryByLabelText("Use tox in new experiments"),
    ).not.toBeInTheDocument();
    expect(scoringControls()).toEqual([]);
  });

  it("the prototype keeps both", async () => {
    setLaunchOverrideForTest({ launch: false });
    proto.on = true;
    const user = userEvent.setup();
    render(<Session evals={[fromBackend("e1", "toxicity")]} />);

    expect(
      screen.getByLabelText("Use toxicity in new experiments"),
    ).toBeInTheDocument();
    await user.click(screen.getAllByText("toxicity")[0]);
    expect(scoringControls().length).toBeGreaterThan(0);
  });
});
