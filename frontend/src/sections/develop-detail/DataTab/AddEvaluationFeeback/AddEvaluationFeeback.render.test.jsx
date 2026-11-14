/* eslint-disable react/prop-types */
import { describe, it, expect, vi } from "vitest";
import { useForm } from "react-hook-form";
import { render, screen } from "src/utils/test-utils";

// The parent module pulls in the develop-detail zustand stores, which read
// localStorage at creation time (unavailable in this env). We only render the
// presentational FeedBackForm, so stub the store/context it imports.
vi.mock("../../states", () => ({
  useAddEvaluationFeebackStore: () => ({
    addEvaluationFeeback: null,
    setAddEvaluationFeeback: () => {},
  }),
}));
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: () => {} }),
}));

import { FeedBackForm } from "./AddEvaluationFeeback";

// Render the feedback form body with a real react-hook-form control (mirrors
// the Storybook Harness). Pins the headline behavior: multi_choice picks the
// checkbox group, single-choice picks radios.
const Wrapper = ({ feedbackData, data = {} }) => {
  const outputType = feedbackData?.output_type;
  const isMulti =
    outputType === "choices" && Boolean(feedbackData?.multi_choice);
  const { control } = useForm({
    defaultValues: { value: isMulti ? [] : "", explanation: "", actionType: "" },
  });
  return (
    <FeedBackForm
      control={control}
      data={data}
      feedbackData={feedbackData}
      outputType={outputType}
      isMulti={isMulti}
    />
  );
};

describe("FeedBackForm — choice rendering", () => {
  it("renders checkboxes when multi_choice is true", () => {
    render(
      <Wrapper
        feedbackData={{
          output_type: "choices",
          multi_choice: true,
          choices: ["Billing", "Technical"],
        }}
      />,
    );
    expect(screen.getByRole("checkbox", { name: "Billing" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Technical" })).toBeTruthy();
    // not radios
    expect(screen.queryByRole("radio", { name: "Billing" })).toBeNull();
  });

  it("renders radios when multi_choice is false", () => {
    render(
      <Wrapper
        feedbackData={{
          output_type: "choices",
          multi_choice: false,
          choices: ["A", "B"],
        }}
      />,
    );
    expect(screen.getByRole("radio", { name: "A" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "B" })).toBeTruthy();
    // not checkboxes
    expect(screen.queryByRole("checkbox", { name: "A" })).toBeNull();
  });

  it("renders Passed/Failed radios for a Pass/Fail eval", () => {
    render(
      <Wrapper
        feedbackData={{
          output_type: "Pass/Fail",
          choices: ["Passed", "Failed"],
          choice_scores: null,
        }}
      />,
    );
    expect(screen.getByRole("radio", { name: "Passed" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Failed" })).toBeTruthy();
  });

  it("renders checkboxes with score annotations when choice_scores + multi_choice", () => {
    render(
      <Wrapper
        feedbackData={{
          output_type: "choices",
          multi_choice: true,
          choices: ["Polite", "Helpful"],
          choice_scores: { Polite: 1, Helpful: 1, Toxic: 0 },
        }}
      />,
    );
    expect(
      screen.getByRole("checkbox", { name: "Polite (score 1)" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("checkbox", { name: "Toxic (score 0)" }),
    ).toBeTruthy();
    // not radios
    expect(
      screen.queryByRole("radio", { name: "Polite (score 1)" }),
    ).toBeNull();
  });

  it("renders radios with score annotations when choice_scores is set and multi_choice is false", () => {
    render(
      <Wrapper
        feedbackData={{
          output_type: "score",
          multi_choice: false,
          choice_scores: { Yes: 1, No: 0 },
        }}
      />,
    );
    expect(screen.getByRole("radio", { name: "Yes (score 1)" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "No (score 0)" })).toBeTruthy();
  });
});
