import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it } from "vitest";
import { useForm } from "react-hook-form";
import { render, screen, fireEvent } from "src/utils/test-utils";
import AgentBehaviourStepRightSection from "../CreateNewAgent/AgentBehaviourStep/AgentBehaviourStepRightSection";
import KeyConfiguration from "../AgentConfiguration/KeyConfiguration";

function Review({ values }) {
  const { control, getValues } = useForm({ defaultValues: values });
  return (
    <>
      <AgentBehaviourStepRightSection control={control} />
      <button onClick={() => values.onSubmit?.(getValues())}>
        Submit unchanged
      </button>
    </>
  );
}

Review.propTypes = {
  values: PropTypes.shape({ onSubmit: PropTypes.func }).isRequired,
};

const secret = "synthetic-provider-review-only";
const defaults = {
  agentType: "voice",
  agentName: "Synthetic support agent",
  authenticationMethod: "api_key",
  assistantId: "synthetic-assistant",
  apiKey: secret,
  languages: ["en"],
};

describe("provider credential review and readback", () => {
  it.each(["vapi", "retell", "elevenlabs", "bland"])(
    "never renders the %s provider key in the review DOM",
    (provider) => {
      let submitted;
      render(
        <Review
          values={{
            ...defaults,
            provider,
            onSubmit: (value) => {
              submitted = value;
            },
          }}
        />,
      );
      expect(screen.getByText("Provider API Key")).toBeVisible();
      expect(
        screen.getByText("Provider API Key").parentElement,
      ).toHaveTextContent("••••••");
      expect(document.body.innerHTML).not.toContain(secret);
      expect(screen.getByText(defaults.assistantId)).toBeVisible();
      fireEvent.click(screen.getByRole("button", { name: "Submit unchanged" }));
      expect(submitted.apiKey).toBe(secret);
    },
  );

  it.each(["x", "short", secret])(
    "masks a key regardless of length (%s)",
    (apiKey) => {
      render(<Review values={{ ...defaults, provider: "vapi", apiKey }} />);
      const row = screen.getByText("Provider API Key").parentElement;
      expect(row.lastElementChild).toHaveTextContent(/^••••••$/);
    },
  );

  it("keeps an absent key absent, rather than implying a configured key", () => {
    render(<Review values={{ ...defaults, provider: "vapi", apiKey: "" }} />);
    expect(
      screen.getByText("Provider API Key").parentElement.lastElementChild,
    ).toHaveTextContent(/^-$/);
  });

  it.each(["livekit", "livekit_bridge"])(
    "masks %s review keys without rendering prefixes",
    (provider) => {
      render(
        <Review values={{ ...defaults, provider, livekitApiKey: secret }} />,
      );
      const row = screen.getByText("LiveKit API Key").parentElement;
      expect(row.lastElementChild).toHaveTextContent(/^••••••$/);
      expect(document.body.innerHTML).not.toContain(secret);
    },
  );

  it("masks the API-key review for chat agents as well", () => {
    render(
      <Review values={{ ...defaults, agentType: "text", provider: null }} />,
    );
    expect(
      screen.getByText("Provider API Key").parentElement,
    ).toHaveTextContent("••••••");
    expect(document.body.innerHTML).not.toContain(secret);
  });

  it("masks readback in the update-keys form while retaining the submitted value", () => {
    let submitted;
    function UpdateKeys() {
      const { control, getValues } = useForm({
        defaultValues: { apiKey: secret, assistantId: defaults.assistantId },
      });
      return (
        <>
          <KeyConfiguration control={control} errors={{}} required />
          <button
            onClick={() => {
              submitted = getValues();
            }}
          >
            Save keys
          </button>
        </>
      );
    }
    render(<UpdateKeys />);
    const input = screen.getByLabelText(/^API Key/);
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveValue(secret);
    fireEvent.change(input, { target: { value: "synthetic-replacement" } });
    fireEvent.click(screen.getByRole("button", { name: "Save keys" }));
    expect(submitted.apiKey).toBe("synthetic-replacement");
  });
});
