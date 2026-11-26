import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SamplingPolicyEditor from "./SamplingPolicyEditor";
import { defaultSamplingPolicy } from "./samplingPolicyForm";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: { project: { samplingPreview: () => "/sample-preview/" } },
}));
// The attribute picker loads project attributes; its own tests cover that.
vi.mock("./sampling_field_picker", async (importOriginal) => ({
  ...(await importOriginal()),
  SamplingFieldPicker: () => <div>Custom ID attribute picker</div>,
}));

function Harness({ policy }) {
  const form = useForm({
    defaultValues: {
      project: "project-id",
      samplingPolicy: policy,
      rowType: "spans",
      runType: policy.mode === "live" ? "continuous" : "historical",
      filters: [],
    },
  });
  return (
    <SamplingPolicyEditor control={form.control} getValues={form.getValues} />
  );
}
Harness.propTypes = { policy: PropTypes.object.isRequired };

const customId = (mode) => ({
  ...defaultSamplingPolicy(),
  mode,
  sampling_unit: "attribute",
  ...(mode === "live"
    ? { window: { start: "2026-10-01T00:00:00Z", end: null } }
    : {}),
});

describe("live Custom ID proof limit", () => {
  it("states the first-admission limit for live Custom ID policies", () => {
    render(<Harness policy={customId("live")} />);
    expect(screen.getByTestId("live-custom-id-limit")).toHaveTextContent(
      "about 20 million spans",
    );
  });

  it("does not show the live-only limit for historical Custom ID policies", () => {
    render(<Harness policy={customId("historical")} />);
    expect(screen.queryByTestId("live-custom-id-limit")).toBeNull();
  });
});
