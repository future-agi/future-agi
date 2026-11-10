import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";

import PersonaBehavioralSetting from "../PersonaCreateEdit/PersonaBehaviouralSetting";
import {
  getPersonaDefaultValues,
  PersonCreateValidationSchema,
} from "../PersonaCreateEdit/common";
import { AGENT_TYPES } from "src/sections/agents/constants";

// A voice persona with everything filled in except Personality, which each
// test picks with the keyboard.
const defaultValues = {
  ...getPersonaDefaultValues(),
  simulationType: AGENT_TYPES.VOICE,
  name: "angry_customer",
  description: "A customer who is angry about the product",
  location: ["India"],
  language: "english",
  communicationStyle: ["Direct and concise"],
  accent: ["american"],
};

const KeyboardForm = ({ onSubmit }) => {
  const form = useForm({
    defaultValues,
    resolver: zodResolver(PersonCreateValidationSchema),
  });
  return (
    <FormProvider {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <PersonaBehavioralSetting type={AGENT_TYPES.VOICE} />
        <button type="submit">Save</button>
      </form>
    </FormProvider>
  );
};

KeyboardForm.propTypes = {
  onSubmit: PropTypes.func.isRequired,
};

// Moves focus with Tab only, the way a keyboard user reaches a control.
// Personality comes before the communication style and accent selects, so
// this never focuses them (they open a popover on focus).
const tabTo = async (user, element) => {
  for (let i = 0; i < 50 && document.activeElement !== element; i += 1) {
    await user.tab();
  }
  expect(element).toHaveFocus();
};

const pressSave = async (user) => {
  act(() => {
    screen.getByRole("button", { name: "Save" }).focus();
  });
  await user.keyboard("{Enter}");
};

describe("Persona create/edit form: Personality with the keyboard", () => {
  it("lets a keyboard user pick a personality and save the persona", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<KeyboardForm onSubmit={onSubmit} />);

    const option = screen.getByRole("button", {
      name: /Friendly and cooperative/,
    });
    expect(option).toHaveAttribute("type", "button");
    expect(option).toHaveAttribute("aria-pressed", "false");

    await tabTo(user, option);
    await user.keyboard("{Enter}");

    expect(option).toHaveAttribute("aria-pressed", "true");
    expect(onSubmit).not.toHaveBeenCalled();

    await pressSave(user);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].personality).toEqual([
      "Friendly and cooperative",
    ]);
    expect(
      screen.queryByText("Personality is required"),
    ).not.toBeInTheDocument();
  });

  it("lets a keyboard user unselect a personality with Space", async () => {
    const user = userEvent.setup();
    render(<KeyboardForm onSubmit={vi.fn()} />);

    const option = screen.getByRole("button", { name: /Analytical/ });
    await tabTo(user, option);

    await user.keyboard("[Space]");
    expect(option).toHaveAttribute("aria-pressed", "true");

    await user.keyboard("[Space]");
    expect(option).toHaveAttribute("aria-pressed", "false");
  });

  it("names the personality options as a group and links its error", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<KeyboardForm onSubmit={onSubmit} />);

    const group = screen.getByRole("group", { name: "Select Personality" });
    expect(group).not.toHaveAccessibleDescription(/Personality is required/);

    await pressSave(user);

    expect(
      await screen.findByText("Personality is required"),
    ).toBeInTheDocument();
    expect(group).toHaveAccessibleDescription(/Personality is required/);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
