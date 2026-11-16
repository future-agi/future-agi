import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { CHANNEL_OPTIONS, CLEAN, typedActor } from "./castFixtures";

let verdict;
vi.mock("src/api/simulate-environments/versions", () => ({
  // The server's answer, fixed per test.
  useActorCheck: (_envId, request) => ({
    verdict: request ? verdict : null,
    pending: false,
    isError: false,
    error: null,
    retry: vi.fn(),
  }),
}));

const { default: ActorForm } = await import("../ActorForm");

const CONTEXT = {
  actions: [],
  channelOptions: CHANNEL_OPTIONS,
  loudnessRange: [0.333, 0.667],
  idHint: "1 to 64 lowercase letters.",
  actorModels: [],
  knowledgeSources: { files: [], records: [] },
  others: [],
};

const renderForm = (actor) =>
  render(
    <ActorForm
      envId="env-1"
      actor={actor}
      context={CONTEXT}
      onClose={vi.fn()}
      onSave={vi.fn()}
    />,
  );

describe("ActorForm", () => {
  it("offers the approver kind", () => {
    verdict = CLEAN;
    renderForm(
      typedActor({
        id: "claims_supervisor",
        name: "Dana Brooks",
        kind: "approver",
      }),
    );
    // The saved kind reads as its label, and the list offers it beside the others.
    expect(screen.getByText("An approver")).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByText("An approver"));
    expect(
      screen.getByRole("option", { name: "An approver" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "A person" }),
    ).toBeInTheDocument();
  });

  it("titles a new actor's form Add actor, with the side it is added on", () => {
    verdict = CLEAN;
    renderForm({ side: "caller" });
    expect(screen.getByText("Add actor")).toBeInTheDocument();
    expect(
      screen.getByText(
        "On the caller's side · added to this environment's draft",
      ),
    ).toBeInTheDocument();
  });

  it("offers only the ways of coming in the chosen channel takes", () => {
    verdict = CLEAN;
    renderForm({ side: "world" });
    fireEvent.mouseDown(screen.getByLabelText("Comes in"));
    expect(
      screen.getByRole("option", { name: "When the agent contacts them" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "When the agent says a word" }),
    ).toBeNull();
  });

  it("shows a problem the server has no field for above Save, and keeps Save off", () => {
    renderForm(typedActor({ id: "ops", name: "Ops" }));
    verdict = {
      ...CLEAN,
      problems: [
        {
          field: null,
          message: "ops is in a cycle of parent links; break it.",
        },
      ],
    };
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ops Lead" },
    });
    expect(screen.getByTestId("actor-problems")).toHaveTextContent(
      "ops is in a cycle of parent links; break it.",
    );
    expect(
      screen.getByRole("button", { name: "Save to draft" }),
    ).toBeDisabled();
  });
});
