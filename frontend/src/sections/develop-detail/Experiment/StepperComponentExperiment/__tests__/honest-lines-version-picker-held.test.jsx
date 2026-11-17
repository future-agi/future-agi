// Honest lines row 21: New experiment › Data step › Dataset version. The
// versions read is refused in this build (absent-path guard), so the request
// is never sent. The picker must be disabled and say "Not available in this
// release" rather than sit blank / "Loading versions…" forever.
import React from "react";
import PropTypes from "prop-types";
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { emptyExperimentValues } from "src/sections/develop-detail/Experiment/experimentFormValues";
import DataStep from "src/sections/develop-detail/Experiment/StepperComponentExperiment/DataStep";

const NOT_IN_RELEASE = "Not available in this release";

const h = vi.hoisted(() => ({ versions: [], versionsHeld: true }));

// Same module mocks as launch-wizard-settings.test.jsx (keeps real r2/axios
// readers out of the tree).
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepLLM",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepTTS",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepSTT",
  () => ({ default: () => null }),
);
vi.mock("src/sections/develop-detail/Experiment/StartFromExperiment", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ReadsOnSampleTable",
  () => ({ default: () => null }),
);
vi.mock("src/sections/develop-detail/Experiment/VariableMappingTable", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows", dataset: null, isLoading: false }),
}));
vi.mock("src/sections/develop-detail/Experiment/useVersionScope", () => ({
  useVersionSplitCounts: () => ({}),
}));
vi.mock(
  "src/sections/develop-detail/DataTab/Versions/useUnsavedSelection",
  () => ({ useUnsavedSelection: () => ({ sentence: null }) }),
);
vi.mock("src/sections/develop-detail/DataTab/Versions/selectedSample", () => ({
  useSelectedSampleContext: () => ({
    datasetId: "d1",
    versions: h.versions,
    versionsHeld: h.versionsHeld,
    splits: [],
    exampleCount: 3,
    rowsInScope: 3,
    rows: [],
    sampleRow: null,
    sampleRowLabel: "Row 1",
    setSampleRowId: () => {},
    userColumns: [{ id: "c1", name: "question" }],
    mappingColumns: [],
  }),
}));

function Host({ children }) {
  const form = useForm({ defaultValues: emptyExperimentValues() });
  return children(form);
}
Host.propTypes = { children: PropTypes.func };

const dataStep = () =>
  render(
    <Host>{(f) => <DataStep control={f.control} setValue={f.setValue} />}</Host>,
  );

afterEach(() => {
  h.versions = [];
  h.versionsHeld = true;
});

describe("Dataset version picker when versions are not in this release", () => {
  it("is disabled and says Not available in this release, never Loading versions…", () => {
    dataStep();
    const picker = screen.getByTestId("experiment-version-select");
    expect(picker.className).toMatch(/Mui-disabled/);
    expect(picker.textContent).toContain(NOT_IN_RELEASE);
    expect(screen.queryByText(/Loading versions/)).toBeNull();
  });

  it("stays enabled and lists the versions when the read is allowed", () => {
    h.versionsHeld = false;
    h.versions = [{ id: "v1", number: 1, row_count: 3 }];
    dataStep();
    const picker = screen.getByTestId("experiment-version-select");
    expect(picker.className).not.toMatch(/Mui-disabled/);
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
  });
});
