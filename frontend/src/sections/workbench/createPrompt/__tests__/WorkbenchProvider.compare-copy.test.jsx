import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { usePromptWorkbenchContext } from "../WorkbenchContext";
import WorkbenchProvider from "../WorkbenchProvider";

// FA-PROMPTS-05: More > Compare adds an unsaved draft COPY of the open
// version (its messages and model settings), under a new identity.

// Every endpoint is a callable path: endpoints.develop.runPrompt.getPrompt(id)
// returns "/develop/runPrompt/getPrompt".
const { endpointPath } = vi.hoisted(() => {
  const endpointPath = (path) =>
    new Proxy(() => path, {
      get: (_, key) =>
        typeof key === "symbol" ? undefined : endpointPath(`${path}/${key}`),
    });
  return { endpointPath };
});

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: endpointPath(""),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: {} }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/sections/workbench/createPrompt/hooks/usePromptStreamUrl", () => ({
  usePromptStreamUrl: () => null,
}));

const LATEST = {
  name: "Trip helper",
  version: "V2",
  is_draft: false,
  is_default: true,
  last_saved: "2026-11-01T10:00:00Z",
  variable_names: { city: ["Paris"] },
  prompt_config: [
    {
      messages: [
        {
          role: "system",
          content: [{ type: "text", text: "You plan trips." }],
        },
        {
          role: "user",
          content: [{ type: "text", text: "Plan a day in {{city}}." }],
        },
      ],
      configuration: {
        model: "gpt-4o-mini",
        temperature: 0.3,
        max_tokens: 512,
        output_format: "string",
        model_detail: { model_name: "gpt-4o-mini", providers: "openai" },
      },
    },
  ],
};

const NEW_DRAFT = {
  template_version: "V3",
  is_draft: true,
  is_default: false,
  updated_at: "2026-11-09T10:00:00Z",
};

let ctx;
function Probe() {
  ctx = usePromptWorkbenchContext();
  return null;
}

function renderProvider() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/dashboard/workbench/create/prompt-1"]}>
        <Routes>
          <Route
            path="/dashboard/workbench/create/:id"
            element={
              <WorkbenchProvider>
                <Probe />
              </WorkbenchProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const texts = (column) =>
  column.prompts.map((m) => m.content.map((c) => c.text).join(""));

describe("WorkbenchProvider addToCompare", () => {
  beforeEach(() => {
    ctx = null;
    axios.get.mockReset();
    axios.post.mockReset();
    axios.get.mockImplementation((url) =>
      String(url).endsWith("/getPrompt")
        ? Promise.resolve({ data: LATEST })
        : new Promise(() => {}),
    );
    axios.post.mockImplementation((url) =>
      String(url).endsWith("/addDraftInPrompt")
        ? Promise.resolve({ data: { result: [NEW_DRAFT] } })
        : new Promise(() => {}),
    );
  });

  it("starts the new column as a draft copy of the open version", async () => {
    renderProvider();
    await waitFor(() => {
      expect(ctx.selectedVersions[0]?.version).toBe("V2");
      expect(ctx.prompts[0]?.prompts).toHaveLength(2);
      expect(ctx.modelConfig[0]?.model).toBe("gpt-4o-mini");
    });
    const base = {
      version: ctx.selectedVersions[0],
      prompts: ctx.prompts[0],
      config: ctx.modelConfig[0],
    };

    await act(async () => {
      await ctx.addToCompare();
    });

    expect(ctx.selectedVersions).toHaveLength(2);
    // The copy shows the open version's messages and model settings ...
    expect(texts(ctx.prompts[1])).toEqual([
      "You plan trips.",
      "Plan a day in {{city}}.",
    ]);
    expect(ctx.prompts[1].prompts.map((m) => m.role)).toEqual([
      "system",
      "user",
    ]);
    expect(ctx.modelConfig[1].model).toBe("gpt-4o-mini");
    expect(ctx.modelConfig[1].temperature).toBe(0.3);
    expect(ctx.modelConfig[1].maxTokens).toBe(512);

    // ... as separate objects, so editing the copy leaves the original alone.
    const baseIds = base.prompts.prompts.map((m) => m.id);
    ctx.prompts[1].prompts.forEach((m, i) => {
      expect(baseIds).not.toContain(m.id);
      expect(m.content).not.toBe(base.prompts.prompts[i].content);
    });
    expect(ctx.prompts[1].id).not.toBe(base.prompts.id);
    expect(ctx.modelConfig[1].id).not.toBe(base.config.id);

    // It is a new draft version, never the original's identity.
    expect(ctx.selectedVersions[1]).toMatchObject({
      version: "V3",
      isDraft: true,
      isDefault: false,
    });
    expect(ctx.selectedVersions[1].id).not.toBe(base.version.id);
    expect(ctx.selectedVersions[0]).toMatchObject({
      version: "V2",
      isDraft: false,
    });
    expect(texts(ctx.prompts[0])).toEqual([
      "You plan trips.",
      "Plan a day in {{city}}.",
    ]);

    // The draft is created from the copy and does not name the original.
    const draftCalls = axios.post.mock.calls.filter(([url]) =>
      String(url).endsWith("/addDraftInPrompt"),
    );
    expect(draftCalls).toHaveLength(1);
    const [newPrompt] = draftCalls[0][1].new_prompts;
    expect(newPrompt).not.toHaveProperty("version");
    expect(
      newPrompt.prompt_config[0].messages.map((m) => m.content[0].text),
    ).toEqual(["You plan trips.", "Plan a day in {{city}}."]);
    expect(newPrompt.prompt_config[0].configuration).toMatchObject({
      model: "gpt-4o-mini",
      temperature: 0.3,
      max_tokens: 512,
    });
    axios.post.mock.calls.forEach(([, body]) => {
      expect(body?.version).not.toBe("V2");
      expect(body?.version_name).not.toBe("V2");
    });
  });
});
