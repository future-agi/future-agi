/* eslint-disable react/prop-types */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "src/utils/test-utils";
import SDkComponentVoiceTestRun from "../SDkComponentVoiceTestRun";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    runTests: {
      getVoiceSDKCode: (id) => `/simulate/run-tests/${id}/sdk-code/`,
    },
  },
}));

vi.mock("react-syntax-highlighter", () => ({
  Prism: ({ children }) => <pre>{children}</pre>,
}));

vi.mock("src/components/iconify", () => ({ default: () => <span /> }));

// Mirrors the live /simulate/run-tests/{id}/sdk-code/ response (snake_case).
const voiceFixture = {
  installation_guide: "VOICE_INSTALL_MARKER",
  sdk_code: "VOICE_SDK_MARKER",
  run_test_id: "rt-1",
  run_test_name: "Run Test",
};

const renderVoice = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SDkComponentVoiceTestRun />
    </QueryClientProvider>,
  );
};

describe("SDkComponentVoiceTestRun SDK code (TH-5814)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({ data: { result: voiceFixture } });
  });

  it("renders both step code blocks from the snake_case response", async () => {
    const { container } = renderVoice();

    // installation_guide (regressed from installationGuide)
    await waitFor(() =>
      expect(container.textContent).toContain("VOICE_INSTALL_MARKER"),
    );
    // sdk_code (regressed from sdkCode)
    expect(container.textContent).toContain("VOICE_SDK_MARKER");
    expect(container.textContent).not.toContain("Code not available");
  });
});
