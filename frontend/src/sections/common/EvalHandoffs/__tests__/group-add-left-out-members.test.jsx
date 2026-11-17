// New experiment's "Add a group" adds the members this deployment can run.
// A member the group read says Apply would leave out (`apply_refusal`) is
// not added and nothing is asked about it; the door shows the read's first
// sentence for it and, for an evaluation whose model can be changed, how to
// add it. A group whose every member is left out adds nothing. The Apply
// dialog, which takes its pick from the same menu, shows its rows as before.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn() },
}));
const toasts = [];
vi.mock("notistack", () => ({
  enqueueSnackbar: (text, opts) => toasts.push({ text, opts }),
}));
const COLUMNS = [
  { id: "c-out", name: "Answer", role: "output", data_type: "text" },
];
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (id) => ({
    queryKey: ["group-add-left-out-table", id],
    queryFn: async () => ({ data: { result: { column_config: COLUMNS } } }),
  }),
}));

const axios = (await import("src/utils/axios")).default;
const { endpoints } = await import("src/utils/axios");
const { groupAddRefusalLine } = await import("../applyGroupModel");
const { default: AddEvalGroupButton } = await import("../AddEvalGroupButton");
const { default: ApplyEvalGroupButton } = await import(
  "../ApplyEvalGroupDialog"
);

// The group read's line for a member Apply would leave out, as the server
// words it: its reason, then, for a custom evaluation, Apply's own action.
const CUSTOM_LEFT_OUT =
  "Won't be added: its model, jev-latest, can't run on this deployment. Change the evaluation's model, then apply again.";
const BUILT_IN_LEFT_OUT =
  "Won't be added: it uses Turing Large, which can't run here.";

// What the door says for each of them.
const CUSTOM_LINE =
  "Won't be added: its model, Jev latest, can't run on this deployment. Change the evaluation's model, then add the group again.";
const BUILT_IN_LINE = BUILT_IN_LEFT_OUT;

const member = (id, name, read = {}) => ({
  eval_template_id: id,
  name,
  required_keys: ["output"],
  optional_keys: [],
  apply_model: null,
  apply_refusal: null,
  apply_refusal_code: null,
  ...read,
});
const TONE = member("t-tone", "Tone", { apply_model: "gpt-4o-mini" });
const POLITE = member("t-polite", "Politeness", { apply_model: "gpt-4o" });
const SAFETY = member("t-safe", "Safety", {
  apply_refusal: BUILT_IN_LEFT_OUT,
  apply_refusal_code: "built_in",
});
const BIAS = member("t-bias", "Bias", {
  apply_refusal: CUSTOM_LEFT_OUT,
  apply_refusal_code: "model",
});
const ALL = [TONE, POLITE, SAFETY, BIAS];

const GROUPS = {
  "g-mix": { name: "Checks", members: [TONE, SAFETY, POLITE, BIAS] },
  "g-off": { name: "Blocked", members: [SAFETY, BIAS] },
};

const respond = (url) => {
  const G = endpoints.develop.eval.groupEvals;
  if (url === G)
    return {
      result: {
        data: Object.entries(GROUPS).map(([id, g]) => ({
          id,
          name: g.name,
          evals_count: g.members.length,
          is_sample: false,
          required_keys: [],
        })),
        total_count: 2,
        total_pages: 1,
      },
    };
  for (const [id, g] of Object.entries(GROUPS)) {
    if (url === `${G}${id}/`)
      return {
        result: { eval_group: { id, name: g.name }, members: g.members },
      };
  }
  const tpl = ALL.find(
    (m) => url === endpoints.develop.eval.getEvalDetail(m.eval_template_id),
  );
  if (tpl)
    return {
      result: {
        id: tpl.eval_template_id,
        name: tpl.name,
        model: "gpt-4o-mini",
        required_keys: ["output"],
        config: { required_keys: ["output"] },
      },
    };
  const ver = ALL.find(
    (m) => url === endpoints.develop.eval.getEvalVersions(m.eval_template_id),
  );
  if (ver)
    return {
      result: {
        versions: [
          {
            id: `ver-${ver.eval_template_id}`,
            version_number: 2,
            is_default: true,
          },
        ],
      },
    };
  throw new Error(`unexpected GET ${url}`);
};

const asked = () => axios.get.mock.calls.map(([url]) => url);
const templateReads = (id) => [
  endpoints.develop.eval.getEvalDetail(id),
  endpoints.develop.eval.getEvalVersions(id),
];
const addedIds = (onAdd) => onAdd.mock.calls.map(([arg]) => arg.templateId);

const withClient = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
};
const addGroup = async (line) => {
  fireEvent.click(screen.getByRole("button", { name: /Add a group/ }));
  fireEvent.click(await screen.findByText(line));
};
const toastsShown = () =>
  waitFor(() => expect(toasts.length).toBeGreaterThan(0));

beforeEach(() => {
  toasts.length = 0;
  axios.get.mockReset();
  axios.post.mockReset();
  axios.get.mockImplementation(async (url) => ({ data: respond(url) }));
});

describe("New experiment's Add a group, where the group read leaves members out", () => {
  it("adds only the members this deployment can run", async () => {
    const onAdd = vi.fn();
    withClient(<AddEvalGroupButton existing={[]} onAdd={onAdd} />);
    await addGroup("Checks · 4 evaluations");
    await toastsShown();

    expect(addedIds(onAdd)).toEqual(["t-tone", "t-polite"]);
  });

  it("says, for each member left out, the read's first sentence and how to add a custom one", async () => {
    withClient(<AddEvalGroupButton existing={[]} onAdd={vi.fn()} />);
    await addGroup("Checks · 4 evaluations");
    await toastsShown();

    expect(toasts).toEqual([
      {
        text: "Added 2 evaluations from Checks.",
        opts: { variant: "success" },
      },
      {
        text: `Safety · ${BUILT_IN_LINE} Bias · ${CUSTOM_LINE}`,
        opts: { variant: "warning" },
      },
    ]);
    expect(toasts.map((t) => t.text).join(" ")).not.toMatch(/apply|checkbox/i);
  });

  it("asks nothing about a member it leaves out", async () => {
    withClient(<AddEvalGroupButton existing={[]} onAdd={vi.fn()} />);
    await addGroup("Checks · 4 evaluations");
    await toastsShown();

    for (const id of ["t-safe", "t-bias"])
      for (const url of templateReads(id)) expect(asked()).not.toContain(url);
    for (const id of ["t-tone", "t-polite"])
      for (const url of templateReads(id)) expect(asked()).toContain(url);
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("adds nothing from a group whose every member is left out, and says why for each", async () => {
    const onAdd = vi.fn();
    withClient(<AddEvalGroupButton existing={[]} onAdd={onAdd} />);
    await addGroup("Blocked · 2 evaluations");
    await toastsShown();

    expect(onAdd).not.toHaveBeenCalled();
    expect(toasts).toEqual([
      {
        text: `Safety · ${BUILT_IN_LINE} Bias · ${CUSTOM_LINE}`,
        opts: { variant: "warning" },
      },
    ]);
    expect(toasts[0].text).not.toContain("already added");
    for (const id of ["t-safe", "t-bias"])
      for (const url of templateReads(id)) expect(asked()).not.toContain(url);
  });

  it("names a member already on the step as already added, not as left out", async () => {
    const onAdd = vi.fn();
    withClient(
      <AddEvalGroupButton
        existing={[{ templateId: "t-safe", name: "Safety" }]}
        onAdd={onAdd}
      />,
    );
    await addGroup("Checks · 4 evaluations");
    await toastsShown();

    expect(addedIds(onAdd)).toEqual(["t-tone", "t-polite"]);
    expect(toasts.map((t) => t.text)).toEqual([
      "Added 2 evaluations from Checks · Safety was already added.",
      `Bias · ${CUSTOM_LINE}`,
    ]);
  });
});

describe("the line for a member Add a group leaves out", () => {
  it("is the read's first sentence, then how to add a custom evaluation again", () => {
    expect(groupAddRefusalLine(BIAS)).toBe(CUSTOM_LINE);
  });

  it("is the read's sentence alone for a built-in evaluation", () => {
    expect(groupAddRefusalLine(SAFETY)).toBe(BUILT_IN_LINE);
  });

  it("keeps a model id with periods whole", () => {
    const dotted = member("t-dot", "Dotted", {
      apply_refusal:
        "Won't be added: its model, acme/judge-1.5, can't run on this deployment. Change the evaluation's model, then apply again.",
      apply_refusal_code: "model",
    });
    expect(groupAddRefusalLine(dotted)).toBe(
      "Won't be added: its model, acme/judge-1.5, can't run on this deployment. Change the evaluation's model, then add the group again.",
    );
  });

  it("is null for a member the read does not leave out", () => {
    expect(groupAddRefusalLine(TONE)).toBeNull();
  });
});

// Guard (green before and after): the Apply dialog takes its pick from the
// same menu and keeps every row, line and checkbox as before.
describe("the Apply dialog, from the same menu", () => {
  it("shows each member's row as before, with the read's whole line and the dialog's action", async () => {
    withClient(
      <ApplyEvalGroupButton
        page="experiment"
        datasetId="ds1"
        experimentId="ex1"
      />,
    );
    await addGroup("Checks · 4 evaluations");
    const line = (id) => screen.findByTestId(`apply-group-member-model-${id}`);

    expect(await line("t-safe")).toHaveTextContent(
      "Won't be added: it uses Turing Large, which can't run here. Clear its checkbox, or choose another group.",
    );
    expect(await line("t-bias")).toHaveTextContent(
      "Won't be added: its model, Jev latest, can't run on this deployment. Change the evaluation's model, then apply again.",
    );
    expect(await line("t-tone")).toHaveTextContent("Model: gpt-4o-mini");
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(4);
    boxes.forEach((box) => expect(box).toBeChecked());
    expect(toasts).toEqual([]);
    for (const m of ALL)
      for (const url of templateReads(m.eval_template_id))
        expect(asked()).not.toContain(url);
  });
});
