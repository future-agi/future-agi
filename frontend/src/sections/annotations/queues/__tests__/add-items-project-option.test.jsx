import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { renderProjectAutocompleteOption } from "../items/add-items-dialog";

const renderOption = (option) =>
  render(
    <ul>
      {renderProjectAutocompleteOption({ key: option.id }, option, {
        index: 0,
      })}
    </ul>,
  );

// The project picker lists every tracing project. Projects whose traces come
// from an experiment run (trace_type "experiment") carry the Experiment tag,
// the name the Experiment flow uses for them; this build has no Prototype page.
describe("Add items: project picker option", () => {
  it("tags an experiment-type project as Experiment", () => {
    renderOption({ id: "p1", name: "support-agent", trace_type: "experiment" });

    expect(screen.getByText("support-agent")).toBeInTheDocument();
    expect(screen.getByText("Experiment")).toBeInTheDocument();
    expect(screen.queryByText(/prototype/i)).not.toBeInTheDocument();
  });

  it("does not tag an Observe project", () => {
    renderOption({ id: "p2", name: "checkout-bot", trace_type: "observe" });

    expect(screen.getByText("checkout-bot")).toBeInTheDocument();
    expect(screen.queryByText("Experiment")).not.toBeInTheDocument();
  });
});
