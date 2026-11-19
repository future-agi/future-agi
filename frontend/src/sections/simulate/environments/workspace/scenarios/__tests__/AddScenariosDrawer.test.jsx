import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "src/utils/test-utils";
import constantsSource from "../addScenarios.constants.js?raw";
import { renderWithClient } from "./scenariosTestUtils";
import userEvent from "@testing-library/user-event";

const extendHarnessJob = vi.fn(async () => ({}));
const sendHarnessConversationMessage = vi.fn(async () => ({}));
const uploadEnvironmentFile = vi.fn(async () => ({}));
vi.mock("src/api/harness/harness", () => ({
  extendHarnessJob: (...args) => extendHarnessJob(...args),
  sendHarnessConversationMessage: (...args) =>
    sendHarnessConversationMessage(...args),
  harnessIdempotencyKey: () => "req-1",
}));
const cast = vi.hoisted(() => ({ data: { actors: [], knowledge_sources: { files: [] } } }));
vi.mock("src/api/simulate-environments/versions", () => ({
  uploadEnvironmentFile: (...args) => uploadEnvironmentFile(...args),
  useActors: () => cast,
}));

const { default: AddScenariosDrawer } = await import("../AddScenariosDrawer");
const { DESCRIBE_PREFIX } = await import("../addScenarios.constants");

const ENV = { id: "env-1" };
const open = (props = {}) =>
  renderWithClient(
    <AddScenariosDrawer open onClose={vi.fn()} env={ENV} {...props} />,
  );

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
    expect(extendHarnessJob).toHaveBeenCalledWith("env-1", {
      count: 3,
      guidance: "cover cancellations",
    });
  });

  it("describes a case through the builder chat with the fixed prefix", async () => {
    const user = userEvent.setup();
    open();
    await user.click(screen.getByRole("button", { name: "Describe a case" }));
    await user.type(
      screen.getByLabelText(
        "What should the agent face, and what should it do?",
      ),
      "a 20-day refund",
    );
    await user.click(screen.getByRole("button", { name: "Write scenarios" }));
    const [, body] = sendHarnessConversationMessage.mock.calls[0];
    expect(body.content).toBe(`${DESCRIBE_PREFIX}\nRequested case 1: a 20-day refund`);
    expect(body.kind).toBe("user_message");
    // The platform reads the requested cases from the payload, never from the text.
    expect(body.payload).toEqual({ requested_cases: ["a 20-day refund"] });
  });

  it("uploads a policy file as knowledge and sends its text", async () => {
    const user = userEvent.setup();
    open();
    await user.click(
      screen.getByRole("button", { name: "From a policy file" }),
    );
    const file = new File(["Refunds within 14 days."], "policy.md", {
      type: "text/markdown",
    });
    await user.upload(screen.getByTestId("add-scenarios-file"), file);
    await user.click(screen.getByRole("button", { name: "Write scenarios" }));
    expect(uploadEnvironmentFile).toHaveBeenCalledWith("env-1", {
      file,
      kind: "knowledge",
    });
    // The message goes after the upload and after the file is read.
    await waitFor(() =>
      expect(sendHarnessConversationMessage).toHaveBeenCalled(),
    );
    const [, body] = sendHarnessConversationMessage.mock.calls[0];
    expect(body.content).toContain(
      "grounded in this file (knowledge/policy.md)",
    );
    expect(body.content).toContain("Refunds within 14 days.");
  });

  it("no longer reads the fixture pool", () => {
    expect(constantsSource).not.toContain("generatedPool");
  });
});

describe("AddScenariosDrawer grounding", () => {
  beforeEach(() => {
    extendHarnessJob.mockClear();
    sendHarnessConversationMessage.mockClear();
    cast.data = {
      actors: [{ id: "dana", name: "Dana Brooks" }],
      knowledge_sources: { files: ["knowledge/policy.md"] },
    };
  });

  it("selected actors and sources appear in the sent message", async () => {
    const user = userEvent.setup();
    renderWithClient(
      <AddScenariosDrawer open onClose={vi.fn()} env={{ id: "env-1", rules: ["Refund within 14 days."] }} />,
    );
    await user.click(screen.getByRole("button", { name: "Describe a case" }));
    await user.type(
      screen.getByLabelText("What should the agent face, and what should it do?"),
      "a late refund{enter}a second refund",
    );
    await user.click(screen.getByLabelText("Actors to include"));
    await user.click(await screen.findByRole("option", { name: "Dana Brooks" }));
    await user.click(screen.getByLabelText("Ground in"));
    await user.click(await screen.findByRole("option", { name: "Rule: Refund within 14 days." }));
    await user.click(screen.getByLabelText("Ground in"));
    await user.click(await screen.findByRole("option", { name: "File: knowledge/policy.md" }));
    await user.click(screen.getByRole("button", { name: "Write scenarios" }));

    const [, body] = sendHarnessConversationMessage.mock.calls[0];
    expect(body.content).toBe(
      `${DESCRIBE_PREFIX}\nRequested case 1: a late refund\nRequested case 2: a second refund` +
        ' Include these actors: Dana Brooks. Cite these sources: rule "Refund within 14 days."; file knowledge/policy.md.',
    );
  });

  it("nothing added when none chosen", async () => {
    const user = userEvent.setup();
    renderWithClient(<AddScenariosDrawer open onClose={vi.fn()} env={{ id: "env-1" }} initialCount={2} />);
    await user.click(screen.getByRole("button", { name: "Write scenarios" }));
    expect(extendHarnessJob).toHaveBeenCalledWith("env-1", { count: 2 });
  });
});
