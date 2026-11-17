import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { usePromptWorkbenchContext } from "../WorkbenchContext";
import WorkbenchProvider from "../WorkbenchProvider";
import PromptActions from "../promptActions/PromptActions";

// Prompts > a Jinja prompt whose User message reads {{ name.__class__ }},
// with name = Ada. The stream refuses it before the model is called and
// shows one plain line: the output cell (an error result) and a toast. When
// the stream cannot start, the page runs the prompt over HTTP and polls
// get-run-status, which now returns the same line as the row's output. The
// page showed it as an ordinary answer, with no error and no toast. It now
// shows it as the stream does.
//
// The page is read as a browser runs it (see WorkbenchProvider.run-anyway):
// no window.event outside an event, and BrowserRouter as in the app.

const { endpointPath, toast } = vi.hoisted(() => {
  const endpointPath = (path) =>
    new Proxy(() => path, {
      get: (_, key) =>
        typeof key === "symbol" ? undefined : endpointPath(`${path}/${key}`),
    });
  return { endpointPath, toast: vi.fn() };
});

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: endpointPath(""),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: "Owner" },
    role: "Owner",
  }),
}));
// The index re-exports all of notistack: keep the rest real. One toast
// function, so what the page told the user can be read.
vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: toast,
  useSnackbar: () => ({ enqueueSnackbar: toast }),
}));
vi.mock("src/sections/workbench/createPrompt/hooks/usePromptStreamUrl", () => ({
  usePromptStreamUrl: () => async () => "ws://prompt-stream.test/",
}));
vi.mock("../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({
    versions: [{ id: "row-v1", template_version: "v1", is_draft: true }],
    refetch: vi.fn(),
  }),
}));

const USER_TEXT =
  "Reply with exactly this text and nothing else: TYPE {{ name.__class__ }} END";
// model_hub/utils/prompt_rendering.py _refused_message("__class__")
const REFUSED_LINE =
  "Not run: the prompt reads __class__, which a template is not allowed to read. Remove it from the prompt and run again.";
const SESSION = "session-1";

let sockets;
class FakeSocket {
  constructor(url) {
    this.url = url;
    this.sent = [];
    this.closed = false;
    sockets.push(this);
  }

  send(data) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.closed = true;
  }
}

const latestDraft = () => ({
  name: "Untitled-1",
  version: "v1",
  is_draft: true,
  is_default: true,
  last_saved: "2026-11-11T15:10:00Z",
  variable_names: { name: ["Ada"] },
  prompt_config: [
    {
      messages: [
        {
          role: "system",
          content: [{ type: "text", text: "You are a careful assistant." }],
        },
        { role: "user", content: [{ type: "text", text: USER_TEXT }] },
      ],
      configuration: {
        model: "gpt-4o",
        output_format: "string",
        template_format: "jinja",
        model_detail: { model_name: "gpt-4o", providers: "openai" },
      },
    },
  ],
});

// What get-run-status returns once the HTTP run has stored its row.
const runStatus = (output) => ({
  status: "completed",
  error_message: null,
  executions_result: {
    template_version: "v1",
    output: [output],
    metadata: [null],
    prompt_config_snapshot: latestDraft().prompt_config[0],
  },
});

let ctx;
function Probe() {
  ctx = usePromptWorkbenchContext();
  return null;
}

function renderWorkbench() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <Routes>
          <Route
            path="/dashboard/workbench/create/:id"
            element={
              <WorkbenchProvider>
                <PromptActions />
                <Probe />
              </WorkbenchProvider>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>,
  );
}

const frame = (fields) => ({
  type: "run_prompt",
  template_id: "prompt-1",
  version: "v1",
  session_uuid: SESSION,
  ...fields,
});

const send = (socket, data) =>
  act(() => {
    socket.onmessage({ data: JSON.stringify(data) });
  });

// Opens the prompt, presses Run Prompt and returns the run's stream.
async function startRun() {
  renderWorkbench();
  await waitFor(() => {
    expect(ctx.selectedVersions[0]?.version).toBe("v1");
    expect(ctx.modelConfig[0]?.model).toBe("gpt-4o");
    expect(ctx.prompts[0]?.prompts).toHaveLength(2);
  });
  fireEvent.click(screen.getByTestId("run-prompt"));
  expect(screen.queryByTestId("run-empty-variables")).toBeNull();
  await waitFor(() => expect(ctx.promptGeneratingStatus[0]).toBe(true));
  await waitFor(() => expect(sockets).toHaveLength(1));
  return sockets[0];
}

// The stream cannot start: it errors before the server sends anything, and
// the page runs the prompt over HTTP.
async function runOverHttpFallback() {
  const socket = await startRun();
  act(() => {
    socket.onerror(new Event("error"));
  });
  await waitFor(() => expect(ctx.promptGeneratingStatus[0]).toBe(false), {
    timeout: 3000,
  });
  // The HTTP run was started for this version, then its status read.
  const runs = axios.post.mock.calls.filter(
    ([path, body]) =>
      String(path).endsWith("/runTemplatePrompt") && body?.is_run === "prompt",
  );
  expect(runs).toHaveLength(1);
  expect(runs[0][1]).toMatchObject({ version: "v1", is_run: "prompt" });
  expect(
    axios.get.mock.calls.some(([path]) => String(path).endsWith("/getStatus")),
  ).toBe(true);
}

const errorToasts = () =>
  toast.mock.calls.filter(([, options]) => options?.variant === "error");

describe("Prompts: a refused Jinja part over the HTTP fallback", () => {
  let eventDescriptor;
  let statusOutput;

  beforeEach(() => {
    ctx = null;
    sockets = [];
    statusOutput = REFUSED_LINE;
    toast.mockReset();
    window.history.replaceState(
      null,
      "",
      "/dashboard/workbench/create/prompt-1",
    );
    eventDescriptor = Object.getOwnPropertyDescriptor(window, "event");
    Object.defineProperty(window, "event", {
      configurable: true,
      get: () => undefined,
    });
    vi.stubGlobal("WebSocket", FakeSocket);
    axios.get.mockReset();
    axios.post.mockReset();
    axios.post.mockImplementation((path) =>
      String(path).endsWith("/runTemplatePrompt")
        ? Promise.resolve({ data: { status: true, result: {} } })
        : new Promise(() => {}),
    );
    axios.get.mockImplementation((path) => {
      if (String(path).endsWith("/getPrompt"))
        return Promise.resolve({ data: latestDraft() });
      if (String(path).endsWith("/getStatus"))
        return Promise.resolve({
          data: { status: true, result: runStatus(statusOutput) },
        });
      return new Promise(() => {});
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    if (eventDescriptor)
      Object.defineProperty(window, "event", eventDescriptor);
    else delete window.event;
  });

  it("shows the refusal line as the stream does: an error result and the same toast", async () => {
    await runOverHttpFallback();

    expect(ctx.results[0].output).toHaveLength(1);
    expect(ctx.results[0].output[0].text).toBe(REFUSED_LINE);
    expect(
      ctx.results[0].output[0].status,
      "the HTTP run's refusal shows as an ordinary answer, not as the stream's error result",
    ).toBe("error");
    expect(
      errorToasts().map(([message]) => message),
      "the stream toasts the refusal line; the HTTP fallback says nothing",
    ).toEqual([REFUSED_LINE]);
  });

  it("guard, passes on BASE too: the stream shows the refusal line as an error result and a toast", async () => {
    const socket = await startRun();
    act(() => {
      socket.onopen();
    });

    // The frames run_template_async sends for a refused one-row run.
    send(
      socket,
      frame({
        streaming_status: "started",
        status: "process_started",
        total_iterations: 1,
        output_format: "string",
      }),
    );
    send(
      socket,
      frame({
        streaming_status: "error",
        error: REFUSED_LINE,
        result_index: 0,
        num_results: 1,
        output_format: "string",
      }),
    );
    send(socket, frame({ streaming_status: "all_completed" }));

    await waitFor(() => expect(ctx.promptGeneratingStatus[0]).toBe(false), {
      timeout: 3000,
    });
    expect(ctx.results[0].output).toHaveLength(1);
    expect(ctx.results[0].output[0].text).toBe(REFUSED_LINE);
    expect(ctx.results[0].output[0].status).toBe("error");
    expect(errorToasts().map(([message]) => message)).toEqual([REFUSED_LINE]);
    expect(
      axios.get.mock.calls.some(([path]) => String(path).endsWith("/getStatus")),
    ).toBe(false);
  });

  it("guard, passes on BASE too: an answer over the HTTP fallback shows as an answer, with no error", async () => {
    statusOutput = "TYPE END";

    await runOverHttpFallback();

    expect(ctx.results[0].output).toHaveLength(1);
    expect(ctx.results[0].output[0].text).toBe("TYPE END");
    expect(ctx.results[0].output[0].status).not.toBe("error");
    expect(errorToasts()).toEqual([]);
  });
});
