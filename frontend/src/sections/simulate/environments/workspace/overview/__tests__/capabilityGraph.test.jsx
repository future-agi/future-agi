import { describe, it, expect } from "vitest";
import { screen, fireEvent } from "@testing-library/react";

import { render } from "src/utils/test-utils";
import CapabilityGraph from "../CapabilityGraph";

const env = { name: "ride-voice-agent", surface: "voice", tools: [] };

describe("CapabilityGraph", () => {
  it("uses the real §6 data, renders a Personas branch (not 'Personas & actors'), and an empty state", () => {
    render(
      <CapabilityGraph
        env={env}
        envState={{}}
        data={{
          tools: ["book_ride"],
          flows: ["Book a ride"],
          personas: [], // empty §6 world.personas
          guardrails: ["Never invent a fare"],
        }}
      />,
    );

    // Renamed branch: "Personas", and no "actors".
    expect(screen.getAllByText("Personas").length).toBeGreaterThan(0);
    expect(screen.queryByText(/Personas & actors/)).toBeNull();
    // Real data leaves render.
    expect(screen.getByText("book_ride")).toBeInTheDocument();
    expect(screen.getByText("Book a ride")).toBeInTheDocument();
    // Empty personas branch shows the honest placeholder, not a fixture actor.
    expect(screen.getByText("none yet")).toBeInTheDocument();
  });

  it("caps a long branch at 5 and surfaces the rest without moving the view", () => {
    const flows = Array.from({ length: 8 }, (_, i) => `flow-${i}`);
    render(
      <CapabilityGraph
        env={env}
        envState={{}}
        data={{ tools: [], flows, personas: [], guardrails: [] }}
      />,
    );

    // 8 flows, capped at 5 → "+ 3 more"; the overflow lives in a hover tooltip,
    // so the hidden rows aren't inline.
    expect(screen.getByText(/\+ 3 more/)).toBeInTheDocument();
    expect(screen.queryByText("flow-7")).toBeNull();

    // Clicking "+ 3 more" does nothing — it never focuses/moves the view.
    fireEvent.click(screen.getByText(/\+ 3 more/));
    expect(screen.queryByText("flow-7")).toBeNull();
    expect(screen.getByText(/\+ 3 more/)).toBeInTheDocument();
  });
});
