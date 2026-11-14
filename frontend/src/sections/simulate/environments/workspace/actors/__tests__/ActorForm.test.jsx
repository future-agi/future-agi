import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ActorForm from "../ActorForm";

const CONTEXT = {
  actions: [],
  records: {},
  channels: {},
  loudness: {},
  actorModels: [],
  knowledgeSources: { files: [], records: [] },
  others: [],
  reservedIds: [],
};

describe("ActorForm", () => {
  it("offers the approver kind", () => {
    render(
      <ActorForm
        actor={{
          id: "claims_supervisor",
          name: "Dana Brooks",
          kind: "approver",
        }}
        context={CONTEXT}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
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
});
