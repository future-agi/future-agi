import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ActorRelations, { actorEdges } from "../ActorRelations";
import { typedActor } from "./castFixtures";

const REACH = "can reach them through its actions";

describe("actorEdges", () => {
  it("joins a caller-side actor to the agent, and the agent to a world actor, each by when it comes in", () => {
    expect(
      actorEdges([
        typedActor({ id: "caller", name: "Dana", side: "caller" }),
        typedActor({
          id: "manager",
          name: "Priya",
          when_label: "When contacted",
        }),
      ]),
    ).toEqual([
      {
        key: "Dana→Agent",
        from: "Dana",
        to: "Agent",
        label: "From the start",
      },
      {
        key: "Agent→Priya",
        from: "Agent",
        to: "Priya",
        label: "When contacted",
      },
    ]);
  });

  it("joins a world actor to the actor who brings it in, not to the agent", () => {
    const edges = actorEdges([
      typedActor({ id: "manager", name: "Priya" }),
      typedActor({
        id: "vp",
        name: "Alex",
        parent: "manager",
        when_label: "When Priya asks",
      }),
    ]);
    expect(edges.map(({ from, to, label }) => [from, to, label])).toEqual([
      ["Agent", "Priya", "From the start"],
      ["Priya", "Alex", "When Priya asks"],
    ]);
  });

  it("adds an edge for each actor its actions reach, after how they come in", () => {
    const edges = actorEdges([
      typedActor({ id: "manager", name: "Priya", reaches: ["caller", "ops"] }),
      typedActor({ id: "caller", name: "Dana", side: "caller" }),
      typedActor({ id: "ops", name: "Ops" }),
    ]);
    expect(edges.map(({ from, to, label }) => [from, to, label])).toEqual([
      ["Agent", "Priya", "From the start"],
      ["Dana", "Agent", "From the start"],
      ["Agent", "Ops", "From the start"],
      ["Priya", "Dana", REACH],
      ["Priya", "Ops", REACH],
    ]);
  });

  it("keeps one edge per pair, the first one found", () => {
    const edges = actorEdges([
      typedActor({ id: "manager", name: "Priya", reaches: ["vp", "vp"] }),
      typedActor({
        id: "vp",
        name: "Alex",
        parent: "manager",
        when_label: "When Priya asks",
      }),
    ]);
    expect(edges.map(({ key, label }) => [key, label])).toEqual([
      ["Agent→Priya", "From the start"],
      ["Priya→Alex", "When Priya asks"],
    ]);
  });

  it("names an actor by its id when it has no name, or is not in the cast, and leaves a missing when blank", () => {
    const edges = actorEdges([
      typedActor({
        id: "lead",
        name: "",
        parent: "gone",
        when_label: null,
        reaches: ["elsewhere"],
      }),
    ]);
    expect(edges.map(({ from, to, label }) => [from, to, label])).toEqual([
      ["gone", "lead", ""],
      ["lead", "elsewhere", REACH],
    ]);
  });

  it("has no edges for an empty cast", () => {
    expect(actorEdges([])).toEqual([]);
  });
});

describe("ActorRelations", () => {
  it("lists who can reach whom in a run, each edge with its label", () => {
    render(
      <ActorRelations
        cast={[
          typedActor({
            id: "manager",
            name: "Priya",
            reaches: ["caller"],
            when_label: "When contacted",
          }),
          typedActor({ id: "caller", name: "Dana", side: "caller" }),
        ]}
      />,
    );
    expect(screen.getByText("How they connect")).toBeInTheDocument();
    expect(screen.getByText("Who can reach whom in a run")).toBeInTheDocument();
    expect(screen.getAllByText("Agent")).toHaveLength(2);
    expect(screen.getAllByText("Priya")).toHaveLength(2);
    expect(screen.getAllByText("Dana")).toHaveLength(2);
    expect(screen.getByText("When contacted")).toBeInTheDocument();
    expect(screen.getByText("From the start")).toBeInTheDocument();
    expect(screen.getByText(REACH)).toBeInTheDocument();
  });

  it("shows nothing for an empty cast", () => {
    const { container } = render(<ActorRelations cast={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
