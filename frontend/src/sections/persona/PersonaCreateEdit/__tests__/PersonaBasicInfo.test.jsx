/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it } from "vitest";
import { FormProvider, useForm } from "react-hook-form";
import { render, screen } from "src/utils/test-utils";
import PersonaBasicInfo from "../PersonaBasicInfo";

const Wrapper = ({ children }) => {
  const methods = useForm({
    defaultValues: {
      name: "",
      description: "",
      gender: [],
      ageGroup: [],
      location: [],
      profession: [],
    },
  });
  return <FormProvider {...methods}>{children}</FormProvider>;
};

const fieldLabel = (text) =>
  screen.getByText(new RegExp(`^${text}`), { selector: "label" });

describe("PersonaBasicInfo required fields", () => {
  it("marks the Location field as required", () => {
    render(
      <Wrapper>
        <PersonaBasicInfo viewOptions={{}} />
      </Wrapper>,
    );

    // The bug left Location optional; the fix adds `required`, which MUI
    // renders as an asterisk on the field label.
    expect(
      fieldLabel("Location").querySelector(".MuiFormLabel-asterisk"),
    ).toBeInTheDocument();
  });

  it("leaves genuinely optional sibling fields (Gender) unmarked", () => {
    render(
      <Wrapper>
        <PersonaBasicInfo viewOptions={{}} />
      </Wrapper>,
    );

    expect(
      fieldLabel("Gender").querySelector(".MuiFormLabel-asterisk"),
    ).not.toBeInTheDocument();
  });
});
