// Every door that applies a group lists the members it left out, by the
// server's sentence, through the one shared notifyRefused, and shows a
// refused apply itself, in full, through the one shared notifyApplyError,
// with the action its place offers a built-in evaluation left out (a drawer
// only while the member's chip shows its remove); what each door does with
// the members added is unchanged. The two drawers are too wide to mount
// here, so this pins their wiring in the source, and runs the shared path
// they call on the response shape the apply returns.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { notifyRefused } from "../applyGroupModel";

const HERE = dirname(fileURLToPath(import.meta.url));
const source = (...parts) => readFileSync(join(HERE, "..", ...parts), "utf8");

// A door's group-apply mutation, with its options.
const applyMutation = (src) => {
  const start = src.indexOf(
    "const { mutate: handleApplyGroup } = useMutation(",
  );
  expect(start).toBeGreaterThan(-1);
  // The mutation's own closing line (two spaces in), not an inner one.
  const end = src.indexOf("\n  });", start);
  return src.slice(start, end);
};

const DOORS = {
  "the Evaluations drawer": {
    src: source("..", "EvaluationDrawer", "EvaluationDrawer.jsx"),
    added: "onSuccess(variables, data?.data?.result);",
  },
  "the task drawer": {
    src: source("..", "EvalsTasks", "EditTaskDrawer", "DetailsEdit.jsx"),
    added: "addEval(variable);",
  },
};

const SENTENCE =
  "Safety was not added: its model, Turing Large, can't run on this deployment. Change the evaluation's model, then apply again.";

describe("every door that applies a group", () => {
  it.each(Object.keys(DOORS))("%s lists the members not added", (door) => {
    const { src, added } = DOORS[door];
    const handler = applyMutation(src);

    expect(src).toMatch(
      /import \{\s*inDrawer,\s*notifyApplyError,\s*notifyRefused,\s*\} from "src\/sections\/common\/EvalHandoffs\/applyGroupModel";/,
    );
    expect(handler).toContain("notifyRefused(data, enqueueSnackbar);");
    // The members added are handled as before.
    expect(handler).toContain(added);
  });

  it.each(Object.keys(DOORS))("%s shows a refusal itself, in full", (door) => {
    const { src } = DOORS[door];
    const handler = applyMutation(src);

    expect(handler).toContain("meta: { errorHandled: true },");
    // Each apply call shows it, with the drawer's action only while a
    // member's chip shows its remove.
    expect(handler).not.toContain("onError:");
    expect(src).toContain(
      "{ isGrouped = false, memberRemovable = false } = {},",
    );
    expect(src).toContain(
      "notifyApplyError(e, enqueueSnackbar, inDrawer(memberRemovable)),",
    );
  });

  it("the Apply dialog lists them the same way", () => {
    const src = source("ApplyEvalGroupDialog.jsx");

    expect(src).toContain("notifyRefused(res, enqueueSnackbar);");
    expect(src).toContain("meta: { errorHandled: true },");
    expect(src).toContain(
      "onError: (e) => notifyApplyError(e, enqueueSnackbar, IN_APPLY_DIALOG),",
    );
  });

  it("the drawers' group form passes whether a member's chip shows its remove", () => {
    const drawer = (file) => source("..", "EvaluationDrawer", file);
    const content = drawer("EvaluationMappingFormContent.jsx");
    const form = drawer("EvaluationMappingForm.jsx");

    // The chip's remove, as the form's content shows it.
    expect(content).toContain(
      'condition={typeof handleEvalDelete === "function"}',
    );
    expect(content).toMatch(
      /\{filteredVisibleItems\?\.length !== 1 &&\s+!isSampleFutureAgiBuilt && \(/,
    );
    // The same three terms, passed with each group apply that can refuse.
    expect(form).toMatch(
      /const memberRemovable =\s+typeof handleEvalDelete === "function" &&\s+filteredVisibleItems\?\.length !== 1 &&\s+!isSampleFutureAgiBuilt;/,
    );
    const grouped =
      "{ isGrouped: selectedEval?.isGroupEvals, memberRemovable }";
    expect(form.split(grouped)).toHaveLength(3);
    expect(form).toMatch(/isGrouped: true,\s+memberRemovable,\s+\}\);/);
  });

  it("shows the server's sentence for a member not added", () => {
    const notify = vi.fn();
    const response = {
      data: {
        status: true,
        result: [{ id: "c1", name: "Tone" }],
        refused: [
          { eval_template_id: "t-safe", name: "Safety", reason: SENTENCE },
        ],
      },
    };

    notifyRefused(response, notify);

    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith(SENTENCE, { variant: "warning" });
    expect(response.data.result).toEqual([{ id: "c1", name: "Tone" }]);
  });
});
