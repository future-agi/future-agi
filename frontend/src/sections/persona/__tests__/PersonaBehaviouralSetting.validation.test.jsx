import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it } from "vitest";
import { FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { fireEvent, render, screen } from "src/utils/test-utils";

import PersonaBehavioralSetting from "../PersonaCreateEdit/PersonaBehaviouralSetting";
import {
  getPersonaDefaultValues,
  PersonCreateValidationSchema,
} from "../PersonaCreateEdit/common";
import { AGENT_TYPES } from "src/sections/agents/constants";

// Values the create persona form submits once Basic Information is filled
// in. Personality, communication style and accent stay at their empty
// defaults unless overridden.
const formValues = (overrides = {}) => ({
  ...getPersonaDefaultValues(),
  simulationType: AGENT_TYPES.VOICE,
  name: "angry_customer",
  description: "A customer who is angry about the product",
  location: ["India"],
  language: "english",
  ...overrides,
});

const filledBehaviour = {
  personality: [{ value: "Friendly and cooperative" }],
  communicationStyle: ["Direct and concise"],
  accent: ["american"],
};

const fieldErrorsFor = (values) => {
  const result = PersonCreateValidationSchema.safeParse(values);
  expect(result.success).toBe(false);
  return result.error.flatten().fieldErrors;
};

const BehaviouralForm = ({ type }) => {
  const form = useForm({
    defaultValues: formValues({ simulationType: type }),
    resolver: zodResolver(PersonCreateValidationSchema),
  });
  return (
    <FormProvider {...form}>
      <PersonaBehavioralSetting type={type} />
      <button type="button" onClick={form.handleSubmit(() => {})}>
        Save
      </button>
    </FormProvider>
  );
};

BehaviouralForm.propTypes = {
  type: PropTypes.string,
};

describe("Persona create/edit form: Behavioural Settings", () => {
  it("rejects a persona with no personality", () => {
    const errors = fieldErrorsFor(
      formValues({ ...filledBehaviour, personality: [] }),
    );

    expect(errors.personality).toEqual(["Personality is required"]);
    expect(errors.communicationStyle).toBeUndefined();
    expect(errors.accent).toBeUndefined();
  });

  it("rejects a persona with no communication style", () => {
    const errors = fieldErrorsFor(
      formValues({ ...filledBehaviour, communicationStyle: [] }),
    );

    expect(errors.communicationStyle).toEqual([
      "Communication style is required",
    ]);
    expect(errors.personality).toBeUndefined();
  });

  it("rejects a voice persona with no accent", () => {
    const errors = fieldErrorsFor(
      formValues({ ...filledBehaviour, accent: [] }),
    );

    expect(errors.accent).toEqual(["Accent is required"]);
  });

  it("gives each missing field its own error", () => {
    const errors = fieldErrorsFor(formValues());

    expect(errors.personality).toEqual(["Personality is required"]);
    expect(errors.communicationStyle).toEqual([
      "Communication style is required",
    ]);
    expect(errors.accent).toEqual(["Accent is required"]);
  });

  it("does not ask a text persona for an accent", () => {
    const result = PersonCreateValidationSchema.safeParse(
      formValues({
        ...filledBehaviour,
        simulationType: AGENT_TYPES.CHAT,
        accent: [],
      }),
    );

    expect(result.success).toBe(true);
    expect(result.data.accent).toBeNull();
  });

  it("still accepts a voice persona with every behavioural field filled in", () => {
    const result = PersonCreateValidationSchema.safeParse(
      formValues(filledBehaviour),
    );

    expect(result.success).toBe(true);
    expect(result.data.personality).toEqual(["Friendly and cooperative"]);
    expect(result.data.communicationStyle).toEqual(["Direct and concise"]);
    expect(result.data.accent).toEqual(["american"]);
  });

  it("shows each missing field's error next to that field", async () => {
    render(<BehaviouralForm type={AGENT_TYPES.VOICE} />);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("Personality is required"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Communication style is required"),
    ).toBeInTheDocument();
    expect(screen.getByText("Accent is required")).toBeInTheDocument();
  });

  it("shows no accent error on a text persona", async () => {
    render(<BehaviouralForm type={AGENT_TYPES.CHAT} />);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("Personality is required"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Communication style is required"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Accent is required")).not.toBeInTheDocument();
  });
});
