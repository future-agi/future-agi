import { describe, expect, it } from "vitest";
import { annotationLabelEventFields } from "../annotationLabelEventFields";

const typedText = "SYNTHETIC_LABEL_NAME_a132";

const label = (type, settings, description = typedText) => ({
  name: typedText,
  type,
  description,
  settings,
});

describe("annotationLabelEventFields", () => {
  it("sends text length settings and whether a placeholder is set", () => {
    expect(
      annotationLabelEventFields(
        label("text", {
          placeholder: typedText,
          minLength: "2",
          maxLength: 20,
        }),
      ),
    ).toEqual({
      type: "text",
      has_description: true,
      has_placeholder: true,
      min_length: 2,
      max_length: 20,
    });
    expect(
      annotationLabelEventFields(
        label("text", { placeholder: "", minLength: "", maxLength: "  " }, " "),
      ),
    ).toEqual({ type: "text", has_description: false });
  });

  it("sends numeric bounds, step and supported display type", () => {
    expect(
      annotationLabelEventFields(
        label("numeric", {
          min: "0",
          max: "10",
          stepSize: "2",
          displayType: "slider",
          placeholder: typedText,
        }),
      ),
    ).toEqual({
      type: "numeric",
      has_description: true,
      min: 0,
      max: 10,
      step_size: 2,
      display_type: "slider",
    });
    expect(
      annotationLabelEventFields(
        label("numeric", {
          min: "",
          max: "not-a-number",
          stepSize: null,
          displayType: typedText,
        }),
      ),
    ).toEqual({ type: "numeric", has_description: true });
  });

  it("sends categorical option count and choice setting", () => {
    expect(
      annotationLabelEventFields(
        label("categorical", {
          options: [
            { label: typedText, value: "person@example.test" },
            { label: typedText },
          ],
          multiChoice: false,
        }),
      ),
    ).toEqual({
      type: "categorical",
      has_description: true,
      option_count: 2,
      multi_choice: false,
    });
    expect(
      annotationLabelEventFields(
        label("categorical", { options: [], multiChoice: true }, ""),
      ),
    ).toEqual({
      type: "categorical",
      has_description: false,
      multi_choice: true,
    });
  });

  it("sends the number of stars for star labels", () => {
    expect(
      annotationLabelEventFields(label("star", { noOfStars: "5" })),
    ).toEqual({
      type: "star",
      has_description: true,
      no_of_stars: 5,
    });
  });

  it("sends the type and description flag for thumbs labels", () => {
    expect(annotationLabelEventFields(label("thumbs_up_down", {}))).toEqual({
      type: "thumbs_up_down",
      has_description: true,
    });
    expect(annotationLabelEventFields({ type: "thumbs_up_down" })).toEqual({
      type: "thumbs_up_down",
      has_description: false,
    });
  });
});
