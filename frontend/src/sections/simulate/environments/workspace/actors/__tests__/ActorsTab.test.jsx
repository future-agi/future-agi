import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ActorsTab from "../ActorsTab";
import { showActorsTab } from "../actorUtils";

vi.mock("src/api/simulate-environments/versions", () => ({
  useActors: () => ({
    data: {
      actors: [],
      channels: { audio_mix: "a voice nearby" },
      loudness: {},
    },
  }),
  useActorView: () => ({}),
  useEditActor: () => ({ mutate: vi.fn(), reset: vi.fn() }),
}));

describe("showActorsTab", () => {
  it("shows the tab for a versioned environment or one with sub-actors, and hides it otherwise", () => {
    expect(showActorsTab({ id: "env-1", versioning: { enabled: true } })).toBe(
      true,
    );
    expect(showActorsTab({ subActors: [{ id: "friend" }] })).toBe(true);
    expect(showActorsTab({})).toBe(false);
    expect(showActorsTab({ versioning: { enabled: false } })).toBe(false);
  });
});

describe("ActorsTab with versioning", () => {
  const versioned = { id: "env-1", versioning: { enabled: true } };
  const callerSide = () => screen.queryByText(/^On the caller's side/);

  it("edits the cast, with the caller's side on a voice environment", () => {
    render(<ActorsTab env={{ ...versioned, surface: "voice" }} />);
    expect(screen.getByText(/^In the world/)).toBeInTheDocument();
    expect(callerSide()).toBeInTheDocument();
  });

  it("leaves the caller's side out on a chat environment with no sub-actors", () => {
    render(<ActorsTab env={{ ...versioned, surface: "chat" }} />);
    expect(screen.getByText(/^In the world/)).toBeInTheDocument();
    expect(callerSide()).toBeNull();
  });

  it("shows the caller's side on any environment that already has sub-actors", () => {
    render(
      <ActorsTab
        env={{ ...versioned, surface: "chat", subActors: [{ id: "friend" }] }}
      />,
    );
    expect(callerSide()).toBeInTheDocument();
  });

  it("is read-only when the environment is locked", () => {
    render(<ActorsTab env={{ ...versioned, surface: "voice" }} locked />);
    expect(screen.queryByText("Add someone in the world")).toBeNull();
  });
});

describe("ActorsTab without versioning", () => {
  it("lists the environment's sub-actors with how they take part and how many scenarios include them", () => {
    render(
      <ActorsTab
        env={{
          surface: "voice",
          subActors: [
            {
              id: "friend",
              name: "Rowan",
              role: "a friend with the caller",
              channel: "audio_mix",
              persona: { gender: "female", age_group: "25-32" },
              scenarios_count: 2,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Sub-actors (1)")).toBeInTheDocument();
    expect(screen.getByText(/a friend with the caller/)).toBeInTheDocument();
    expect(
      screen.getByText("Heard nearby · Female · 25-32 · In 2 scenarios"),
    ).toBeInTheDocument();
  });

  it("shows no sub-actors section for an environment without any", () => {
    render(<ActorsTab env={{ surface: "voice" }} />);
    expect(screen.queryByText(/^Sub-actors/)).toBeNull();
  });
});
