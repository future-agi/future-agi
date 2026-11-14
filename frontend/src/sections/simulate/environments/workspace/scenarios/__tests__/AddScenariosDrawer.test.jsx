import { describe, it, expect, vi, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const extendHarnessJob = vi.fn(async () => ({}));
const sendHarnessConversationMessage = vi.fn(async () => ({}));
const uploadEnvironmentFile = vi.fn(async () => ({}));
vi.mock("src/api/harness/harness", () => ({
  extendHarnessJob: (...args) => extendHarnessJob(...args),
  sendHarnessConversationMessage: (...args) => sendHarnessConversationMessage(...args),
  harnessIdempotencyKey: () => "req-1",
}));
vi.mock("src/api/simulate-environments/versions", () => ({
  uploadEnvironmentFile: (...args) => uploadEnvironmentFile(...args),
}));

const { default: AddScenariosDrawer } = await import("../AddScenariosDrawer");
const { DESCRIBE_PREFIX } = await import("../addScenarios.constants");

const ENV = { id: "env-1" };
const open = (props = {}) => render(<AddScenariosDrawer open onClose={vi.fn()} env={ENV} {...props} />);

describe("AddScenariosDrawer routes", () => {
  beforeEach(() => {
    extendHarnessJob.mockClear();
    sendHarnessConversationMessage.mockClear();
    uploadEnvironmentFile.mockClear();
  });

  it("generates more through extend with the count and guidance", async () => {
    const user = userEvent.setup();
    open({ initialGuidance: "cover cancellations", initialCount: 3 });
    await user.click(screen.getByRole("button", { name: "Write scenarios" }));
    expect(extendHarnessJob).toHaveBeenCalledWith("env-1", { count: 3, guidance: "cover cancellations" });
  });

  it("describes a case through the builder chat with the fixed prefix", async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByRole("button", { name: "Describe a case" }));
    await user.type(screen.getByLabelText("What should the agent face, and what should it do?"), "a 20-day refund");
    await user.click(screen.getByRole("button", { name: "Write scenarios" }));
    const [, body] = sendHarnessConversationMessage.mock.calls[0];
    expect(body.content).toBe(`${DESCRIBE_PREFIX} a 20-day refund`);
    expect(body.kind).toBe("user_message");
  });

  it("uploads a policy file as knowledge and sends its text", async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByRole("button", { name: "From a policy file" }));
    const file = new File(["Refunds within 14 days."], "policy.md", { type: "text/markdown" });
    await user.upload(screen.getByTestId("add-scenarios-file"), file);
    await user.click(screen.getByRole("button", { name: "Write scenarios" }));
    expect(uploadEnvironmentFile).toHaveBeenCalledWith("env-1", { file, kind: "knowledge" });
    const [, body] = sendHarnessConversationMessage.mock.calls[0];
    expect(body.content).toContain("grounded in this file (knowledge/policy.md)");
    expect(body.content).toContain("Refunds within 14 days.");
  });

  it("no longer reads the fixture pool", () => {
    const source = readFileSync(new URL("../addScenarios.constants.js", import.meta.url), "utf8");
    expect(source).not.toContain("generatedPool");
  });
});
