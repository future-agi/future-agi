import React from "react";
import { describe, expect, it } from "vitest";
import { FormProvider, useForm } from "react-hook-form";
import { render, screen } from "src/utils/test-utils";

import PersonaBasicInfo from "../PersonaCreateEdit/PersonaBasicInfo";
import {
  getPersonaDefaultValues,
  PersonCreateValidationSchema,
} from "../PersonaCreateEdit/common";

// Values the create persona form submits once Name, Description and Language
// are filled in. Location is left at its empty default unless overridden.
const formValues = (overrides = {}) => ({
  ...getPersonaDefaultValues(),
  simulationType: "voice",
  name: "angry_customer",
  description: "A customer who is angry about the product",
  language: "english",
  ...overrides,
});

describe("Persona create/edit form: Location", () => {
  it("rejects an empty Location with a field error", () => {
    const result = PersonCreateValidationSchema.safeParse(formValues());

    expect(result.success).toBe(false);
    expect(result.error.flatten().fieldErrors.location).toEqual([
      "Location is required",
    ]);
  });

  it("rejects an empty Location for a multilingual persona", () => {
    const result = PersonCreateValidationSchema.safeParse(
      formValues({ multilingual: true, language: ["english"] }),
    );

    expect(result.success).toBe(false);
    expect(result.error.flatten().fieldErrors.location).toEqual([
      "Location is required",
    ]);
  });

  it("still accepts a persona with a Location selected", () => {
    const result = PersonCreateValidationSchema.safeParse(
      formValues({ location: ["India"] }),
    );

    expect(result.success).toBe(true);
    expect(result.data.location).toEqual(["India"]);
  });

  it("marks the Location field as required", () => {
    const BasicInfoForm = () => {
      const form = useForm({ defaultValues: getPersonaDefaultValues() });
      return (
        <FormProvider {...form}>
          <PersonaBasicInfo />
        </FormProvider>
      );
    };

    render(<BasicInfoForm />);

    expect(screen.getByLabelText(/^Location/)).toBeRequired();
  });
});
