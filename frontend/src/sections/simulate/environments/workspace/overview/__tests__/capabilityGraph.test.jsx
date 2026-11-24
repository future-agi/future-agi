import { describe, it, expect } from "vitest";
import { screen, fireEvent } from "@testing-library/react";

import { render } from "src/utils/test-utils";
import CapabilityGraph from "../CapabilityGraph";

const env = { name: "ride-voice-agent", surface: "voice", tools: [] };

describe("CapabilityGraph", () => {
  it("uses the real §6 data, renders a Personas branch (not 'Personas & actors')", () => {
    render(
      <CapabilityGraph
        env={env}
        envState={{}}
        defaultOpen
        data={{
          tools: ["book_ride"],
          flows: ["Book a ride"],
          personas: ["Late rider"],
          guardrails: ["Never invent a fare"],
        }}
      />,
    );

    expect(screen.getAllByText("Personas").length).toBeGreaterThan(0);
    expect(screen.queryByText(/Personas & actors/)).toBeNull();
    expect(screen.getByText("book_ride")).toBeInTheDocument();
    expect(screen.getByText("Book a ride")).toBeInTheDocument();
    expect(screen.getByText("Late rider")).toBeInTheDocument();
  });

  it("leaves an empty branch out of the graph and its tabs", () => {
    render(
      <CapabilityGraph
        env={env}
        envState={{}}
        defaultOpen
        data={{
          tools: ["book_ride"],
          flows: ["Book a ride"],
          personas: [],
          guardrails: [],
        }}
      />,
    );

    expect(screen.queryByText("Personas")).toBeNull();
    expect(screen.queryByText("Guardrails")).toBeNull();
    expect(screen.queryByText("none yet")).toBeNull();
    expect(screen.getByRole("tab", { name: "Tools" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Flows" })).toBeInTheDocument();
  });

  it("draws nothing when every branch is empty", () => {
    const { container } = render(
      <CapabilityGraph
        env={env}
        envState={{}}
        data={{ tools: [], flows: [], personas: [], guardrails: [] }}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("caps a long branch at 5 and surfaces the rest without moving the view", () => {
    const flows = Array.from({ length: 8 }, (_, i) => `flow-${i}`);
    render(
      <CapabilityGraph
        env={env}
        envState={{}}
        defaultOpen
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

  it("starts collapsed and opens on click", () => {
    render(
      <CapabilityGraph
        env={env}
        envState={{}}
        data={{ tools: ["book_ride"], flows: [], personas: [], guardrails: [] }}
      />,
    );

    expect(screen.getByText("Capability graph")).toBeInTheDocument();
    expect(screen.queryByText("book_ride")).toBeNull();
    expect(screen.queryByRole("tab", { name: "Tools" })).toBeNull();

    expect(screen.getByText("Tools, flows, personas and guardrails as one picture")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Capability graph/ }));
    expect(screen.getByText("book_ride")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Tools" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Hide capability graph" }));
    expect(screen.queryByRole("tab", { name: "Tools" })).toBeNull();
  });
});
