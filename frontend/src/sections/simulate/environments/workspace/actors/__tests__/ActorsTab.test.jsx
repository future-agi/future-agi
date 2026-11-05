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
  it("shows the tab for a versioned environment or one with actors, and hides it otherwise", () => {
    expect(showActorsTab({ id: "env-1", versioning: { enabled: true } })).toBe(
      true,
    );
    expect(showActorsTab({ callActors: [{ id: "friend" }] })).toBe(true);
    expect(showActorsTab({})).toBe(false);
    expect(showActorsTab({ versioning: { enabled: false } })).toBe(false);
  });
});

describe("ActorsTab with versioning", () => {
  const versioned = { id: "env-1", versioning: { enabled: true } };
  it("edits every actor in one list, on any environment", () => {
    for (const surface of ["voice", "chat"]) {
      const { unmount } = render(<ActorsTab env={{ ...versioned, surface }} />);
      expect(screen.getByText(/^Actors/)).toBeInTheDocument();
      expect(
        screen.queryByText(/On the caller's side|In the world/),
      ).toBeNull();
      unmount();
    }
  });

  it("is read-only when the environment is locked", () => {
    render(<ActorsTab env={{ ...versioned, surface: "voice" }} locked />);
    expect(screen.queryByText("Add an actor")).toBeNull();
  });
});

describe("ActorsTab without versioning", () => {
  it("lists the environment's actors with how they take part and how many scenarios include them", () => {
    render(
      <ActorsTab
        env={{
          surface: "voice",
          callActors: [
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
    expect(screen.getByText("Actors (1)")).toBeInTheDocument();
    expect(screen.getByText(/a friend with the caller/)).toBeInTheDocument();
    expect(
      screen.getByText("Heard nearby · Female · 25-32 · In 2 scenarios"),
    ).toBeInTheDocument();
  });
});
