// The Scoring editor on a dataset's saved evaluations. Its Save sends the
// evaluation as stored (run settings, mapping, notes, reason column), the
// version it runs and the new pass threshold, and the success line shows
// only when the server reports that threshold back. Direction is not built,
// so its control is disabled with the shared line. Both states of the
// show-unreleased switch are pinned the way launch-gates.test.jsx pins them.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";

// The switch as the product modules read it, and in the override that
// launchSwitch itself reads.
const unreleased = vi.hoisted(() => ({ shown: true }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ dataset: "ds-1" }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "owner" }),
}));
const toast = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: toast.enqueueSnackbar,
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

// A score evaluation saved the way production saves one: the evaluation
// editor's request (bindingSavePayload.js buildBindingSavePayload) stored
// by add_user_eval with the reason column on. Its threshold lives in the
// run settings. The definition still holds the template's own threshold.
const storedConfig = (extra = {}) => ({
  mapping: { output: "col-output" },
  config: {
    rule_prompt: "Judge {{output}}",
    required_keys: ["output"],
    output: "score",
    pass_threshold: 0.5,
    version_pin: "v1",
  },
  run_config: {
    model: "gpt-4o-mini",
    agent_mode: "agent",
    check_internet: false,
    summary: { type: "concise" },
    pass_threshold: 0.7,
    multi_choice: false,
    error_localizer_enabled: false,
    data_injection: { variables_only: true },
  },
  description: "Flags answers that miss the question",
  reason_column: true,
  ...extra,
});

// The get_evals_list row for it: `config` is the stored config itself,
// `run_config` the list's display view (summary collapsed to its type,
// defaults filled in), and pass_threshold the one a run applies.
const listRow = ({ config = storedConfig(), ...extra } = {}) => ({
  id: "e1",
  name: "relevance",
  eval_type: "llm",
  template_name: "relevance",
  template_id: "tpl-1",
  owner: "user",
  output_type: "percentage",
  eval_required_keys: ["output"],
  config,
  mapping: config.mapping ?? {},
  params: config.params ?? {},
  model: "gpt-4o-mini",
  error_localizer: false,
  run_config: {
    agent_mode: "agent",
    check_internet: false,
    summary: "concise",
    pass_threshold: 0.7,
    error_localizer_enabled: false,
    data_injection: { variables_only: true },
    knowledge_bases: [],
    tools: {},
    model: "gpt-4o-mini",
    multi_choice: false,
  },
  pinned_version_id: "ver-1",
  is_experiment_default: false,
  version: "v1",
  version_number: 1,
  pinned_version_number: 1,
  latest_version: "v2",
  latest_version_number: 2,
  pass_threshold: 0.7,
  higher_is_better: true,
  ...extra,
});

const LIST_URL = endpoints.develop.eval.getEvalsList("ds-1");
const EDIT_URL = endpoints.develop.eval.editEval("ds-1", "e1");

// What the server answers when the list is read again after a Save.
const server = { rows: [] };

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

const openRow = async (user) => {
  await user.click(screen.getAllByText("relevance")[0]);
  expect(screen.getByText("Variable Mapping")).toBeInTheDocument();
};

const typeThreshold = async (user, value) => {
  const input = screen.getByLabelText("Pass threshold");
  await user.clear(input);
  if (value !== "") await user.type(input, value);
};

// The body as it goes on the wire (undefined keys dropped).
const sentBody = () => JSON.parse(JSON.stringify(http.post.mock.calls[0][1]));

const toastsOf = (variant) =>
  toast.enqueueSnackbar.mock.calls.filter(([, o]) => o?.variant === variant);

const LAUNCH = {
  launch: true,
  showUnreleased: false,
  experimentsBasic: false,
  madeReal: [],
};
// As shipped: the override leaves the switch to the build, which shows it.
const SHIPPED = { launch: true, experimentsBasic: false, madeReal: [] };

beforeEach(() => {
  toast.enqueueSnackbar.mockReset();
  http.put.mockReset().mockResolvedValue({ data: { status: true } });
  http.post.mockReset().mockResolvedValue({
    data: {
      status: true,
      result: "Column evaluation updated and queued for processing",
    },
  });
  server.rows = [];
  http.get.mockReset().mockImplementation((url) =>
    Promise.resolve(
      url === LIST_URL
        ? { data: { status: true, result: { evals: server.rows } } }
        : {
            data: {
              result: {
                versions: [{ version_number: 1 }, { version_number: 2 }],
              },
            },
          },
    ),
  );
});

afterEach(() => {
  setLaunchOverrideForTest(null);
  unreleased.shown = true;
});

describe("Scoring editor with the show-unreleased switch off", () => {
  beforeEach(() => {
    unreleased.shown = false;
    setLaunchOverrideForTest(LAUNCH);
  });

  it("is not shown and sends nothing", async () => {
    const user = userEvent.setup();
    render(<Session evals={[listRow()]} />);
    await openRow(user);

    expect(screen.queryByLabelText("Pass threshold")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "↓ Lower is better" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(http.post).not.toHaveBeenCalled();
  });

  it("where the build claims the surface, direction is disabled with the shared line", async () => {
    setLaunchOverrideForTest({ ...LAUNCH, madeReal: ["dataset.evalScoring"] });
    const user = userEvent.setup();
    render(<Session evals={[listRow()]} />);
    await openRow(user);

    const lower = screen.getByRole("button", { name: "↓ Lower is better" });
    expect(lower).toBeDisabled();
    expect(lower).toHaveAccessibleDescription(NOT_IN_RELEASE);
    expect(screen.getByLabelText("Pass threshold")).toBeEnabled();
  });
});

describe("Scoring editor as shipped (unreleased parts shown)", () => {
  beforeEach(() => {
    unreleased.shown = true;
    setLaunchOverrideForTest(SHIPPED);
  });

  it("shows the direction runs apply, disabled with the shared line", async () => {
    const user = userEvent.setup();
    render(<Session evals={[listRow()]} />);
    await openRow(user);

    const higher = screen.getByRole("button", { name: "↑ Higher is better" });
    const lower = screen.getByRole("button", { name: "↓ Lower is better" });
    expect(higher).toBeDisabled();
    expect(lower).toBeDisabled();
    expect(higher).toHaveAttribute("aria-pressed", "true");
    expect(lower).toHaveAttribute("aria-pressed", "false");
    expect(higher).toHaveAccessibleDescription(NOT_IN_RELEASE);
    expect(lower).toHaveAccessibleDescription(NOT_IN_RELEASE);
    expect(screen.getAllByText(NOT_IN_RELEASE).length).toBeGreaterThan(0);
    // The threshold stays editable and shows the one runs apply.
    expect(screen.getByLabelText("Pass threshold")).toHaveValue("0.7");
    expect(screen.getByLabelText("Pass threshold")).toBeEnabled();
  });

  it("Save sends the stored evaluation, its version and the new threshold, then confirms it", async () => {
    const user = userEvent.setup();
    server.rows = [listRow({ pass_threshold: 0.8 })];
    render(<Session evals={[listRow()]} />);
    await openRow(user);

    await typeThreshold(user, "0.8");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));
    expect(http.post.mock.calls[0][0]).toBe(EDIT_URL);
    // The stored config exactly (not the list's display view), the pinned
    // version, the threshold in its own field; no direction, model or name.
    expect(sentBody()).toEqual({
      config: storedConfig(),
      pinned_version_id: "ver-1",
      error_localizer: false,
      save_as_template: false,
      run: false,
      pass_threshold: 0.8,
    });
    await waitFor(() =>
      expect(toast.enqueueSnackbar).toHaveBeenCalledWith(
        "relevance: pass threshold saved (Pass ≥ 0.80)",
        { variant: "success" },
      ),
    );
    expect(http.get).toHaveBeenCalledWith(LIST_URL, {
      params: { eval_type: "user" },
    });
    expect(toastsOf("error")).toEqual([]);
  });

  it("an evaluation that follows the default version sends a null version", async () => {
    const user = userEvent.setup();
    const row = listRow({
      pinned_version_id: null,
      pinned_version_number: null,
    });
    server.rows = [{ ...row, pass_threshold: 0.8 }];
    render(<Session evals={[row]} />);
    await openRow(user);

    await typeThreshold(user, "0.8");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));
    // A missing key would ask the server to save a new version; null keeps
    // the evaluation on the default one.
    expect(sentBody()).toHaveProperty("pinned_version_id", null);
    await waitFor(() => expect(toastsOf("success")).toHaveLength(1));
  });

  it("a stored evaluation with no reason-column setting keeps it off", async () => {
    const user = userEvent.setup();
    const { reason_column: _off, ...noReason } = storedConfig();
    server.rows = [listRow({ config: noReason, pass_threshold: 0.8 })];
    render(<Session evals={[listRow({ config: noReason })]} />);
    await openRow(user);

    await typeThreshold(user, "0.8");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));
    expect(sentBody().config).toEqual({ ...noReason, reason_column: false });
  });

  it("shows no success line when the server did not keep the threshold", async () => {
    const user = userEvent.setup();
    // An older server answers 200 and drops the field.
    server.rows = [listRow({ pass_threshold: 0.7 })];
    render(<Session evals={[listRow()]} />);
    await openRow(user);

    await typeThreshold(user, "0.8");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(toast.enqueueSnackbar).toHaveBeenCalledWith(
        "relevance: the pass threshold wasn't saved. Runs still use Pass ≥ 0.70.",
        { variant: "error" },
      ),
    );
    expect(toastsOf("success")).toEqual([]);
  });

  it("shows no success line when the version it runs changed", async () => {
    const user = userEvent.setup();
    server.rows = [
      listRow({ pass_threshold: 0.8, pinned_version_id: "ver-9" }),
    ];
    render(<Session evals={[listRow()]} />);
    await openRow(user);

    await typeThreshold(user, "0.8");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(toastsOf("warning")).toHaveLength(1));
    expect(toastsOf("success")).toEqual([]);
  });

  it("a refused Save says the pass threshold wasn't saved and shows no success line", async () => {
    const user = userEvent.setup();
    http.post.mockReset().mockRejectedValue({});
    render(<Session evals={[listRow()]} />);
    await openRow(user);

    await typeThreshold(user, "0.8");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(toast.enqueueSnackbar).toHaveBeenCalledWith(
        "Couldn't save the pass threshold",
        { variant: "error" },
      ),
    );
    expect(toastsOf("success")).toEqual([]);
    expect(http.get).not.toHaveBeenCalledWith(LIST_URL, expect.anything());
  });

  it("a refused Save shows the server's reason", async () => {
    const user = userEvent.setup();
    http.post.mockReset().mockRejectedValue({
      result: "Missing required mapping keys: output",
    });
    const row = listRow({
      pinned_version_id: null,
      pinned_version_number: null,
    });
    render(<Session evals={[row]} />);
    await openRow(user);

    await typeThreshold(user, "0.8");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(toast.enqueueSnackbar).toHaveBeenCalledWith(
        "Missing required mapping keys: output",
        { variant: "error" },
      ),
    );
    // The refused request was still the one that keeps the default version.
    expect(sentBody()).toHaveProperty("pinned_version_id", null);
    expect(toastsOf("success")).toEqual([]);
  });

  it("Save stays off for an empty, out-of-range or unchanged threshold", async () => {
    const user = userEvent.setup();
    render(<Session evals={[listRow()]} />);
    await openRow(user);
    const save = () => screen.getByRole("button", { name: "Save" });

    expect(save()).toBeDisabled();
    await typeThreshold(user, "");
    expect(save()).toBeDisabled();
    await typeThreshold(user, "1.5");
    expect(save()).toBeDisabled();
    await typeThreshold(user, "0.70");
    expect(save()).toBeDisabled();
    await typeThreshold(user, "0.75");
    expect(save()).toBeEnabled();
    expect(http.post).not.toHaveBeenCalled();
  });
});
