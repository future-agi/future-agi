import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ui,
  mountDrawer,
  assertNoSuccessEffects,
} from "./createAdmissionDrawer.fixture";
import { recoveryDraftKey } from "../createAdmissionDraft";

const api = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    develop: { experiment: { create: () => "/model-hub/experiments/v2/" } },
  },
}));
beforeEach(() => {
  vi.clearAllMocks();
  ui.nameAvailable = true;
  ui.realNameCheck = false;
  ui.blockAtValidation = false;
  ui.emptyDefaults = false;
  ui.columns = [];
  ui.datasetEvals = [];
  ui.runConfigs = undefined;
  ui.sourceExperiment = null;
  ui.evalChoice = [];
  ui.sampleRows = [];
  ui.sampleTotal = 1;
  ui.store.prefill = undefined;
  ui.suggestion = {};
});
afterEach(cleanup);

const id = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3a";
// Normalized by the production Axios response interceptor.
const admissionError = {
  status: false,
  code: "experiment_admission_unconfirmed",
  detail: `Configuration saved, but execution admission is unconfirmed. Retry the same submission; do not create another experiment. experiment_id=${id}`,
  statusCode: 503,
  transportCode: "ERR_BAD_RESPONSE",
};
const confirmed = {
  data: {
    status: true,
    result: `Experiment created successfully. experiment_id=${id}`,
  },
};
const submit = () =>
  fireEvent.click(screen.getByRole("button", { name: /Run experiment/ }));
// Close through the production Discard dialog. Once the drawer was reopened in
// the same test DOM, its new dialog sits under the drawer's aria-hidden
// siblings, so the newest Discard button is found including hidden ones.
const discardDrawer = async ({ rerender, view }, { reopened = false } = {}) => {
  fireEvent.click(
    screen.getByRole(
      "button",
      /** @type {import("@testing-library/react").ByRoleOptions} */ ({
        name: "Close",
        exact: true,
      }),
    ),
  );
  if (reopened) {
    const buttons = await screen.findAllByRole(
      "button",
      /** @type {import("@testing-library/react").ByRoleOptions} */ ({
        name: "Discard",
        exact: true,
        hidden: true,
      }),
    );
    fireEvent.click(buttons[buttons.length - 1]);
  } else {
    fireEvent.click(
      await screen.findByRole(
        "button",
        /** @type {import("@testing-library/react").ByRoleOptions} */ ({
          name: "Discard",
          exact: true,
        }),
      ),
    );
  }
  await act(async () => rerender(view()));
  expect(ui.store.openRunExperiment).toBe(false);
};

describe("create admission in the production drawer", () => {
  it("shows recovery instructions, retains the draft and has no success effects", async () => {
    api.post.mockRejectedValue(admissionError);
    mountDrawer();
    submit();
    await waitFor(() =>
      expect(ui.snackbar).toHaveBeenCalledWith(admissionError.detail, {
        variant: "error",
      }),
    );
    expect(api.post).toHaveBeenCalledOnce();
    expect(screen.getByTestId("experiment-drawer")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(admissionError.detail);
    assertNoSuccessEffects();
  });

  it("retries an identical saved draft despite a taken-name read, then confirms exactly once", async () => {
    api.post.mockRejectedValueOnce(admissionError).mockResolvedValue(confirmed);
    const { rerender, view } = mountDrawer();
    submit();
    await waitFor(() => expect(ui.snackbar).toHaveBeenCalled());
    ui.nameAvailable = false;
    await act(async () => rerender(view()));
    expect(screen.queryByTestId("wizard-run-blocked")).not.toBeInTheDocument();
    assertNoSuccessEffects();
    submit();
    await waitFor(() => expect(ui.submitted).toHaveBeenCalledOnce());
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1]).toEqual(api.post.mock.calls[0]);
    expect(ui.navigate).toHaveBeenCalledOnce();
    expect(ui.navigate).toHaveBeenCalledWith(
      `/dashboard/develop/experiment/${id}/data`,
    );
    expect(ui.refreshGrid).toHaveBeenCalledOnce();
  });

  it("restores the submitted draft after Discard and reopening with empty defaults", async () => {
    api.post.mockRejectedValueOnce(admissionError).mockResolvedValue(confirmed);
    const { rerender, view, client } = mountDrawer();
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment name" }), {
      target: { value: "Saved before closing" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Prompt name" }), {
      target: { value: "Saved prompt configuration" },
    });
    submit();
    await waitFor(() => expect(ui.snackbar).toHaveBeenCalledOnce());
    const submittedBody = JSON.parse(JSON.stringify(api.post.mock.calls[0][1]));
    assertNoSuccessEffects();

    // A later edit is not the request the server saved.
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment name" }), {
      target: { value: "Unsaved later edit" },
    });
    fireEvent.click(
      screen.getByRole(
        "button",
        /** @type {import("@testing-library/react").ByRoleOptions} */ ({
          name: "Close",
          exact: true,
        }),
      ),
    );
    fireEvent.click(
      await screen.findByRole(
        "button",
        /** @type {import("@testing-library/react").ByRoleOptions} */ ({
          name: "Discard",
          exact: true,
        }),
      ),
    );
    await act(async () => rerender(view()));
    expect(
      screen.queryByRole("textbox", { name: "Experiment name" }),
    ).not.toBeInTheDocument();
    expect(ui.store.openRunExperiment).toBe(false);

    // Reopen the production drawer using the same query cache. No saved body
    // or form values are passed to it; it starts with actual empty defaults.
    ui.emptyDefaults = true;
    ui.nameAvailable = false;
    ui.store.openRunExperiment = true;
    await act(async () => rerender(view()));
    await waitFor(() =>
      expect(
        screen.getByRole("textbox", { name: "Experiment name" }),
      ).toHaveValue("Saved before closing"),
    );
    expect(screen.getByRole("textbox", { name: "Prompt name" })).toHaveValue(
      "Saved prompt configuration",
    );
    expect(screen.queryByTestId("wizard-run-blocked")).not.toBeInTheDocument();
    expect(api.post).toHaveBeenCalledOnce();
    submit();
    await waitFor(() => expect(ui.submitted).toHaveBeenCalledOnce());
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1]).toEqual(submittedBody);
    expect(ui.navigate).toHaveBeenCalledOnce();
    expect(ui.refreshGrid).toHaveBeenCalledOnce();

    await act(async () => rerender(view()));
    ui.nameAvailable = true;
    ui.store.openRunExperiment = true;
    await act(async () => rerender(view()));
    expect(
      screen.getByRole("textbox", { name: "Experiment name" }),
    ).not.toHaveValue("Saved before closing");
    client.clear();
  });

  it.each([false, true])(
    "preserves No reference column after Discard and reopen (columns delayed: %s)",
    async (delayed) => {
      const expected = {
        id: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3f",
        name: "Expected",
        role: "expected",
        origin_type: "others",
      };
      ui.columns = [expected];
      api.post
        .mockRejectedValueOnce(admissionError)
        .mockResolvedValue(confirmed);
      const { rerender, view, client } = mountDrawer();
      const reference = () =>
        screen.getByRole("combobox", { name: "Reference column" });
      await waitFor(() => expect(reference()).toHaveValue(expected.id));
      fireEvent.change(reference(), { target: { value: "" } });
      submit();
      await waitFor(() => expect(ui.snackbar).toHaveBeenCalledOnce());
      const submittedBody = JSON.parse(
        JSON.stringify(api.post.mock.calls[0][1]),
      );
      expect(submittedBody.column_id).toBeNull();
      assertNoSuccessEffects();

      fireEvent.click(
        screen.getByRole(
          "button",
          /** @type {import("@testing-library/react").ByRoleOptions} */ ({
            name: "Close",
            exact: true,
          }),
        ),
      );
      fireEvent.click(
        await screen.findByRole(
          "button",
          /** @type {import("@testing-library/react").ByRoleOptions} */ ({
            name: "Discard",
            exact: true,
          }),
        ),
      );
      await act(async () => rerender(view()));
      expect(ui.store.openRunExperiment).toBe(false);
      ui.emptyDefaults = true;
      ui.nameAvailable = false;
      ui.columns = delayed ? [] : [expected];
      ui.store.openRunExperiment = true;
      await act(async () => rerender(view()));
      await waitFor(() =>
        expect(
          screen.getByRole("textbox", { name: "Experiment name" }),
        ).toHaveValue(submittedBody.name),
      );
      if (delayed) {
        ui.columns = [expected];
        await act(async () => rerender(view()));
      }
      expect(reference()).toHaveValue("");
      submit();
      await waitFor(() => expect(ui.submitted).toHaveBeenCalledOnce());
      expect(api.post).toHaveBeenCalledTimes(2);
      expect(api.post.mock.calls[1][1]).toEqual(submittedBody);
      client.clear();
    },
  );

  it("keeps edited evaluations over a Start from copy after Discard and reopen", async () => {
    const source = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c41";
    const sourceEval = {
      id: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c42",
      template_id: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c43",
      name: "source-score",
      is_experiment_default: false,
      mapping: { output: "output" },
    };
    ui.columns = [
      {
        id: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c40",
        name: "Input",
        role: "input",
        origin_type: "others",
      },
    ];
    ui.datasetEvals = [sourceEval];
    ui.runConfigs = {
      results: [{ experiment_id: source, metric_keys: ["source-score"] }],
      metric_defs: [{ key: "source-score", user_eval_id: sourceEval.id }],
    };
    ui.sourceExperiment = source;
    ui.evalChoice = [
      {
        templateId: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c44",
        name: "edited-score",
        config: { mapping: { output: "output" } },
      },
    ];
    api.post.mockRejectedValueOnce(admissionError).mockResolvedValue(confirmed);
    const { rerender, view, client } = mountDrawer();
    const evaluations = () => screen.getByTestId("evaluations").textContent;
    fireEvent.click(screen.getByRole("button", { name: "Start from source" }));
    await waitFor(() => expect(evaluations()).toBe("source-score"));
    fireEvent.click(
      screen.getByRole("button", { name: "Replace evaluations" }),
    );
    expect(evaluations()).toBe("edited-score");
    submit();
    await waitFor(() => expect(ui.snackbar).toHaveBeenCalledOnce());
    const submittedBody = JSON.parse(JSON.stringify(api.post.mock.calls[0][1]));
    expect(submittedBody.user_eval_metrics).toEqual([
      expect.objectContaining({ name: "edited-score" }),
    ]);
    assertNoSuccessEffects();

    fireEvent.click(
      screen.getByRole(
        "button",
        /** @type {import("@testing-library/react").ByRoleOptions} */ ({
          name: "Close",
          exact: true,
        }),
      ),
    );
    fireEvent.click(
      await screen.findByRole(
        "button",
        /** @type {import("@testing-library/react").ByRoleOptions} */ ({
          name: "Discard",
          exact: true,
        }),
      ),
    );
    await act(async () => rerender(view()));
    expect(ui.store.openRunExperiment).toBe(false);
    ui.emptyDefaults = true;
    ui.nameAvailable = false;
    ui.store.openRunExperiment = true;
    await act(async () => rerender(view()));
    await waitFor(() =>
      expect(
        screen.getByRole("textbox", { name: "Experiment name" }),
      ).toHaveValue(submittedBody.name),
    );
    // The saved request chose these evaluations after the copy; a reopened
    // draft must not copy the source experiment's evaluations over them.
    expect(evaluations()).toBe("edited-score");
    submit();
    await waitFor(() => expect(ui.submitted).toHaveBeenCalledOnce());
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1]).toEqual(submittedBody);
    client.clear();
  });

  it.each(["delayed", "in flight"])(
    "keeps the saved rows when a rows link reopens the drawer (row read %s)",
    async (timing) => {
      const row = { row_id: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c45", order: 0 };
      let finishRead;
      api.get.mockReturnValue(
        new Promise((resolve) => {
          finishRead = resolve;
        }),
      );
      api.post
        .mockRejectedValueOnce(admissionError)
        .mockResolvedValue(confirmed);
      const { rerender, view, client } = mountDrawer();
      const rows = () => screen.getByTestId("rows").textContent;
      expect(rows()).toBe("");
      fireEvent.change(
        screen.getByRole("textbox", { name: "Experiment name" }),
        { target: { value: "All rows saved before closing" } },
      );
      submit();
      await waitFor(() => expect(ui.snackbar).toHaveBeenCalledOnce());
      const submittedBody = JSON.parse(
        JSON.stringify(api.post.mock.calls[0][1]),
      );
      expect(submittedBody.row_ids).toBeUndefined();
      assertNoSuccessEffects();

      fireEvent.click(
        screen.getByRole(
          "button",
          /** @type {import("@testing-library/react").ByRoleOptions} */ ({
            name: "Close",
            exact: true,
          }),
        ),
      );
      fireEvent.click(
        await screen.findByRole(
          "button",
          /** @type {import("@testing-library/react").ByRoleOptions} */ ({
            name: "Discard",
            exact: true,
          }),
        ),
      );
      await act(async () => rerender(view()));
      expect(ui.store.openRunExperiment).toBe(false);
      // A `rows=` link opens the drawer again for the same dataset.
      ui.emptyDefaults = true;
      ui.nameAvailable = false;
      ui.store.prefill = { row_ids: [row.row_id], row_refs: [row.row_id] };
      ui.sampleRows = timing === "delayed" ? null : [];
      ui.store.openRunExperiment = true;
      await act(async () => rerender(view()));
      await waitFor(() =>
        expect(
          screen.getByRole("textbox", { name: "Experiment name" }),
        ).toHaveValue(submittedBody.name),
      );
      // The link's rows resolve only after the saved draft was restored.
      if (timing === "delayed") {
        ui.sampleRows = [row];
        await act(async () => rerender(view()));
      } else {
        await act(async () =>
          finishRead?.({
            data: { result: { table: [row], metadata: { total_rows: 1 } } },
          }),
        );
      }
      expect(rows()).toBe("");
      submit();
      await waitFor(() => expect(ui.submitted).toHaveBeenCalledOnce());
      expect(api.post).toHaveBeenCalledTimes(2);
      expect(api.post.mock.calls[1][1]).toEqual(submittedBody);
      client.clear();
    },
  );

  it.each(["delayed", "in flight"])(
    "restores a draft saved after a rows link reopened the drawer on the next open (row read %s)",
    async (timing) => {
      const row = { row_id: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c45", order: 0 };
      let finishRead;
      api.get.mockReturnValue(
        new Promise((resolve) => {
          finishRead = resolve;
        }),
      );
      let failFirst;
      api.post
        .mockReturnValueOnce(
          new Promise((_, reject) => {
            failFirst = reject;
          }),
        )
        .mockResolvedValue(confirmed);
      const { rerender, view, client } = mountDrawer();
      const rows = () => screen.getByTestId("rows").textContent;
      const name = () =>
        screen.getByRole("textbox", { name: "Experiment name" });
      fireEvent.change(name(), { target: { value: "Pending when closed" } });
      submit();
      await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
      const submittedBody = JSON.parse(
        JSON.stringify(api.post.mock.calls[0][1]),
      );

      // Closed while the request is pending, then opened from a rows link.
      await discardDrawer({ rerender, view });
      ui.emptyDefaults = true;
      ui.nameAvailable = false;
      ui.store.prefill = { row_ids: [row.row_id], row_refs: [row.row_id] };
      ui.sampleRows = timing === "delayed" ? null : [];
      ui.store.openRunExperiment = true;
      await act(async () => rerender(view()));
      // Only now does the earlier request fail unconfirmed and save its draft.
      await act(async () => failFirst(admissionError));
      await waitFor(() =>
        expect(
          client.getQueryData(recoveryDraftKey(submittedBody.dataset_id)),
        ).toEqual(expect.objectContaining({ body: submittedBody })),
      );
      if (timing === "delayed") {
        ui.sampleRows = [row];
        await act(async () => rerender(view()));
      } else {
        await act(async () =>
          finishRead?.({
            data: { result: { table: [row], metadata: { total_rows: 1 } } },
          }),
        );
        expect(api.get).not.toHaveBeenCalledWith(
          "/dataset-rows",
          expect.objectContaining({
            params: expect.objectContaining({ current_page_index: 1 }),
          }),
        );
      }
      // The open drawer keeps what the link selected; nothing is swapped in.
      expect(name()).toHaveValue("");
      expect(rows()).toBe(row.row_id);

      // The next open, without the link, restores the saved request whole.
      fireEvent.change(name(), { target: { value: "Link draft" } });
      await discardDrawer({ rerender, view }, { reopened: true });
      ui.store.prefill = undefined;
      ui.sampleRows = [];
      ui.store.openRunExperiment = true;
      await act(async () => rerender(view()));
      await waitFor(() => expect(name()).toHaveValue(submittedBody.name));
      expect(rows()).toBe("");
      submit();
      await waitFor(() => expect(ui.submitted).toHaveBeenCalledOnce());
      expect(api.post).toHaveBeenCalledTimes(2);
      expect(api.post.mock.calls[1][1]).toEqual(submittedBody);
      client.clear();
    },
  );

  it("keeps a restored name that equals the cached name suggestion", async () => {
    ui.suggestion = { suggestedName: "Recovery experiment" };
    api.post.mockRejectedValueOnce(admissionError).mockResolvedValue(confirmed);
    const drawer = mountDrawer();
    const name = () => screen.getByRole("textbox", { name: "Experiment name" });
    const promptName = () =>
      screen.getByRole("textbox", { name: "Prompt name" });
    expect(name()).toHaveValue("Recovery experiment");
    fireEvent.change(promptName(), { target: { value: "Named run" } });
    submit();
    await waitFor(() => expect(ui.snackbar).toHaveBeenCalledOnce());
    const submittedBody = JSON.parse(JSON.stringify(api.post.mock.calls[0][1]));
    expect(submittedBody.name).toBe("Recovery experiment");
    assertNoSuccessEffects();

    // Reopened with empty defaults, the suggestion fills the name first; the
    // restored runs must not then rename the restored request automatically.
    await discardDrawer(drawer);
    ui.emptyDefaults = true;
    ui.nameAvailable = false;
    ui.store.openRunExperiment = true;
    await act(async () => drawer.rerender(drawer.view()));
    await act(async () => {});
    expect(promptName()).toHaveValue("Named run");
    expect(name()).toHaveValue("Recovery experiment");
    submit();
    await waitFor(() => expect(ui.submitted).toHaveBeenCalledOnce());
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1]).toEqual(submittedBody);
    drawer.client.clear();
  });

  it("keeps a restored draft being edited when a later request saves another", async () => {
    let failA;
    let failB;
    api.post
      .mockReturnValueOnce(
        new Promise((_, reject) => {
          failA = reject;
        }),
      )
      .mockReturnValueOnce(
        new Promise((_, reject) => {
          failB = reject;
        }),
      )
      .mockResolvedValue(confirmed);
    const drawer = mountDrawer();
    const { rerender, view, client } = drawer;
    const name = () => screen.getByRole("textbox", { name: "Experiment name" });
    const reopen = async () => {
      ui.store.openRunExperiment = true;
      await act(async () => rerender(view()));
    };

    // Request A is closed while pending; request B, from a fresh drawer, too.
    fireEvent.change(name(), { target: { value: "Request A" } });
    submit();
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    await discardDrawer(drawer);
    await reopen();
    fireEvent.change(name(), { target: { value: "Request B" } });
    submit();
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
    const bodyB = JSON.parse(JSON.stringify(api.post.mock.calls[1][1]));
    await discardDrawer(drawer, { reopened: true });

    // A fails unconfirmed; the next open restores A and it is edited.
    await act(async () => failA(admissionError));
    ui.emptyDefaults = true;
    ui.nameAvailable = false;
    await reopen();
    await waitFor(() => expect(name()).toHaveValue("Request A"));
    fireEvent.change(name(), { target: { value: "Request A edited" } });

    // B then fails unconfirmed: the open drawer is not restored a second time.
    await act(async () => failB(admissionError));
    await waitFor(() =>
      expect(client.getQueryData(recoveryDraftKey(bodyB.dataset_id))).toEqual(
        expect.objectContaining({ body: bodyB }),
      ),
    );
    await act(async () => {});
    expect(name()).toHaveValue("Request A edited");

    // The next open restores B, the request saved last, and retries it as is.
    await discardDrawer(drawer, { reopened: true });
    await reopen();
    await waitFor(() => expect(name()).toHaveValue("Request B"));
    submit();
    await waitFor(() => expect(ui.submitted).toHaveBeenCalledOnce());
    expect(api.post).toHaveBeenCalledTimes(3);
    expect(api.post.mock.calls[2][1]).toEqual(bodyB);
    client.clear();
  });

  it.each([
    { status: false, detail: "Not confirmed" },
    { status: true, result: "Experiment created successfully" },
    { status: true, result: {} },
    { status: true, result: { experiment_id: "not-an-id" } },
  ])(
    "refuses a transport 200 without a persisted identity: %j",
    async (data) => {
      api.post.mockResolvedValue({ data });
      mountDrawer();
      submit();
      await waitFor(() => expect(ui.snackbar).toHaveBeenCalled());
      assertNoSuccessEffects();
      expect(screen.getByTestId("experiment-drawer")).toBeInTheDocument();
    },
  );

  it("retains admission recovery for a semantic refusal delivered as a 200", async () => {
    api.post.mockResolvedValue({ data: admissionError });
    const { rerender, view } = mountDrawer();
    submit();
    await waitFor(() =>
      expect(ui.snackbar).toHaveBeenCalledWith(admissionError.detail, {
        variant: "error",
      }),
    );
    ui.nameAvailable = false;
    await act(async () => rerender(view()));
    expect(screen.queryByTestId("wizard-run-blocked")).not.toBeInTheDocument();
    assertNoSuccessEffects();
  });

  it("does not waive name validation for a generic 503", async () => {
    api.post.mockRejectedValue({ statusCode: 503, detail: "Unavailable" });
    const { rerender, view } = mountDrawer();
    submit();
    await waitFor(() => expect(ui.snackbar).toHaveBeenCalled());
    ui.nameAvailable = false;
    await act(async () => rerender(view()));
    expect(screen.getByTestId("wizard-run-blocked")).toBeDisabled();
    expect(api.post).toHaveBeenCalledOnce();
    assertNoSuccessEffects();
  });

  it("preserves the existing late server validation guard", async () => {
    ui.blockAtValidation = true;
    mountDrawer();
    submit();
    await waitFor(() =>
      expect(ui.store.setCurrentStep).toHaveBeenCalledWith(3),
    );
    expect(api.post).not.toHaveBeenCalled();
    assertNoSuccessEffects();
  });

  it("coalesces simultaneous form submits before React renders pending state", async () => {
    let reject;
    api.post.mockImplementation(
      () =>
        new Promise((_, fail) => {
          reject = fail;
        }),
    );
    mountDrawer();
    const form = screen
      .getByRole("button", { name: /Run experiment/ })
      .closest("form");
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
    await act(async () => reject(admissionError));
    await waitFor(() => expect(ui.snackbar).toHaveBeenCalledOnce());
    expect(api.post).toHaveBeenCalledOnce();
    assertNoSuccessEffects();
  });
});
