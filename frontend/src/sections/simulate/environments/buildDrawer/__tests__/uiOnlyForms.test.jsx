import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  render as baseRender,
  fireEvent,
  screen,
  within,
} from "src/utils/test-utils";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: { items: [], total: 0 },
    isLoading: false,
  }),
}));

vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return { ...actual, enqueueSnackbar: vi.fn() };
});

const { preflightHarnessJob, createHarnessJob, storeHarnessSecretValues } =
  await import("src/api/harness/harness");
const { enqueueSnackbar } = await import("notistack");
const { default: BuildEnvironmentDrawer } = await import(
  "../BuildEnvironmentDrawer"
);

const render = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  baseRender(
    <QueryClientProvider client={client}>
      <BuildEnvironmentDrawer open onClose={vi.fn()} />
    </QueryClientProvider>,
  );
};

const TAB = {
  code: /^Code/,
  hosted: /^Hosted platforms/,
  other: /^Other ways/,
};
/** Opens a way's setup from its catalog row. */
const open = (tab, name) => {
  fireEvent.click(screen.getByRole("tab", { name: TAB[tab] }));
  fireEvent.click(screen.getByRole("button", { name: `Connect ${name}` }));
};
// Back slides the setup out; it leaves the page once the slide ends.
const back = () => {
  fireEvent.click(
    screen.getByRole("button", { name: "Back to build environment" }),
  );
  act(() => {
    vi.advanceTimersByTime(400);
  });
};
const wait = (ms) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });
const type = (name, value) =>
  fireEvent.change(screen.getByRole("textbox", { name }), {
    target: { value },
  });
const typeSecret = (placeholder, value) =>
  fireEvent.change(screen.getAllByPlaceholderText(placeholder)[0], {
    target: { value },
  });
const choose = (selectName, option) => {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: selectName }));
  fireEvent.click(
    within(screen.getByRole("listbox")).getByRole("option", { name: option }),
  );
};
const notice = () => screen.queryByText(/This flow isn't wired to the API yet/);
const footer = (text) => screen.getByText(text);

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  preflightHarnessJob.mockReset();
  createHarnessJob.mockReset();
  storeHarnessSecretValues.mockReset();
  enqueueSnackbar.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("every UI-only way opens its design in the drawer", () => {
  it.each([
    ["code", "LangGraph", "Where it runs"],
    ["code", "GitLab", "Connect GitLab to pick the agent"],
    ["code", "Bitbucket", "Connect Bitbucket to pick the agent"],
    ["code", "OpenAI, Anthropic or Gemini calls", "Git host"],
    ["hosted", "ElevenLabs", "ElevenLabs API key"],
    ["hosted", "Salesforce Agentforce", "Connected app credentials"],
    ["other", "Agent endpoint", "Speaks"],
    ["other", "Sierra", "How we reach it"],
  ])("%s · %s", (tab, name, marker) => {
    render();
    open(tab, name);
    expect(notice()).toBeInTheDocument();
    expect(screen.getAllByText(marker)[0]).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Connect and build environment" }),
    ).toBeDisabled();
  });
});

describe("framework through our SDK", () => {
  it("shows each framework's own install and wrap", () => {
    render();
    open("code", "LangGraph");
    expect(
      screen.getByText('pip install "futureagi[langgraph]"'),
    ).toBeInTheDocument();
    expect(screen.getByText(/langgraph as fagi/)).toBeInTheDocument();
    expect(screen.getByText("Wrap your compiled graph")).toBeInTheDocument();
    expect(
      screen.getByText("Prompts, tools and each step's model"),
    ).toBeInTheDocument();
    back();

    open("code", "Mastra");
    expect(
      screen.getByText("npm install @futureagi/adapters"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/@futureagi\/adapters\/mastra/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/langgraph as fagi/)).toBeNull();
    back();

    open("code", "LiveKit Agents");
    expect(
      screen.getByText('pip install "futureagi[livekit]"'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/python serve\.py\s+# audio over WebRTC/),
    ).toBeInTheDocument();
  });

  it("prefills the adapter endpoint and checks in a sample local agent", () => {
    render();
    open("code", "CrewAI");
    expect(
      screen.getByRole("textbox", { name: "Adapter endpoint" }),
    ).toHaveValue("http://localhost:8000/fagi");
    fireEvent.click(screen.getByRole("radio", { name: "Run on your machine" }));
    expect(
      screen.getByText(/fagi\.init\(SupportCrew\(\)\.crew\(\)\)/),
    ).toBeInTheDocument();
    expect(footer("Waiting for your agent to check in")).toBeInTheDocument();
    wait(2500);
    expect(screen.getByText(/Connected · commit/)).toBeInTheDocument();
    expect(screen.getAllByLabelText("Sample data").length).toBeGreaterThan(0);
    expect(footer("Run preflight first")).toBeInTheDocument();
  });

  it("points at the repository instead, in the drawer, and comes back", () => {
    render();
    open("code", "Pydantic AI");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Point us at the repository instead",
      }),
    );
    expect(screen.getByText("Git host")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "GitLab" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Back to our SDK/ }));
    expect(screen.getByText("Where it runs")).toBeInTheDocument();
  });
});

describe("GitLab and Bitbucket", () => {
  const connectGitLab = () => {
    open("code", "GitLab");
    expect(screen.getByText("read_repository")).toBeInTheDocument();
    const connect = screen.getByRole("button", { name: "Connect GitLab" });
    expect(connect).toBeDisabled();
    typeSecret("Paste the token", "glpat-1");
    fireEvent.click(connect);
    wait(700);
  };

  it("connects, lists sample repositories, scans and finds the agents", () => {
    render();
    connectGitLab();
    expect(screen.getByText(/from GitLab \(acme-inc\)/)).toBeInTheDocument();
    // The select's placeholder, and the footer's hint.
    expect(screen.getAllByText("Choose a repository")).toHaveLength(2);
    choose("Repository", /acme-inc\/platform\/support-graph/);
    expect(screen.getByText(/for agents…/)).toBeInTheDocument();
    wait(1200);
    expect(
      screen.getByText("We found 16 agents. Which one do you mean?"),
    ).toBeInTheDocument();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Search the agents found" }),
      { target: { value: "dspy" } },
    );
    expect(screen.getByText("1 of 16")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("radio", { name: "Ticket classifier (DSPy)" }),
    );
    expect(
      screen.getByText(/DSPy's instructions are rewritten/),
    ).toBeInTheDocument();
    expect(screen.getByText("Environment values")).toBeInTheDocument();
    expect(screen.getByText("OPENAI_API_KEY")).toBeInTheDocument();
    // A secret takes a masked key; a plain setting a visible value.
    expect(screen.getByLabelText("OPENAI_API_KEY")).toHaveAttribute(
      "type",
      "password",
    );
    expect(
      screen.getByRole("textbox", { name: "LOG_LEVEL" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Connect another group" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/A fix merged there counts as released/),
    ).toBeInTheDocument();
    expect(footer("Run preflight first")).toBeInTheDocument();
  });

  it("says a read timed out, retries, then asks for the entry file", () => {
    render();
    connectGitLab();
    choose("Repository", /acme-inc\/infra-terraform/);
    wait(2600);
    expect(screen.getByText(/timed out/)).toBeInTheDocument();
    expect(footer("Retry reading the repository")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    wait(1200);
    expect(screen.getByText(/No agent found in/)).toBeInTheDocument();
    expect(footer("Name the entry file")).toBeInTheDocument();
  });

  it("takes a typed repository and folder instead", () => {
    render();
    open("code", "Bitbucket");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Enter a Bitbucket repository URL instead",
      }),
    );
    type("Bitbucket repository", "acme/claims");
    expect(footer("Add the agent folder")).toBeInTheDocument();
    type("Agent folder", "agents/claims");
    expect(
      screen.getByText("Where the agent starts (when it uses no framework)"),
    ).toBeInTheDocument();
    expect(footer("Run preflight first")).toBeInTheDocument();
  });
});

describe("OpenAI, Anthropic or Gemini calls", () => {
  it("picks the git host, then asks where the picked agent starts", () => {
    render();
    open("code", "OpenAI, Anthropic or Gemini calls");
    for (const host of ["GitHub", "GitLab", "Bitbucket"]) {
      expect(screen.getByRole("button", { name: host })).toBeInTheDocument();
    }
    choose("Repository", /acme-inc\/faq-assistant/);
    wait(1200);
    fireEvent.click(
      screen.getByRole("radio", { name: "Help-centre FAQ assistant" }),
    );
    expect(
      screen.getByRole("textbox", { name: "Where the agent starts" }),
    ).toHaveValue("app/chat.py:handle_message");
  });

  it("says when a repository couldn't be read", () => {
    render();
    open("code", "OpenAI, Anthropic or Gemini calls");
    choose("Repository", /acme-labs\/notebooks/);
    wait(1200);
    expect(screen.getByText(/Couldn't read/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("hosted platforms without a connector", () => {
  it("lists sample agents once a key is in, and sets up the one picked", () => {
    render();
    open("hosted", "ElevenLabs");
    expect(footer("Add the key")).toBeInTheDocument();
    typeSecret("Paste the key", "xi-key");
    expect(
      screen.getByText("Listing agents on ElevenLabs…"),
    ).toBeInTheDocument();
    wait(700);
    expect(
      screen.getByText(
        "2 agents on this ElevenLabs account. Which one do you mean?",
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Sample data")).toBeInTheDocument();
    expect(
      screen.getByText(/Every agent this key can see is listed/),
    ).toBeInTheDocument();
    expect(footer("Pick the agent")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Concierge" }));
    expect(
      screen.getByText("Read from ElevenLabs: Workflow · 3 nodes."),
    ).toBeInTheDocument();
    expect(screen.getByText("Web simulation (WebRTC)")).toBeInTheDocument();
    expect(screen.getByText("concierge")).toBeInTheDocument();
    expect(screen.getByText(/Changes ship as a branch/)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Enable observability" }),
    );
    expect(
      screen.getByText(/Off. Its production calls stay out of Observe/),
    ).toBeInTheDocument();
  });

  it("offers the chat channels on chat, and only one type where that's all there is", () => {
    render();
    open("hosted", "ElevenLabs");
    fireEvent.click(screen.getByRole("button", { name: /Chat/ }));
    expect(
      screen.getByRole("button", { name: /WhatsApp/ }),
    ).toBeInTheDocument();
    back();
    open("hosted", "Telnyx");
    expect(screen.queryByText(/Voice or chat/)).toBeNull();
  });
});

describe("closed platforms", () => {
  it("switches the fields with the way we reach it", () => {
    render();
    open("other", "Decagon");
    // The list stays mounted, hidden, under the setup.
    expect(
      screen.getByText("Phone number", {
        ignore: '[aria-hidden="true"] *, script, style',
      }),
    ).toBeInTheDocument();
    expect(footer("Add the number to call")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Chat channel/ }));
    expect(
      screen.getByRole("textbox", { name: "Chat channel address" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Conversation API/ }));
    expect(
      screen.getByRole("textbox", { name: "Conversation API address" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Where the reply sits" }),
    ).toHaveValue("$.messages[-1].text");
    back();
    open("other", "Intercom Fin");
    expect(screen.queryByRole("button", { name: /Phone line/ })).toBeNull();
  });
});

describe("agent endpoint", () => {
  it("says what's wrong with the URL and reply path, then asks for consent", () => {
    render();
    open("other", "Agent endpoint");
    expect(footer("Add the endpoint")).toBeInTheDocument();
    type("Endpoint URL", "ftp://agent");
    expect(
      screen.getByText("An HTTP endpoint starts with https:// or http://"),
    ).toBeInTheDocument();
    expect(footer("Use a https:// address")).toBeInTheDocument();
    type("Endpoint URL", "https://api.acme.com/agent");
    expect(footer("Say where the reply sits")).toBeInTheDocument();
    type("Where the reply sits", "output");
    expect(
      screen.getByText(
        "A JSON path, like $.output.text or $.messages[0].content",
      ),
    ).toBeInTheDocument();
    type("Where the reply sits", "$.output.text");
    expect(screen.getByText("How we'll test it")).toBeInTheDocument();
    expect(
      footer("Acknowledge that simulations drive the real agent"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByText("Secondary webhook")).toBeInTheDocument();
    expect(screen.getByText(/hooks\.futureagi\.com/)).toBeInTheDocument();
    expect(footer("Run preflight first")).toBeInTheDocument();
  });
});

describe("preflight and building", () => {
  it("shows sample checks, then only says building isn't wired", () => {
    render();
    open("other", "Agent endpoint");
    type("Endpoint URL", "https://api.acme.com/agent");
    type("Where the reply sits", "$.output.text");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    expect(
      screen.getByText("Checking the source and credentials…"),
    ).toBeInTheDocument();
    wait(900);
    expect(screen.getByText("Ready to build")).toBeInTheDocument();
    expect(screen.getByText("Answered a test turn")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Connect and build environment" }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      expect.stringMatching(/^UI only/),
      expect.anything(),
    );
    expect(preflightHarnessJob).not.toHaveBeenCalled();
    expect(createHarnessJob).not.toHaveBeenCalled();
    expect(storeHarnessSecretValues).not.toHaveBeenCalled();
  });

  it("drops a finished preflight when a field changes", () => {
    render();
    open("other", "Sierra");
    fireEvent.click(screen.getByRole("button", { name: /Chat channel/ }));
    type("Chat channel address", "https://acme.sierra.chat/agent");
    type("Its instructions", "You handle returns.");
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    wait(900);
    expect(screen.getByText("Ready to build")).toBeInTheDocument();
    type("Name", "Returns chat");
    expect(screen.queryByText("Ready to build")).toBeNull();
    expect(createHarnessJob).not.toHaveBeenCalled();
  });
});
