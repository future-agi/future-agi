import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn() },
}));
const toasts = [];
vi.mock("notistack", () => ({
  enqueueSnackbar: (text, opts) => toasts.push({ text, opts }),
}));

const axios = (await import("src/utils/axios")).default;
const { endpoints } = await import("src/utils/axios");
const { default: AddEvalGroupButton } = await import("../AddEvalGroupButton");

const SE = {
  id: "g-se",
  name: "Support essentials",
  evals_count: 4,
  is_sample: false,
  required_keys: [],
};
const SAMPLE = {
  id: "g-s",
  name: "Safety",
  evals_count: 2,
  is_sample: true,
  required_keys: [],
};
const MEMBERS = [
  ["c", "correctness_vs_reference"],
  ["g", "groundedness_policy_kb"],
  ["t", "tone_on_brand"],
  ["k", "task_completion"],
].map(([id, name]) => ({
  eval_template_id: id,
  name,
  required_keys: ["output"],
}));

const respond = (url) => {
  const G = endpoints.develop.eval.groupEvals;
  if (url === G)
    return { result: { data: [SE, SAMPLE], total_count: 2, total_pages: 1 } };
  if (url === `${G}g-se/`)
    return {
      result: {
        eval_group: { id: "g-se", name: "Support essentials" },
        members: MEMBERS,
      },
    };
  if (url === `${G}g-s/`)
    return {
      result: {
        eval_group: { id: "g-s", name: "Safety", is_sample: true },
        members: MEMBERS.slice(0, 2),
      },
    };
  const tpl = MEMBERS.find(
    (m) => url === endpoints.develop.eval.getEvalDetail(m.eval_template_id),
  );
  if (tpl)
    return {
      result: {
        id: tpl.eval_template_id,
        name: tpl.name,
        model: "gpt-4o",
        required_keys: ["input", "output"],
        config: { required_keys: ["input", "output"] },
      },
    };
  const ver = MEMBERS.find(
    (m) => url === endpoints.develop.eval.getEvalVersions(m.eval_template_id),
  );
  if (ver)
    return {
      result: {
        versions: [
          {
            id: `ver-${ver.eval_template_id}-3`,
            version_number: 3,
            is_default: true,
          },
          { id: "old", version_number: 2 },
        ],
      },
    };
  throw new Error(`unexpected GET ${url}`);
};

const renderButton = (props) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AddEvalGroupButton {...props} />
    </QueryClientProvider>,
  );
};

describe("AddEvalGroupButton", () => {
  beforeEach(() => {
    toasts.length = 0;
    axios.get.mockReset();
    axios.get.mockImplementation(async (url) => ({ data: respond(url) }));
  });

  it("lists groups with counts, member previews and the sample tag", async () => {
    renderButton({ existing: [], onAdd: vi.fn() });
    fireEvent.click(screen.getByRole("button", { name: /Add a group/ }));
    expect(
      await screen.findByText("Support essentials · 4 evaluations"),
    ).toBeTruthy();
    expect(screen.getByText("Safety · 2 evaluations")).toBeTruthy();
    expect(screen.getAllByText("Future AGI built")).toHaveLength(1);
    expect(
      await screen.findByText(
        "correctness_vs_reference, groundedness_policy_kb, tone_on_brand +1",
      ),
    ).toBeTruthy();
    expect(screen.getByPlaceholderText("Search groups")).toBeTruthy();
    expect(screen.getByText("Manage groups ↗").getAttribute("href")).toBe(
      "/dashboard/evaluations/groups",
    );
  });

  it("adds only the members the step lacks, once each, at the current version", async () => {
    const onAdd = vi.fn();
    const existing = [
      { templateId: "c", name: "Correctness" },
      { templateId: "g", name: "Groundedness" },
      { templateId: "t", name: "Tone" },
    ];
    renderButton({ existing, onAdd });
    fireEvent.click(screen.getByRole("button", { name: /Add a group/ }));
    fireEvent.click(
      await screen.findByText("Support essentials · 4 evaluations"),
    );
    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
    const arg = onAdd.mock.calls[0][0];
    expect(arg).toMatchObject({
      templateId: "k",
      name: "task_completion",
      versionId: "ver-k-3",
      version: "v3",
    });
    expect(arg.requiredKeys).toEqual(["input", "output"]);
    await waitFor(() =>
      expect(toasts.map((t) => t.text)).toEqual([
        "Added 1 evaluation from Support essentials · Correctness, Groundedness and Tone were already added.",
      ]),
    );
  });

  it("says so when every member is already on the step", async () => {
    const onAdd = vi.fn();
    const existing = MEMBERS.map((m) => ({
      templateId: m.eval_template_id,
      name: m.name,
    }));
    renderButton({ existing, onAdd });
    fireEvent.click(screen.getByRole("button", { name: /Add a group/ }));
    fireEvent.click(
      await screen.findByText("Support essentials · 4 evaluations"),
    );
    await waitFor(() =>
      expect(toasts.map((t) => t.text)).toEqual([
        "Every evaluation in Support essentials is already added.",
      ]),
    );
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("with no groups points to Evals", async () => {
    axios.get.mockImplementation(async () => ({
      data: { result: { data: [], total_count: 0, total_pages: 0 } },
    }));
    renderButton({ existing: [], onAdd: vi.fn() });
    fireEvent.click(screen.getByRole("button", { name: /Add a group/ }));
    expect(await screen.findByText(/No groups yet ·/)).toBeTruthy();
    expect(
      screen.getByText("Create one in Evals ↗").getAttribute("href"),
    ).toBe("/dashboard/evaluations/groups");
  });
});
