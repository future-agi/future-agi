/* eslint-disable react/prop-types */
import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import EvalCreatePage from "../EvalCreatePage";

// Create evaluation's Code tab follows the server's custom_code_evals entry
// in /api/capabilities/: where code evaluations run it works, and where they
// don't it shows turned off with the sentence the server sends. The tab is
// never removed. A code draft opened where Code is off keeps its Code tab
// within reach, in the same off state; a new code evaluation cannot be
// started there. Looking at another tab on such a draft saves no type
// change: only a change there, or Save, does. Every request goes through
// the mocked HTTP client. Each case seeds the capabilities answer before the
// page renders (so no case passes on a read still loading), except the failed
// read, which waits for the read to fail.

// The server's sentence, as the capabilities answer carries it.
const SERVER_SENTENCE =
  "Custom code evaluations are temporarily unavailable on Future AGI Cloud.";
const CODE = "def evaluate(input, output, expected):\n    return 1";

const mocks = vi.hoisted(() => ({
  capabilities: null,
  draft: null,
  mutate: vi.fn(),
  mutateAsync: vi.fn(),
  snack: vi.fn(),
  navigate: vi.fn(),
  agentLocked: false,
  // The Agents tab's real editor, for the one case that needs its own
  // behaviour when it opens.
  realInstructionEditor: false,
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  useSnackbar: () => ({ enqueueSnackbar: mocks.snack }),
}));
vi.mock("react-router", async (original) => ({
  ...(await original()),
  useParams: () => ({ draftId: "draft-1" }),
  useNavigate: () => mocks.navigate,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
// Only the code evaluations entry is read from the answer; Agents evaluations
// are locked where a case says so, and every other feature is on.
vi.mock("src/hooks/useCapabilities", async (original) => {
  const actual = await original();
  const real = (id) => id === "custom_code_evals";
  return {
    ...actual,
    useFeatureLocked: (id) =>
      real(id)
        ? actual.useFeatureLocked(id)
        : {
            locked: id === "agentic_eval" && mocks.agentLocked,
            isLoading: false,
          },
    useFeatureDenied: (id) => (real(id) ? actual.useFeatureDenied(id) : false),
  };
});
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(async (url) => {
      if (String(url).includes("/api/capabilities/")) {
        if (mocks.capabilities instanceof Error) throw mocks.capabilities;
        return { data: mocks.capabilities };
      }
      if (url === "/models") return { data: { results: [], next: null } };
      return { data: { result: mocks.draft } };
    }),
    post: vi.fn(async () => ({ data: { status: true, result: {} } })),
  },
  endpoints: {
    develop: {
      apiKey: { status: "/provider-status" },
      modelList: "/models",
      eval: {
        getEvalDetail: (id) => `/detail/${id}`,
        createEvalTemplateV2: "/create-v2",
        evalPlayground: "/playground",
      },
    },
    knowledge: { list: "/knowledge" },
  },
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useUpdateEval: () => ({
    mutate: mocks.mutate,
    mutateAsync: mocks.mutateAsync,
  }),
}));
vi.mock("../../hooks/useCreateEval", () => ({
  useCreateEval: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ mutateAsync: vi.fn() }),
  useExecuteCompositeEval: () => ({ mutateAsync: vi.fn() }),
  useExecuteCompositeEvalAdhoc: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("../../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({}),
  useSetDefaultVersion: () => ({}),
}));
vi.mock("../../hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
// The LLM-As-A-Judge editor stands in for a person typing a prompt.
vi.mock("../LLMPromptEditor", () => ({
  default: ({ onMessagesChange }) => (
    <button
      type="button"
      onClick={() =>
        onMessagesChange([{ role: "system", content: "Is {{output}} kind?" }])
      }
    >
      Type a prompt
    </button>
  ),
}));
vi.mock("../InstructionEditor", async (original) => {
  const actual = await original();
  return {
    default: (props) =>
      mocks.realInstructionEditor ? <actual.default {...props} /> : null,
  };
});
// Only the page's own code is needed from these when the real Agents editor
// renders.
vi.mock("../ModelSelector", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock("src/components/PromptCards/PromptEditor", () => ({
  default: forwardRef(function PromptEditor() {
    return null;
  }),
}));
vi.mock("../TestPlayground", () => ({
  default: forwardRef(function TestPlayground() {
    return null;
  }),
}));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({
  default: ({ code }) => <pre data-testid="code-editor">{code}</pre>,
  PYTHON_CODE_TEMPLATE: "def evaluate(input, output, expected):\n    return 1",
}));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));

const codeEvals = (allowed) => ({
  features: {
    custom_code_evals: allowed
      ? {
          display_name: "Custom code evaluations",
          allowed: true,
          reason_code: null,
          requires_network: false,
          oss_baseline: true,
        }
      : {
          display_name: "Custom code evaluations",
          allowed: false,
          reason_code: "SERVICE_UNAVAILABLE",
          reason: SERVER_SENTENCE,
          requires_network: false,
          oss_baseline: true,
        },
  },
});

// The page in a query client that already holds the capabilities answer, as
// the API client returns it, unless `read` (the page reads it itself).
const renderPage = ({ read = false } = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  if (!read)
    client.setQueryData(CAPABILITIES_QUERY_KEY, { data: mocks.capabilities });
  render(
    <QueryClientProvider client={client}>
      <EvalCreatePage />
    </QueryClientProvider>,
  );
  return client;
};
const answerRead = (client) =>
  client.getQueryState(CAPABILITIES_QUERY_KEY)?.status;

const codeTab = () => screen.findByRole("tab", { name: /Code/ });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.agentLocked = false;
  mocks.mutateAsync.mockResolvedValue({});
  mocks.draft = {
    id: "draft-1",
    eval_type: "llm",
    output_type_normalized: "pass_fail",
    config: { model: "turing_large" },
  };
});

describe("Create evaluation's Code tab where code evaluations run", () => {
  beforeEach(() => {
    mocks.capabilities = codeEvals(true);
  });

  it("offers Code and opens its editor", async () => {
    const client = renderPage();
    expect(answerRead(client)).toBe("success");
    const tab = await codeTab();
    expect(tab).not.toHaveAttribute("aria-disabled", "true");

    await userEvent.click(tab);

    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByTestId("code-editor")).toHaveTextContent(
      "def evaluate(input, output, expected):",
    );
    expect(mocks.snack).not.toHaveBeenCalledWith(
      SERVER_SENTENCE,
      expect.anything(),
    );
  });

  it("creates a code evaluation from Create", async () => {
    renderPage();
    await userEvent.click(await codeTab());
    await userEvent.type(
      screen.getByPlaceholderText("e.g. hallucination_detector"),
      "row_length_check",
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "Save Evaluation" }),
    );

    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledTimes(1));
    expect(mocks.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "row_length_check",
        eval_type: "code",
        code: CODE,
        publish: true,
      }),
    );
    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/evaluations/draft-1",
    );
  });

  it("reopens a code draft as code", async () => {
    mocks.draft = {
      ...mocks.draft,
      eval_type: "code",
      code: CODE,
      config: { code: CODE },
    };
    renderPage();

    const tab = await codeTab();
    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByTestId("code-editor")).toHaveTextContent(
      "def evaluate(input, output, expected):",
    );
  });
});

describe("Create evaluation's Code tab where the answer says nothing about it", () => {
  it("offers Code when the answer has no code evaluations entry", async () => {
    mocks.capabilities = { features: {} };
    const client = renderPage();
    expect(answerRead(client)).toBe("success");
    const tab = await codeTab();
    expect(tab).not.toHaveAttribute("aria-disabled", "true");

    await userEvent.click(tab);

    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByTestId("code-editor")).toBeInTheDocument();
  });

  it("offers Code when the capabilities read fails", async () => {
    mocks.capabilities = new Error("capabilities read failed");
    const client = renderPage({ read: true });
    // The hook retries once; wait until the read has failed for good.
    await waitFor(() => expect(answerRead(client)).toBe("error"), {
      timeout: 5000,
    });
    const tab = await codeTab();
    expect(tab).not.toHaveAttribute("aria-disabled", "true");

    await userEvent.click(tab);

    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByTestId("code-editor")).toBeInTheDocument();
    expect(mocks.snack).not.toHaveBeenCalledWith(
      SERVER_SENTENCE,
      expect.anything(),
    );
  }, 10000);
});

describe("Create evaluation's Code tab where code evaluations don't run", () => {
  beforeEach(() => {
    mocks.capabilities = codeEvals(false);
  });

  it("shows Code turned off, described by the server's sentence", async () => {
    renderPage();
    const tab = await codeTab();

    await waitFor(() => expect(tab).toHaveAttribute("aria-disabled", "true"));
    expect(tab).toHaveAccessibleDescription(SERVER_SENTENCE);
    // Still reachable by keyboard, so its reason can be read.
    expect(tab).not.toHaveAttribute("disabled");
    tab.focus();
    expect(tab).toHaveFocus();
  });

  it("keeps the current type and says why when Code is chosen", async () => {
    renderPage();
    const tab = await codeTab();
    await waitFor(() => expect(tab).toHaveAttribute("aria-disabled", "true"));

    await userEvent.click(tab);

    expect(tab).toHaveAttribute("aria-selected", "false");
    expect(screen.queryByTestId("code-editor")).toBeNull();
    expect(mocks.snack).toHaveBeenCalledWith(SERVER_SENTENCE, {
      variant: "info",
    });
  });

  const openCodeDraft = async () => {
    mocks.draft = {
      ...mocks.draft,
      eval_type: "code",
      code: CODE,
      config: { code: CODE },
    };
    renderPage();
    const tab = await codeTab();
    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
    return tab;
  };

  it("reopens a code draft as code, in the same off state", async () => {
    const tab = await openCodeDraft();

    expect(tab).toHaveAttribute("aria-disabled", "true");
    expect(tab).toHaveAccessibleDescription(SERVER_SENTENCE);
    expect(screen.getByTestId("code-editor")).toHaveTextContent(
      "def evaluate(input, output, expected):",
    );
  });

  it("lets a code draft go back to its Code tab after another tab", async () => {
    const tab = await openCodeDraft();
    const judge = screen.getByRole("tab", { name: "LLM-As-A-Judge" });

    await userEvent.click(judge);
    await waitFor(() => expect(judge).toHaveAttribute("aria-selected", "true"));
    expect(screen.queryByTestId("code-editor")).toBeNull();

    await userEvent.click(tab);

    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByTestId("code-editor")).toHaveTextContent(
      "def evaluate(input, output, expected):",
    );
    // The same off state, and no refusal for the draft's own type.
    expect(tab).toHaveAttribute("aria-disabled", "true");
    expect(tab).toHaveAccessibleDescription(SERVER_SENTENCE);
    expect(mocks.snack).not.toHaveBeenCalledWith(
      SERVER_SENTENCE,
      expect.anything(),
    );
  });
});

// Saving an Agents evaluation where Agents evaluations are not enabled says
// what to use instead; Code is named only where code evaluations run.
const AGENTS_REFUSED_WITH_CODE =
  "Agent evaluations aren't enabled for this workspace. Use LLM-as-a-Judge or Code evaluations instead.";
const AGENTS_REFUSED =
  "Agent evaluations aren't enabled for this workspace. Use LLM-as-a-Judge instead.";

describe("saving an Agents evaluation that isn't enabled", () => {
  beforeEach(() => {
    mocks.agentLocked = true;
    mocks.draft = {
      ...mocks.draft,
      eval_type: "agent",
      instructions: "Is {{output}} polite?",
    };
  });

  const saveAgents = async () => {
    renderPage();
    await userEvent.type(
      await screen.findByPlaceholderText("e.g. hallucination_detector"),
      "polite_check",
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Save Evaluation" }),
    );
  };

  it.each([
    ["code evaluations run", () => codeEvals(true)],
    ["the server sends no code evaluations entry", () => ({ features: {} })],
  ])("names Code as an alternative where %s", async (_where, answer) => {
    mocks.capabilities = answer();

    await saveAgents();

    await waitFor(() =>
      expect(mocks.snack).toHaveBeenCalledWith(AGENTS_REFUSED_WITH_CODE, {
        variant: "error",
      }),
    );
    expect(mocks.mutateAsync).not.toHaveBeenCalled();
  });

  it("leaves Code out where code evaluations don't run", async () => {
    mocks.capabilities = codeEvals(false);
    renderPage();
    const tab = await codeTab();
    await waitFor(() => expect(tab).toHaveAttribute("aria-disabled", "true"));
    await userEvent.type(
      screen.getByPlaceholderText("e.g. hallucination_detector"),
      "polite_check",
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "Save Evaluation" }),
    );

    await waitFor(() =>
      expect(mocks.snack).toHaveBeenCalledWith(AGENTS_REFUSED, {
        variant: "error",
      }),
    );
    expect(mocks.snack).not.toHaveBeenCalledWith(
      AGENTS_REFUSED_WITH_CODE,
      expect.anything(),
    );
    expect(mocks.mutateAsync).not.toHaveBeenCalled();
  });
});

// The page's autosave waits 800 ms after the last change.
const pastAutosave = () => new Promise((resolve) => setTimeout(resolve, 1000));

describe("a code draft that looks at another tab", () => {
  beforeEach(() => {
    mocks.capabilities = codeEvals(false);
    mocks.realInstructionEditor = false;
    // The draft as saved: a code evaluation that also kept instructions
    // from an earlier type. The server keeps the type an autosave sends.
    mocks.draft = {
      ...mocks.draft,
      eval_type: "code",
      code: CODE,
      instructions: "Is {{output}} polite?",
      config: { code: CODE, model: "turing_large" },
    };
    mocks.mutate.mockImplementation((payload) => {
      mocks.draft = { ...mocks.draft, eval_type: payload.eval_type };
    });
  });
  afterEach(() => {
    mocks.mutate.mockReset();
    mocks.realInstructionEditor = false;
  });

  // Open the draft and let anything the opening itself saves settle first.
  const openDraft = async () => {
    renderPage();
    const tab = await codeTab();
    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
    await pastAutosave();
    mocks.mutate.mockClear();
  };

  const goTo = async (name) => {
    const tab = screen.getByRole("tab", { name });
    await userEvent.click(tab);
    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
  };

  // A fresh page opens the draft as the server now holds it.
  const reopensOn = async (name) => {
    cleanup();
    renderPage();
    const tab = await screen.findByRole("tab", { name });
    await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
  };

  it.each([
    ["LLM-As-A-Judge", false],
    ["Agents", true],
  ])(
    "saves no type change where Code is off when it only looks at %s",
    async (other, realEditor) => {
      // Agents opens its own editor, in the ordinary case: nothing in the
      // draft for it to change as it opens.
      mocks.realInstructionEditor = realEditor;
      await openDraft();

      await goTo(other);
      await pastAutosave();

      expect(mocks.mutate).not.toHaveBeenCalled();
      await reopensOn(/Code/);
    },
    15000,
  );

  it("saves no type change where Code is off when it looks at both other tabs", async () => {
    await openDraft();

    await goTo("LLM-As-A-Judge");
    await goTo("Agents");
    await pastAutosave();

    expect(mocks.mutate).not.toHaveBeenCalled();
    await reopensOn(/Code/);
  }, 15000);

  it("saves the new type once when something changes on the other tab", async () => {
    await openDraft();
    await goTo("LLM-As-A-Judge");
    await pastAutosave();

    await userEvent.click(
      screen.getByRole("button", { name: "Type a prompt" }),
    );
    await pastAutosave();

    expect(mocks.mutate).toHaveBeenCalledTimes(1);
    expect(mocks.mutate.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        eval_type: "llm",
        messages: [{ role: "system", content: "Is {{output}} kind?" }],
      }),
    );
    await reopensOn("LLM-As-A-Judge");
  }, 15000);

  it("saves the new type when Save is pressed on the other tab", async () => {
    await openDraft();
    await goTo("LLM-As-A-Judge");
    await userEvent.type(
      screen.getByPlaceholderText("e.g. hallucination_detector"),
      "polite_check",
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "Save Evaluation" }),
    );

    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledTimes(1));
    expect(mocks.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ eval_type: "llm", publish: true }),
    );
    // Save alone sends it.
    await pastAutosave();
    expect(mocks.mutate).not.toHaveBeenCalled();
  }, 15000);

  // As it is: on opening, the Agents editor moves instructions that hold
  // template blocks to the Jinja format, and that change is saved with the
  // Agents type.
  it("saves the Agents type when its editor changes the draft on opening", async () => {
    mocks.realInstructionEditor = true;
    mocks.draft = {
      ...mocks.draft,
      instructions: "{% if output %}Is {{ output }} polite?{% endif %}",
      template_format: "mustache",
    };
    await openDraft();

    await goTo("Agents");
    await pastAutosave();

    expect(mocks.mutate).toHaveBeenCalledTimes(1);
    expect(mocks.mutate.mock.calls[0][0]).toEqual(
      expect.objectContaining({ eval_type: "agent", template_format: "jinja" }),
    );
    await reopensOn("Agents");
  }, 15000);

  it("saves the new type from another tab where code evaluations run", async () => {
    mocks.capabilities = codeEvals(true);
    await openDraft();

    await goTo("LLM-As-A-Judge");
    await pastAutosave();

    expect(mocks.mutate).toHaveBeenCalledTimes(1);
    expect(mocks.mutate.mock.calls[0][0]).toEqual(
      expect.objectContaining({ eval_type: "llm" }),
    );
  }, 15000);
});
