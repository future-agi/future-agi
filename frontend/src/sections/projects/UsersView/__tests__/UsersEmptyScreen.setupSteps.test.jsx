import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// OBS-SW-109: the Users setup steps must be followable as written. The
// snippet the endpoint returns is Python that imports three packages, so the
// install step has to install them and the snippet has to read as Python.
const INSTALL_COMMAND =
  "pip install fi-instrumentation-otel traceai-openai openai";
const PYTHON_SNIPPET = [
  "import openai",
  "from fi_instrumentation import FITracer, register, using_attributes",
  "from traceai_openai import OpenAIInstrumentor",
].join("\n");

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

// The top-level Users page (/dashboard/users) has no project in the URL.
vi.mock("react-router", async (orig) => ({
  ...(await orig()),
  useParams: () => ({}),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => getMock(...args) },
  endpoints: {
    project: { getUserExampleCode: () => "/tracer/users/get_code_example/" },
  },
}));

// Show what each code block contains and how it is highlighted.
vi.mock("src/sections/project/NewProject/InstructionCodeCopy", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ text, language }) => <pre data-language={language}>{text}</pre>,
}));
vi.mock("src/sections/project/NewProject/InstructionTitle", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ title }) => <h6>{title}</h6>,
}));

import UsersEmptyScreen from "../UsersEmptyScreen";

const renderScreen = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <UsersEmptyScreen />
    </QueryClientProvider>,
  );
};

const comesBefore = (earlier, later) =>
  earlier.compareDocumentPosition(later) === Node.DOCUMENT_POSITION_FOLLOWING;

describe("UsersEmptyScreen setup steps (OBS-SW-109)", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue({ data: { result: PYTHON_SNIPPET } });
  });

  it("shows the pip install command for the snippet's packages under Install dependencies", async () => {
    renderScreen();

    const install = await screen.findByText(INSTALL_COMMAND);
    expect(install).toHaveAttribute("data-language", "bash");
    const installTitle = screen.getByText("Install dependencies");
    const telemetryTitle = screen.getByText("Set up telemetry");
    expect(comesBefore(installTitle, install)).toBe(true);
    expect(comesBefore(install, telemetryTitle)).toBe(true);
  });

  it("labels the snippet Python and highlights it as Python", async () => {
    renderScreen();

    const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabs).toEqual(["Python"]);
    const snippet = await screen.findByText(/from traceai_openai import/);
    expect(snippet).toHaveAttribute("data-language", "python");
  });
});
