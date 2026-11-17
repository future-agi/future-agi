// New experiment's "Add a group" gives each member it adds a model. A member
// whose template names a model keeps that model, whatever the group read
// says. A member whose template names none gets the model the group read
// names for it (`apply_model`: the model Apply would use for it). A member
// the read leaves out is still not added or read, and a member that neither
// its template nor the read gives a model is added as before: the browser
// never picks a model of its own.
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

const axios = (await import("src/utils/axios")).default;
const { endpoints } = await import("src/utils/axios");
const { default: AddEvalGroupButton } = await import("../AddEvalGroupButton");

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

// Plain judge's template names no model (read as null). Blank judge's model
// is blank and its default version names the model the read gives, so the
// read's model is not the server's usual fallback. Own judge's template names
// its own model, and the read names another.
const PLAIN = member("t-plain", "Plain judge", { apply_model: "turing_large" });
const BLANK = member("t-blank", "Blank judge", { apply_model: "turing_flash" });
const OWN = member("t-own", "Own judge", { apply_model: "turing_large" });
const OWN_OLDER_READ = { ...OWN, apply_model: null };
const SAFETY = member("t-safe", "Safety", {
  apply_refusal: "Won't be added: it uses Turing Large, which can't run here.",
  apply_refusal_code: "built_in",
});
const OLDER = member("t-older", "Older judge");

// Each template's own model, as the evaluation detail read returns it.
const TEMPLATE_MODEL = {
  "t-plain": null,
  "t-blank": "",
  "t-own": "gpt-4o-mini",
  "t-safe": "turing_large",
  "t-older": null,
};
const TEMPLATE_IDS = Object.keys(TEMPLATE_MODEL);
const NAMES = {
  "t-plain": PLAIN.name,
  "t-blank": BLANK.name,
  "t-own": OWN.name,
  "t-safe": SAFETY.name,
  "t-older": OLDER.name,
};

const GROUPS = {
  "g-plain": { name: "No model", members: [PLAIN] },
  "g-own": { name: "Own model", members: [OWN] },
  "g-own-older": { name: "Own model, older read", members: [OWN_OLDER_READ] },
  "g-mix": { name: "Mixed", members: [PLAIN, SAFETY, OWN, BLANK] },
  "g-older": { name: "Older read", members: [OLDER] },
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
        total_count: Object.keys(GROUPS).length,
        total_pages: 1,
      },
    };
  for (const [id, g] of Object.entries(GROUPS)) {
    if (url === `${G}${id}/`)
      return {
        result: { eval_group: { id, name: g.name }, members: g.members },
      };
  }
  const tplId = TEMPLATE_IDS.find(
    (id) => url === endpoints.develop.eval.getEvalDetail(id),
  );
  if (tplId)
    return {
      result: {
        id: tplId,
        name: NAMES[tplId],
        model: TEMPLATE_MODEL[tplId],
        required_keys: ["output"],
        config: { required_keys: ["output"] },
      },
    };
  const verId = TEMPLATE_IDS.find(
    (id) => url === endpoints.develop.eval.getEvalVersions(id),
  );
  if (verId)
    return {
      result: {
        versions: [{ id: `ver-${verId}`, version_number: 2, is_default: true }],
      },
    };
  throw new Error(`unexpected GET ${url}`);
};

const asked = () => axios.get.mock.calls.map(([url]) => url);
const templateReads = (id) => [
  endpoints.develop.eval.getEvalDetail(id),
  endpoints.develop.eval.getEvalVersions(id),
];

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
// What onAdd received, once it has been called `n` times.
const addedWith = async (onAdd, n) => {
  await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(n));
  return onAdd.mock.calls.map(([arg]) => arg);
};
const modelsOf = (args) =>
  Object.fromEntries(args.map((arg) => [arg.templateId, arg.model ?? null]));

beforeEach(() => {
  toasts.length = 0;
  axios.get.mockReset();
  axios.post.mockReset();
  axios.get.mockImplementation(async (url) => ({ data: respond(url) }));
});

describe("New experiment's Add a group gives each member a model", () => {
  it("adds a member whose template names no model with the model the group read names for it", async () => {
    const onAdd = vi.fn();
    withClient(<AddEvalGroupButton existing={[]} onAdd={onAdd} />);
    await addGroup("No model · 1 evaluation");

    const [arg] = await addedWith(onAdd, 1);
    expect(arg.templateId).toBe("t-plain");
    expect(arg.model).toBe("turing_large");
  });

  it("keeps the model a member's template names, exactly as before, whatever the group read says", async () => {
    const withRead = vi.fn();
    const first = withClient(
      <AddEvalGroupButton existing={[]} onAdd={withRead} />,
    );
    await addGroup("Own model · 1 evaluation");
    const [readSaysOther] = await addedWith(withRead, 1);
    first.unmount();

    const withoutRead = vi.fn();
    withClient(<AddEvalGroupButton existing={[]} onAdd={withoutRead} />);
    await addGroup("Own model, older read · 1 evaluation");
    const [readSaysNone] = await addedWith(withoutRead, 1);

    expect(readSaysOther.model).toBe("gpt-4o-mini");
    // The read's model changes nothing for this member: what the step
    // receives is the same, key for key and in the same order.
    expect(JSON.stringify(readSaysOther)).toBe(JSON.stringify(readSaysNone));
  });

  it("in a mixed group gives each member its model and still leaves out the member the read refuses", async () => {
    const onAdd = vi.fn();
    withClient(<AddEvalGroupButton existing={[]} onAdd={onAdd} />);
    await addGroup("Mixed · 4 evaluations");

    const args = await addedWith(onAdd, 3);
    expect(args.map((arg) => arg.templateId)).toEqual([
      "t-plain",
      "t-own",
      "t-blank",
    ]);
    expect(modelsOf(args)).toEqual({
      "t-plain": "turing_large",
      "t-own": "gpt-4o-mini",
      "t-blank": "turing_flash",
    });
    for (const url of templateReads("t-safe"))
      expect(asked()).not.toContain(url);
  });

  it("adds a member as before when neither its template nor the group read names a model", async () => {
    const onAdd = vi.fn();
    withClient(<AddEvalGroupButton existing={[]} onAdd={onAdd} />);
    await addGroup("Older read · 1 evaluation");

    const [arg] = await addedWith(onAdd, 1);
    expect(arg.templateId).toBe("t-older");
    expect(arg.model ?? null).toBeNull();
  });
});
