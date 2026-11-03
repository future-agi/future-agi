import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const mutate = vi.fn();
let actorsData;

vi.mock("src/api/simulate-environments/versions", () => ({
  useActors: () => ({ data: actorsData }),
  useActorView: () => ({
    data: {
      sees: [{ input: "actor.goal", value: "Approve fast" }],
      never_sees: ["scenario.solution"],
    },
  }),
  useEditActor: () => ({
    mutate,
    reset: vi.fn(),
    isPending: false,
    error: null,
  }),
}));

const { default: WorldActorsPanel } = await import("../WorldActorsPanel");

const MANAGER = {
  id: "manager",
  name: "Priya Raman",
  role: "sales manager",
  goal: "Approve only discounts she can justify",
  permitted_actions: ["crm.approve_discount"],
};

describe("WorldActorsPanel", () => {
  beforeEach(() => {
    mutate.mockReset();
    actorsData = { actors: [MANAGER], source: "draft", revision: 3 };
  });

  it("lists the cast with what each may do and says the changes are unsaved", () => {
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Actors (1)")).toBeInTheDocument();
    expect(screen.getByText(/Priya Raman/)).toBeInTheDocument();
    expect(
      screen.getByText("May do: crm.approve_discount"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
  });

  it("leaves the caller's side to the Sub-actors panel", () => {
    actorsData = {
      actors: [
        MANAGER,
        {
          id: "colleague",
          name: "Jordan Taylor",
          side: "caller",
          channel: "audio_mix",
        },
      ],
      source: "saved",
      revision: 0,
    };
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Actors (1)")).toBeInTheDocument();
    expect(screen.queryByText(/Jordan Taylor/)).toBeNull();
  });

  it("opens what an actor sees and never sees", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText(/Priya Raman/));
    expect(screen.getByText("actor.goal: Approve fast")).toBeInTheDocument();
    expect(screen.getByText("scenario.solution")).toBeInTheDocument();
  });

  it("adds an actor to the draft at the revision it was read at", () => {
    actorsData = { actors: [], source: "saved", revision: 0 };
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add actor"));
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "manager" },
    });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya" },
    });
    fireEvent.change(screen.getByLabelText(/World actions they may take/), {
      target: { value: "crm.approve_discount, crm.reject_discount" },
    });
    fireEvent.change(screen.getByLabelText(/Comes in when the agent calls/), {
      target: { value: "crm.request_approval" },
    });
    fireEvent.click(screen.getByText("Save to draft"));
    const [change] = mutate.mock.calls[0];
    expect(change.op).toBe("create");
    expect(change.revision).toBe(0);
    expect(change.actor).toMatchObject({
      id: "manager",
      name: "Priya",
      permitted_actions: ["crm.approve_discount", "crm.reject_discount"],
      activation: {
        trigger: { kind: "on_tool", tool: "crm.request_approval" },
        fallback: "silent",
      },
    });
  });

  it("keeps an actor's trigger when only another field is edited", () => {
    const delayed = { kind: "after_seconds", seconds: 30 };
    actorsData = {
      actors: [
        { ...MANAGER, activation: { trigger: delayed, fallback: "silent" } },
      ],
      source: "draft",
      revision: 3,
    };
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya R." },
    });
    fireEvent.click(screen.getByText("Save to draft"));
    const [change] = mutate.mock.calls[0];
    expect(change.actor.name).toBe("Priya R.");
    expect(change.actor.activation.trigger).toEqual(delayed);
  });

  it("removes an actor from the draft, and a locked environment offers no edits", () => {
    const { unmount } = render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Remove"));
    expect(mutate.mock.calls[0][0]).toEqual({
      op: "delete",
      actorId: "manager",
      revision: 3,
    });
    unmount();
    render(<WorldActorsPanel envId="env-1" locked />);
    expect(screen.queryByText("Add actor")).toBeNull();
    expect(screen.queryByText("Remove")).toBeNull();
  });
});
