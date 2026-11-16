import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ActorsTab from "../ActorsTab";

vi.mock("src/api/simulate-environments/versions", async () => {
  const { cast: castOf } = await import("./castFixtures");
  return {
    useActors: () => ({ data: castOf([]) }),
    useActorView: () => ({}),
    useActorCheck: () => ({
      verdict: null,
      pending: false,
      isError: false,
      error: null,
      retry: vi.fn(),
    }),
    useEditActor: () => ({ mutate: vi.fn(), reset: vi.fn() }),
    useSetActorLimits: () => ({ mutate: vi.fn(), reset: vi.fn() }),
    useAcceptUnresolved: () => ({ mutate: vi.fn(), reset: vi.fn() }),
    useUploadEnvironmentFile: () => ({ mutateAsync: vi.fn(), reset: vi.fn() }),
  };
});

describe("ActorsTab", () => {
  it("edits the cast when the environment says edit", () => {
    render(<ActorsTab env={{ id: "env-1", actorsTab: "edit" }} />);
    expect(screen.getByText("Actors (0)")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add actor" }),
    ).toBeInTheDocument();
  });

  it("is read-only when the environment is locked", () => {
    render(<ActorsTab env={{ id: "env-1", actorsTab: "edit" }} locked />);
    expect(screen.queryByText("Add actor")).toBeNull();
  });

  it("shows nothing when the environment has no Actors tab", () => {
    const { container } = render(
      <ActorsTab env={{ id: "env-1", actorsTab: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the environment's actors with how they take part and how many scenarios include them, when it says list", () => {
    render(
      <ActorsTab
        env={{
          surface: "voice",
          actorsTab: "list",
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
