import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios, { endpoints } from "src/utils/axios";
import { usePromptWorkbenchContext } from "../WorkbenchContext";
import WorkbenchProvider from "../WorkbenchProvider";

// cu-fa-pr-01: editing a committed prompt version must make exactly one new
// draft, which holds the edit, so committing it gives v2 (never an empty v2
// draft beside a v3). Compare opens only the versions picked in History: an
// empty draft that is open never joins on its own.

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

const PARIS = "What is the weather in {{ city }} today?";
const TOKYO = "What is the weather in {{ city }} this week?";

const messagesWith = (userText) => [
  { role: "system", content: [{ type: "text", text: "" }] },
  { role: "user", content: [{ type: "text", text: userText }] },
];

const CONFIGURATION = {
  model: "gpt-4o",
  output_format: "string",
  model_detail: { model_name: "gpt-4o", providers: "openai" },
};

const userTextOf = (messages) =>
  (messages || [])
    .filter((m) => m.role === "user")
    .flatMap((m) => m.content || [])
    .map((c) => c.text || "")
    .join("")
    .trim();

// A small stand-in for the prompt version rows behind the endpoints the
// workbench calls, with the server's rules: add-new-draft numbers each new
// draft after the last version; a save (run_template, is_run false) writes a
// draft in place, and a save that names no draft writes the latest draft or
// makes a new one; commit finalizes the named version.
let backend;
function resetBackend(rows) {
  backend = { rows: rows.map((row) => ({ ...row })), draftBodies: [] };
}

function handlePost(url, body) {
  const path = String(url);
  if (path.endsWith("/addDraftInPrompt")) {
    backend.draftBodies.push(body);
    const created = (body?.new_prompts || []).map((prompt) => {
      const row = {
        template_version: `v${backend.rows.length + 1}`,
        is_draft: true,
        is_default: false,
        messages: prompt?.prompt_config?.[0]?.messages,
        variable_names: prompt?.variable_names,
      };
      backend.rows.push(row);
      return row;
    });
    return Promise.resolve({
      data: {
        result: created.map((row) => ({
          template_version: row.template_version,
          is_draft: true,
          is_default: false,
          updated_at: "2026-11-10T12:00:00Z",
        })),
      },
    });
  }
  if (path.endsWith("/runTemplatePrompt") && !body?.is_run) {
    const named = backend.rows.find(
      (row) => row.template_version === body?.version,
    );
    const latest = backend.rows[backend.rows.length - 1];
    let target = named?.is_draft ? named : null;
    if (!target && latest?.is_draft) target = latest;
    if (!target) {
      target = {
        template_version: `v${backend.rows.length + 1}`,
        is_draft: true,
        is_default: false,
      };
      backend.rows.push(target);
    }
    target.messages = body?.prompt_config?.[0]?.messages;
    target.variable_names = body?.variable_names;
    return Promise.resolve({
      data: { result: { version: target.template_version } },
    });
  }
  if (path.endsWith("/commitSavePrompt")) {
    const row = backend.rows.find(
      (r) => r.template_version === body?.version_name,
    );
    if (!row) return Promise.reject(new Error("Prompt Version Not Found"));
    row.is_draft = false;
    if (body?.set_default) {
      backend.rows.forEach((r) => {
        r.is_default = r === row;
      });
    }
    return Promise.resolve({ data: { result: "committed" } });
  }
  return new Promise(() => {});
}

let latestVersion;
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

const promptTexts = (column) =>
  column.prompts.map((m) => m.content.map((c) => c.text).join(""));

// The editor's own change: PromptSection replaces the message's content.
const editUserMessage = (text) => (prev) => {
  const next = [...prev];
  next[1].content = [{ type: "text", text }];
  return next;
};

const draftPostBodies = () =>
  axios.post.mock.calls
    .filter(([url]) => String(url).endsWith("/addDraftInPrompt"))
    .map(([, body]) => body);

describe("WorkbenchProvider: editing a committed version", () => {
  beforeEach(() => {
    ctx = null;
    resetBackend([
      {
        template_version: "v1",
        is_draft: false,
        is_default: true,
        messages: messagesWith(PARIS),
        variable_names: { city: ["Paris"] },
      },
    ]);
    latestVersion = {
      name: "Weather",
      version: "v1",
      is_draft: false,
      is_default: true,
      last_saved: "2026-11-10T12:00:00Z",
      variable_names: { city: ["Paris"] },
      prompt_config: [
        { messages: messagesWith(PARIS), configuration: CONFIGURATION },
      ],
    };
    axios.get.mockReset();
    axios.post.mockReset();
    axios.get.mockImplementation((url) =>
      String(url).endsWith("/getPrompt")
        ? Promise.resolve({ data: latestVersion })
        : new Promise(() => {}),
    );
    axios.post.mockImplementation(handlePost);
  });

  it("makes one draft that holds the edit; committing it gives v1 and v2, no empty draft", async () => {
    renderProvider();
    await waitFor(() => {
      expect(ctx.selectedVersions[0]).toMatchObject({
        version: "v1",
        isDraft: false,
      });
      expect(promptTexts(ctx.prompts[0])).toEqual(["", PARIS]);
    });

    // Typing over a selection in the editor (Quill 2) is two changes in one
    // event, a delete and then an insert, both before React re-renders: the
    // same render's setter runs twice.
    const { setPromptsByIndex } = ctx;
    act(() => {
      setPromptsByIndex(0, editUserMessage(""));
      setPromptsByIndex(0, editUserMessage(TOKYO));
    });

    await waitFor(
      () => {
        expect(
          backend.rows.map((row) => [row.template_version, row.is_draft]),
        ).toEqual([
          ["v1", false],
          ["v2", true],
        ]);
        expect(ctx.selectedVersions[0]).toMatchObject({
          version: "v2",
          isDraft: true,
        });
      },
      { timeout: 3000 },
    );
    // Give any later save the editor schedules (0.5 s after a change), or a
    // second draft, the time to arrive: none may.
    await act(() => new Promise((resolve) => setTimeout(resolve, 900)));

    // Exactly one new draft, made from the edit, never from an empty editor.
    expect(draftPostBodies()).toHaveLength(1);
    draftPostBodies()
      .flatMap((body) => body.new_prompts)
      .forEach((prompt) => {
        expect(userTextOf(prompt.prompt_config?.[0]?.messages)).not.toBe("");
      });
    expect(ctx.selectedVersions[0]).toMatchObject({
      version: "v2",
      isDraft: true,
    });
    expect(promptTexts(ctx.prompts[0])).toEqual(["", TOKYO]);
    expect(
      backend.rows.map((row) => [row.template_version, row.is_draft]),
    ).toEqual([
      ["v1", false],
      ["v2", true],
    ]);
    expect(userTextOf(backend.rows[1].messages)).toBe(TOKYO);

    // Commit sends the open version (SaveAndCommit's version_name).
    await act(async () => {
      await axios.post(
        endpoints.develop.runPrompt.commitSavePrompt("prompt-1"),
        {
          message: "second version: Tokyo",
          set_default: true,
          is_draft: false,
          version_name: ctx.selectedVersions[0].version,
        },
      );
    });

    expect(
      backend.rows.map((row) => [row.template_version, row.is_draft]),
    ).toEqual([
      ["v1", false],
      ["v2", false],
    ]);
    expect(userTextOf(backend.rows[0].messages)).toBe(PARIS);
    expect(userTextOf(backend.rows[1].messages)).toBe(TOKYO);
    backend.rows.forEach((row) => {
      expect(userTextOf(row.messages)).not.toBe("");
    });
  });

  it("keeps the edit's later keystrokes in that one draft", async () => {
    renderProvider();
    await waitFor(() => {
      expect(ctx.selectedVersions[0]?.version).toBe("v1");
      expect(ctx.prompts[0]?.prompts).toHaveLength(2);
    });

    act(() => {
      ctx.setPromptsByIndex(0, editUserMessage(TOKYO));
    });
    act(() => {
      ctx.setPromptsByIndex(0, editUserMessage(`${TOKYO} Thanks`));
    });

    // The later keystroke is saved into the draft 0.5 s after it is made.
    await waitFor(
      () => {
        expect(userTextOf(backend.rows[1]?.messages)).toBe(`${TOKYO} Thanks`);
      },
      { timeout: 3000 },
    );

    expect(draftPostBodies()).toHaveLength(1);
    expect(ctx.selectedVersions[0]).toMatchObject({
      version: "v2",
      isDraft: true,
    });
    expect(
      backend.rows.map((row) => [row.template_version, row.is_draft]),
    ).toEqual([
      ["v1", false],
      ["v2", true],
    ]);
  });
});

const historyRow = (version, userText, extra = {}) => ({
  id: `id-${version}`,
  template_version: version,
  is_draft: false,
  is_default: false,
  updated_at: "2026-11-10T12:00:00Z",
  labels: [],
  variable_names: { city: [version === "v1" ? "Paris" : "Tokyo"] },
  output: [],
  prompt_config_snapshot: {
    messages: messagesWith(userText),
    configuration: CONFIGURATION,
    placeholders: [],
  },
  ...extra,
});

describe("WorkbenchProvider: Compare from History", () => {
  beforeEach(() => {
    ctx = null;
    resetBackend([]);
    axios.get.mockReset();
    axios.post.mockReset();
    axios.post.mockImplementation(handlePost);
  });

  it("opens only the two picked versions when the open version is an empty draft", async () => {
    // A prompt that already has a stray empty draft (v2) open in the editor.
    latestVersion = {
      name: "Weather",
      version: "v2",
      is_draft: true,
      is_default: false,
      last_saved: "2026-11-10T12:00:00Z",
      variable_names: {},
      prompt_config: [
        { messages: messagesWith(""), configuration: CONFIGURATION },
      ],
    };
    axios.get.mockImplementation((url) =>
      String(url).endsWith("/getPrompt")
        ? Promise.resolve({ data: latestVersion })
        : new Promise(() => {}),
    );
    renderProvider();
    await waitFor(() => {
      expect(ctx.selectedVersions[0]).toMatchObject({
        version: "v2",
        isDraft: true,
      });
      expect(ctx.prompts[0]?.prompts).toHaveLength(2);
    });

    act(() => {
      ctx.applyCompare([
        historyRow("v3", TOKYO, { is_default: true }),
        historyRow("v1", PARIS),
      ]);
    });

    await waitFor(() => {
      expect(ctx.selectedVersions.map((v) => v.version)).toEqual(["v3", "v1"]);
    });
    expect(ctx.prompts).toHaveLength(2);
    expect(promptTexts(ctx.prompts[0])).toEqual(["", TOKYO]);
    expect(promptTexts(ctx.prompts[1])).toEqual(["", PARIS]);
    expect(ctx.modelConfig).toHaveLength(2);
    // Nothing is deleted or written: the empty draft stays as it is.
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("still keeps an open version that holds content as the first pane", async () => {
    latestVersion = {
      name: "Weather",
      version: "v3",
      is_draft: false,
      is_default: true,
      last_saved: "2026-11-10T12:00:00Z",
      variable_names: { city: ["Tokyo"] },
      prompt_config: [
        { messages: messagesWith(TOKYO), configuration: CONFIGURATION },
      ],
    };
    axios.get.mockImplementation((url) =>
      String(url).endsWith("/getPrompt")
        ? Promise.resolve({ data: latestVersion })
        : new Promise(() => {}),
    );
    renderProvider();
    await waitFor(() => {
      expect(ctx.selectedVersions[0]?.version).toBe("v3");
      expect(ctx.prompts[0]?.prompts).toHaveLength(2);
    });

    act(() => {
      ctx.applyCompare([historyRow("v1", PARIS)]);
    });

    await waitFor(() => {
      expect(ctx.selectedVersions.map((v) => v.version)).toEqual(["v3", "v1"]);
    });
    expect(promptTexts(ctx.prompts[0])).toEqual(["", TOKYO]);
    expect(promptTexts(ctx.prompts[1])).toEqual(["", PARIS]);
  });
});
