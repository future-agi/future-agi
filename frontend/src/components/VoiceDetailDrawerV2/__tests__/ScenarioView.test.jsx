import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import ScenarioView from "../ScenarioView";

// What an environment (harness) call sends: the dataset's persona column is
// there but empty, and the persona itself arrives as `persona_details`.
const PERSONA_DETAILS = {
  name: "Siddharth Nair",
  voice: "Indian male",
  age: "50-60",
  traits: ["Anxious", "Questioning"],
};
const emptyPersonaColumn = {
  persona: { column_name: "persona", value: {}, data_type: "persona" },
};

describe("ScenarioView persona", () => {
  it("falls back to persona_details when the persona column is empty", () => {
    render(
      <ScenarioView
        data={{
          scenario: "Riley",
          scenario_columns: emptyPersonaColumn,
          persona_details: PERSONA_DETAILS,
        }}
      />,
    );
    expect(screen.getByText("Persona")).toBeInTheDocument();
    expect(screen.getByText(/Name: Siddharth/i)).toBeInTheDocument();
    expect(screen.getByText(/Traits: Anxious, Questioning/i)).toBeInTheDocument();
  });

  it("shows the persona even when the call has no persona column at all", () => {
    render(<ScenarioView data={{ scenario: "Riley", persona_details: PERSONA_DETAILS }} />);
    expect(screen.getByText(/Name: Siddharth/i)).toBeInTheDocument();
  });

  it("keeps using a filled persona column", () => {
    render(
      <ScenarioView
        data={{
          scenario: "Riley",
          scenario_columns: {
            persona: { column_name: "persona", value: { name: "Helena Rostova" } },
          },
          persona_details: PERSONA_DETAILS,
        }}
      />,
    );
    expect(screen.getByText(/Name: Helena/i)).toBeInTheDocument();
    expect(screen.queryByText(/Name: Siddharth/i)).toBeNull();
  });

  it("hides the Persona card when there is no persona anywhere", () => {
    render(
      <ScenarioView
        data={{
          scenario: "Riley",
          scenario_columns: emptyPersonaColumn,
          persona_details: { name: null, voice: null, age: null, traits: [] },
        }}
      />,
    );
    expect(screen.queryByText("Persona")).toBeNull();
  });

  it("treats a persona cell stored as an empty dict string as empty", () => {
    render(
      <ScenarioView
        data={{
          scenario: "Riley",
          scenario_columns: { persona: { column_name: "persona", value: "{}" } },
          persona_details: PERSONA_DETAILS,
        }}
      />,
    );
    expect(screen.getByText(/Name: Siddharth/i)).toBeInTheDocument();
  });

  it("reads a Python-style dict string with None as empty too", () => {
    render(
      <ScenarioView
        data={{
          scenario: "Riley",
          scenario_columns: { persona: { column_name: "persona", value: "{'name': None}" } },
          persona_details: PERSONA_DETAILS,
        }}
      />,
    );
    expect(screen.getByText(/Name: Siddharth/i)).toBeInTheDocument();
  });

  it("still renders a filled dict string from the cell", () => {
    render(
      <ScenarioView
        data={{
          scenario: "Riley",
          scenario_columns: { persona: { column_name: "persona", value: "{'name': 'Helena Rostova'}" } },
          persona_details: PERSONA_DETAILS,
        }}
      />,
    );
    expect(screen.getByText(/Name: Helena/i)).toBeInTheDocument();
  });
});
