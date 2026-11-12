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

// Prompts > a new prompt whose User message is "Customer asks:
// {{customer_question}}", with no value for the variable. Run Prompt opens the
// "Not run yet" box; Run anyway saved the draft and the server ran it, but the
// output stayed on "Creating your final output..." with Stop Generating and
// never showed the answer: the page ignored every message of that run's
// stream, its answer and its end included. The same prompt with the value
// filled answered at once.
//
// The page is read as a browser runs it, in two ways jsdom differs:
// - Outside an event, a browser has no window.event; jsdom still holds the
//   last click there while a timer runs. React gives a state update the
//   priority of window.event, so with the click still there Run anyway's
//   updates rendered before the run started, which a browser never does.
// - BrowserRouter, as in the app: the workbench keeps its tab and version in
//   the address and merges them through window.location, which a
//   MemoryRouter leaves behind.

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
  useAuthContext: () => ({
    user: { organization_role: "Owner" },
    role: "Owner",
  }),
}));
// The index re-exports all of notistack: keep the rest real.
vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
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

const USER_TEXT = "Customer asks: {{customer_question}}";
const SESSION = "session-1";

// A stand-in for the browser's WebSocket: the test plays the server.
let sockets;
class FakeSocket {
  constructor(url) {
    this.url = url;
    this.sent = [];
    this.closed = false;
    // The calls the page made before it opened the stream.
    this.postsBefore = axios.post.mock.calls.map(([path, body]) => ({
      path: String(path),
      body,
    }));
    sockets.push(this);
  }

  send(data) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.closed = true;
  }
}

const latestDraft = (variableNames) => ({
  name: "Untitled-1",
  version: "v1",
  is_draft: true,
  is_default: true,
  last_saved: "2026-11-11T11:58:28Z",
  variable_names: variableNames,
  prompt_config: [
    {
      messages: [
        {
          role: "system",
          content: [
            {
              type: "text",
              text: "You are a friendly support agent for an online shop.",
            },
          ],
        },
        { role: "user", content: [{ type: "text", text: USER_TEXT }] },
      ],
      configuration: {
        model: "gpt-4o",
        output_format: "string",
        model_detail: { model_name: "gpt-4o", providers: "openai" },
      },
    },
  ],
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

// The frames the server sends for a one-row run (websocket_direct_manager).
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

function playRun(socket, answer) {
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
      streaming_status: "started",
      result_index: 0,
      num_results: 1,
      output_format: "string",
    }),
  );
  send(
    socket,
    frame({
      streaming_status: "running",
      chunk: answer,
      chunk_pos: 0,
      result_index: 0,
      num_results: 1,
    }),
  );
  send(
    socket,
    frame({
      streaming_status: "completed",
      result_index: 0,
      num_results: 1,
      metadata: {
        response_time: 0.02,
        cost: { total_cost: 0.00014 },
        usage: { total_tokens: 14 },
      },
      output_format: "string",
    }),
  );
  send(socket, frame({ streaming_status: "all_completed" }));
}

// Opens the prompt, presses Run Prompt (and Run anyway in the box when it
// asks), and returns the run's stream once it is open.
async function startRun({ runAnyway }) {
  renderWorkbench();
  await waitFor(() => {
    expect(ctx.selectedVersions[0]?.version).toBe("v1");
    expect(ctx.modelConfig[0]?.model).toBe("gpt-4o");
    expect(ctx.prompts[0]?.prompts).toHaveLength(2);
  });
  fireEvent.click(screen.getByTestId("run-prompt"));
  if (runAnyway) {
    await screen.findByTestId("run-empty-variables");
    fireEvent.click(screen.getByRole("button", { name: "Run anyway" }));
  } else {
    expect(screen.queryByTestId("run-empty-variables")).toBeNull();
  }
  // The run is on screen: Stop Generating, "Creating your final output...".
  await waitFor(() => expect(ctx.promptGeneratingStatus[0]).toBe(true));
  await waitFor(() => expect(sockets).toHaveLength(1));
  const socket = sockets[0];
  // The page listens to the stream before the server sends anything.
  expect(typeof socket.onmessage).toBe("function");
  act(() => {
    socket.onopen();
  });
  return socket;
}

const runSaves = (socket) =>
  socket.postsBefore.filter(({ path }) => path.endsWith("/runTemplatePrompt"));

describe("Prompts: Run anyway with {{customer_question}} empty", () => {
  let eventDescriptor;

  beforeEach(() => {
    ctx = null;
    sockets = [];
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
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    if (eventDescriptor)
      Object.defineProperty(window, "event", eventDescriptor);
    else delete window.event;
  });

  const useLatest = (variableNames) =>
    axios.get.mockImplementation((path) =>
      String(path).endsWith("/getPrompt")
        ? Promise.resolve({ data: latestDraft(variableNames) })
        : new Promise(() => {}),
    );

  it("shows the answer and leaves the generating state when the stream ends", async () => {
    useLatest({});
    const socket = await startRun({ runAnyway: true });
    const answer = 'echo: [{"type":"text","text":"Customer asks: "}]';

    playRun(socket, answer);

    await waitFor(
      () =>
        expect(
          ctx.promptGeneratingStatus[0],
          'the stream ended but the run still shows "Creating your final output..." with Stop Generating',
        ).toBe(false),
      { timeout: 3000 },
    );
    expect(ctx.results[0].output).toHaveLength(1);
    expect(ctx.results[0].output[0].text).toBe(answer);
    expect(screen.getByTestId("run-prompt")).toBeInTheDocument();
    expect(screen.queryByText("Stop Generating")).toBeNull();
  });

  it("guard, passes on BASE too: the run saves {{customer_question}} as blank first, so the box's promise holds", async () => {
    useLatest({});
    const socket = await startRun({ runAnyway: true });

    // The draft is saved with the variable defined as "" before the stream
    // opens; the server fills {{customer_question}} with it.
    const saves = runSaves(socket);
    expect(saves.length).toBeGreaterThan(0);
    expect(saves[saves.length - 1].body).toMatchObject({
      version: "v1",
      is_run: false,
      variable_names: { customer_question: [""] },
    });
    // The stream runs that saved draft.
    expect(socket.sent).toEqual([
      {
        type: "run_template",
        template_id: "prompt-1",
        version: "v1",
        is_run: "prompt",
      },
    ]);
  });

  it("guard, passes on BASE too: with the value filled there is no box and the answer shows", async () => {
    useLatest({ customer_question: ["Where is my order 1234?"] });
    const socket = await startRun({ runAnyway: false });
    const answer =
      'echo: [{"type":"text","text":"Customer asks: Where is my order 1234?"}]';

    expect(runSaves(socket).at(-1)?.body).toMatchObject({
      variable_names: { customer_question: ["Where is my order 1234?"] },
    });
    playRun(socket, answer);

    await waitFor(() => expect(ctx.promptGeneratingStatus[0]).toBe(false), {
      timeout: 3000,
    });
    expect(ctx.results[0].output[0].text).toBe(answer);
    expect(screen.getByTestId("run-prompt")).toBeInTheDocument();
  });
});
