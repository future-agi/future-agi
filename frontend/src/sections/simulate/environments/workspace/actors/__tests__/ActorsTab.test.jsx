import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ActorsTab from "../ActorsTab";
import { showActorsTab } from "../actorUtils";

describe("showActorsTab", () => {
  it("shows the tab for a versioned environment or one with sub-actors, and hides it otherwise", () => {
    expect(showActorsTab({ id: "env-1", versioning: { enabled: true } })).toBe(true);
    expect(showActorsTab({ subActors: [{ id: "friend" }] })).toBe(true);
    expect(showActorsTab({})).toBe(false);
    expect(showActorsTab({ versioning: { enabled: false } })).toBe(false);
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
