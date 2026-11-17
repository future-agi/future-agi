/* eslint-disable react/prop-types */
// When every member of a group is left out, the apply answers 400 with each
// member's sentence, joined: past the 240 characters after which the app's
// error handler says only "Something went wrong". Each door shows that text
// in full, once, and the app's handler adds nothing beside it. Models are
// named as the model selector names them, from its own list. A built-in
// evaluation left out is named once, by its reason; each place then adds the
// action it offers, told apart by the refusal's code, never its words.
import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import { handleError } from "src/utils/queryErrorHandler";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { WORKSPACE_RECOVERY_HANDLED } from "src/contexts/workspaceRefusalHold";
import {
  applyErrorText,
  inDrawer,
  memberModelLine,
  notifyApplyError,
  notifyRefused,
  refusedText,
  withModelNames,
} from "../applyGroupModel";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
const COLUMNS = [
  { id: "c-out", name: "Answer", role: "output", data_type: "text" },
];
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (id) => ({
    queryKey: ["apply-group-refusal-table", id],
    queryFn: async () => ({ data: { result: { column_config: COLUMNS } } }),
  }),
}));

const reason = (model) =>
  `its model, ${model}, can't run on this deployment. Change the evaluation's model, then apply again.`;
const notAdded = (name, model) => `${name} was not added: ${reason(model)}`;
// What the apply answers when all three are left out.
const EVERY_ONE = [
  notAdded("Safety", "Turing Large"),
  notAdded("Politeness", "Turing Large"),
  notAdded("Brevity", "Turing Small"),
].join(" ");
// The app's axios hands a failed request's body on, with its status.
const refused400 = (text) => ({
  status: false,
  message: text,
  result: text,
  statusCode: 400,
});
const ERROR = { variant: "error" };
const WARNING = { variant: "warning" };

const MEMBERS = ["Safety", "Politeness", "Brevity"].map((name) => ({
  eval_template_id: `t-${name}`,
  name,
  required_keys: ["output"],
  optional_keys: [],
  apply_model: "gpt-4o-mini",
  apply_refusal: null,
}));
const withMembers = (members) => ({
  group: { id: "g1", name: "Checks" },
  detail: { eval_group: { id: "g1", name: "Checks" }, members },
  skipped: [],
});
const PICK = withMembers(MEMBERS);
// The group the menu picks; a test may pick another.
let picked = PICK;
vi.mock("../AddEvalGroupButton", () => ({
  default: ({ onPick }) => (
    <button type="button" onClick={() => onPick(picked)}>
      pick group
    </button>
  ),
}));

const axios = (await import("src/utils/axios")).default;
const { enqueueSnackbar } = await import("notistack");
const { default: ApplyEvalGroupButton } = await import(
  "../ApplyEvalGroupDialog"
);
const modelLine = (id) => screen.getByTestId(`apply-group-member-model-${id}`);

describe("a refused group apply, in full", () => {
  beforeEach(() => {
    axios.post.mockReset();
    enqueueSnackbar.mockReset();
    picked = PICK;
  });

  it("the shared helper gives a 400's text whole, past 240 characters", () => {
    expect(EVERY_ONE.length).toBeGreaterThan(240);
    expect(applyErrorText(refused400(EVERY_ONE))).toBe(EVERY_ONE);
  });

  it("the app's other rules stay", () => {
    const internal = `${EVERY_ONE} Traceback (most recent call last)`;
    const twoLines = `${EVERY_ONE}\n${EVERY_ONE}`;
    const failed = { status: false, result: "Not here", statusCode: 500 };
    const workspace = {
      ...refused400("Select an accessible workspace."),
      handledBy: WORKSPACE_RECOVERY_HANDLED,
    };

    expect(applyErrorText(refused400(internal))).toBe(
      "Couldn't apply the group",
    );
    expect(applyErrorText(refused400(twoLines))).toBe(
      "Couldn't apply the group",
    );
    expect(applyErrorText(failed)).toBe("Couldn't apply the group");
    expect(applyErrorText(workspace)).toBe(
      "Your workspace changed. Try again.",
    );
  });

  it("each drawer's onError shows it once, in full", () => {
    const notify = vi.fn();

    notifyApplyError(refused400(EVERY_ONE), notify);

    expect(notify.mock.calls).toEqual([[EVERY_ONE, ERROR]]);
  });

  it("a 429 gets no toast from it, as from the app's handler", () => {
    const notify = vi.fn();
    const limit = { status: false, result: "Slow down", statusCode: 429 };

    notifyApplyError(limit, notify);

    expect(notify).not.toHaveBeenCalled();
  });

  it("every other screen keeps the 240-character rule", () => {
    const shown = getSafeActionErrorMessage(refused400(EVERY_ONE), "Fallback");

    expect(shown).toBe("Fallback");
  });

  it("the Apply dialog shows it once, and nothing generic", async () => {
    axios.post.mockRejectedValue(refused400(EVERY_ONE));
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
      // The app's own handler, as app.jsx installs it.
      mutationCache: new MutationCache({ onError: handleError }),
    });
    render(
      <QueryClientProvider client={qc}>
        <ApplyEvalGroupButton
          page="experiment"
          datasetId="ds1"
          experimentId="ex1"
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "pick group" }));
    await waitFor(() => expect(modelLine("t-Safety")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("apply-group-apply"));

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(EVERY_ONE, ERROR),
    );
    // One toast, the server's words; no "Something went wrong" beside it.
    expect(enqueueSnackbar.mock.calls).toEqual([[EVERY_ONE, ERROR]]);
  });
});

describe("models named as the model selector names them", () => {
  it("names Jev by the selector's name in every refusal", () => {
    const jev = notAdded("Tone", "jev-latest");
    const named = notAdded("Tone", "Jev latest");
    const pinned = { apply_refusal: `Won't be added: ${reason("jev-1.13.0")}` };

    expect(withModelNames(jev)).toBe(
      "Tone was not added: its model, Jev latest, can't run on this deployment. Change the evaluation's model, then apply again.",
    );
    expect(refusedText([{ name: "Tone", reason: jev }])).toBe(named);
    expect(applyErrorText(refused400(jev))).toBe(named);
    expect(memberModelLine(pinned)).toBe(
      "Won't be added: its model, Jev 1.13 (pinned), can't run on this deployment. Change the evaluation's model, then apply again.",
    );
  });

  it("names an added member's model the same way", () => {
    const line = (model) => memberModelLine({ apply_model: model });

    expect(line("jev-latest")).toBe("Model: Jev latest");
    expect(line("turing_large")).toBe("Model: Turing Large");
    // A bare size, which the selector names by its Turing model too.
    expect(line("small")).toBe("Model: Turing Small");
    // Neither the selector nor the backend's map names these: each keeps
    // its id.
    expect(line("protect")).toBe("Model: protect");
    expect(line("protect_flash")).toBe("Model: protect_flash");
    expect(line("turing_large_xl")).toBe("Model: turing_large_xl");
  });

  it("leaves a name the server already gives as it is", () => {
    const named = notAdded("Safety", "Turing Large");

    expect(withModelNames(named)).toBe(named);
  });

  it("leaves a model no list names as its id in every sentence", () => {
    for (const model of ["protect", "protect_flash", "turing_large_xl"]) {
      const general = notAdded("Guard", model);
      const builtIn = `Guard was not added: it uses ${model}, which can't run here.`;

      expect(withModelNames(general)).toBe(general);
      expect(withModelNames(builtIn)).toBe(builtIn);
    }
  });

  it("the model selector keeps no list of its own", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const selector = readFileSync(
      join(here, "../../../evals/components/ModelSelector.jsx"),
      "utf8",
    );

    expect(selector).toContain('} from "./evaluatorModels";');
    expect(selector).not.toMatch(/const (FAGI|JEV)_MODELS = \[/);
    // It names the current model by the same function this dialog uses.
    expect(selector).toContain("return evaluatorModelLabel(model) ?? model;");
  });
});

// A built-in evaluation left out, and each place's action, word for word.
const BUILT_IN_WONT =
  "Won't be added: it uses Turing Large, which can't run here.";
const BUILT_IN_NOT_ADDED =
  "Safety was not added: it uses Turing Large, which can't run here.";
const GENERAL_WONT = `Won't be added: ${reason("Turing Large")}`;
const GENERAL_NOT_ADDED = notAdded("Brevity", "Turing Large");
const builtInEntry = {
  eval_template_id: "t-Safety",
  name: "Safety",
  reason: BUILT_IN_NOT_ADDED,
  code: "built_in",
};
const generalEntry = {
  eval_template_id: "t-Brevity",
  name: "Brevity",
  reason: GENERAL_NOT_ADDED,
  code: "model",
};
// The 400 when every member is left out: the joined text, and each entry.
const everyOneRefused = (refused) => ({
  ...refused400(refused.map((entry) => entry.reason).join(" ")),
  refused,
});

function openApply() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ApplyEvalGroupButton
        page="experiment"
        datasetId="ds1"
        experimentId="ex1"
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "pick group" }));
}

describe("a built-in evaluation left out: its reason, then the place's action", () => {
  beforeEach(() => {
    axios.post.mockReset();
    enqueueSnackbar.mockReset();
    picked = withMembers([
      {
        ...MEMBERS[0],
        apply_model: null,
        apply_refusal: BUILT_IN_WONT,
        apply_refusal_code: "built_in",
      },
      {
        ...MEMBERS[1],
        apply_model: null,
        apply_refusal: GENERAL_WONT,
        apply_refusal_code: "model",
      },
      MEMBERS[2],
    ]);
  });

  it("the Apply dialog before Apply: its row adds the dialog's action", async () => {
    openApply();
    await waitFor(() => expect(modelLine("t-Safety")).toBeInTheDocument());

    expect(modelLine("t-Safety").textContent).toBe(
      "Won't be added: it uses Turing Large, which can't run here. Clear its checkbox, or choose another group.",
    );
    // A member left out for the evaluation's model gets no action.
    expect(modelLine("t-Politeness").textContent).toBe(GENERAL_WONT);
  });

  it("a partial success: the toast gives the reason and nothing more", async () => {
    axios.post.mockResolvedValue({
      data: { result: { run: "started" }, refused: [builtInEntry] },
    });
    openApply();
    await waitFor(() => expect(modelLine("t-Safety")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("apply-group-apply"));

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(BUILT_IN_NOT_ADDED, WARNING),
    );
    // A drawer's partial success says the same.
    const notify = vi.fn();
    notifyRefused({ data: { refused: [builtInEntry] } }, notify);
    expect(notify.mock.calls).toEqual([[BUILT_IN_NOT_ADDED, WARNING]]);
  });

  it("the Apply dialog when every member is refused: the dialog's action", async () => {
    axios.post.mockRejectedValue(everyOneRefused([builtInEntry, generalEntry]));
    openApply();
    await waitFor(() => expect(modelLine("t-Safety")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("apply-group-apply"));

    const toast =
      "Safety was not added: it uses Turing Large, which can't run here. Clear its checkbox, or choose another group. " +
      GENERAL_NOT_ADDED;
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(toast, ERROR),
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
  });

  it("a drawer when every member is refused: the drawer's action", () => {
    const notify = vi.fn();

    notifyApplyError(everyOneRefused([builtInEntry]), notify, inDrawer(true));

    expect(notify.mock.calls).toEqual([
      [
        "Safety was not added: it uses Turing Large, which can't run here. Remove it from the group.",
        ERROR,
      ],
    ]);
  });

  it("a drawer whose chip shows no remove: the reason alone", () => {
    const notify = vi.fn();

    notifyApplyError(everyOneRefused([builtInEntry]), notify, inDrawer(false));

    expect(notify.mock.calls).toEqual([[BUILT_IN_NOT_ADDED, ERROR]]);
  });

  it("an answer that lists no entries keeps its own text", () => {
    const notify = vi.fn();
    const drawer = { builtInAction: "Remove it from the group." };

    notifyApplyError(refused400(BUILT_IN_NOT_ADDED), notify, drawer);

    expect(notify.mock.calls).toEqual([[BUILT_IN_NOT_ADDED, ERROR]]);
  });

  it("names a built-in evaluation's model as the selector does", () => {
    const jev =
      "Safety was not added: it uses jev-latest, which can't run here.";
    const before = {
      apply_refusal:
        "Won't be added: it uses jev-latest, which can't run here.",
      apply_refusal_code: "built_in",
    };
    // An evaluation whose own name has "uses" in it.
    const named =
      "Tool uses check was not added: it uses jev-latest, which can't run here.";

    expect(refusedText([{ reason: jev, code: "built_in" }])).toBe(
      "Safety was not added: it uses Jev latest, which can't run here.",
    );
    expect(memberModelLine(before)).toBe(
      "Won't be added: it uses Jev latest, which can't run here. Clear its checkbox, or choose another group.",
    );
    expect(withModelNames(named)).toBe(
      "Tool uses check was not added: it uses Jev latest, which can't run here.",
    );
  });
});
