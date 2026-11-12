// OK-B (R1-06): the dataset evaluation configuration judge-model menu.
// RED on base 651ab5bb: ModelSelectInput.jsx:97-107 admits a model only when
// config.apiKeyAvailable (an OpenAI-key check, develop_dataset.py:8308) is
// true, so a managed-eligible eval opens provider-key setup, never names the
// payer, and a contract "unavailable" eval can still be picked. The two
// pre-contract cases are BYOK regression guards (pass on base and after).
//
// The cut line must show
// for the shape the real develop_dataset emitter sends (apiKeyAvailable and
// no one-key fields). RED on the r1 bytes, which showed it only for a
// structure-wide credential_mode that emitter never sends.
import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { useForm, useWatch } from "react-hook-form";
import { fireEvent, render, screen } from "src/utils/test-utils";

// Keep the real eligibility adapter without importing the application's HTTP
// client (and its unrelated generated-contract initialization).
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));

// Provider-key dialog stub: reports only whether it was opened.
vi.mock(
  "src/sections/develop-detail/Common/ConfigureKeys/ConfigureKeys",
  () => ({
    default: ({ open }) =>
      open ? <div data-testid="provider-setup-dialog" /> : null,
  }),
);

import ModelSelectInput from "src/sections/develop-detail/Common/EvaluationConfigure/ModelSelectInput";
import { MANAGED_PAYER_LABEL } from "./modelListContractFixtures";

const MODELS = ["gpt-5.5-mini", "claude-sonnet-5"];

function Harness({ config }) {
  const { control } = useForm({
    defaultValues: { config: { config: { model: MODELS[0] } } },
  });
  const picked = useWatch({ control, name: "config.config.model" });
  return (
    <>
      <ModelSelectInput control={control} configKey="model" config={config} />
      <output data-testid="picked">{picked ?? ""}</output>
    </>
  );
}
Harness.propTypes = { config: PropTypes.object };

// Eval structures arrive camelCased (requiredKeys, configParamsOption, ...).
const evalConfig = (extra) => ({
  configParamsOption: { model: MODELS },
  ...extra,
});

const openMenu = () => fireEvent.mouseDown(screen.getByRole("combobox"));
const CUT_LINE =
  "Future AGI credits do not run a judge from this menu. Add your own provider key, or use the evaluator model picker.";
// Two MUI modals open together (the Select's own hidden menu, whose items are
// role "option", and the model Popover), so aria-hidden bookkeeping must not
// decide whether the Popover's menu items are found.
const optionRow = (name) =>
  screen
    .getAllByRole("menuitem", { hidden: true })
    .find((item) => item.textContent.startsWith(name));

describe("ModelSelectInput: managed-or-BYOK judge model", () => {
  it("lets a managed-eligible eval pick its judge model without provider-key setup", () => {
    render(
      <Harness
        config={evalConfig({
          apiKeyAvailable: false,
          credentialMode: "managed",
          payer: "future_agi_credits",
        })}
      />,
    );

    openMenu();
    fireEvent.click(optionRow("claude-sonnet-5"));

    expect(screen.queryByTestId("provider-setup-dialog")).toBeNull();
    expect(screen.getByTestId("picked")).toHaveTextContent("gpt-5.5-mini");
  });

  it("names Future AGI credits as the payer of a managed judge", () => {
    render(
      <Harness
        config={evalConfig({
          apiKeyAvailable: false,
          credentialMode: "managed",
          payer: "future_agi_credits",
        })}
      />,
    );

    openMenu();
    expect(screen.getByText(CUT_LINE)).toBeInTheDocument();
    expect(screen.queryByText(/ yet\b/)).toBeNull();

    expect(optionRow("gpt-5.5-mini")).not.toHaveTextContent(MANAGED_PAYER_LABEL);
    expect(optionRow("claude-sonnet-5")).not.toHaveTextContent(MANAGED_PAYER_LABEL);
  });

  it("accepts the snake_case contract fields too", () => {
    render(
      <Harness
        config={evalConfig({
          apiKeyAvailable: false,
          credential_mode: "managed",
          payer: "future_agi_credits",
        })}
      />,
    );

    openMenu();
    fireEvent.click(optionRow("claude-sonnet-5"));

    expect(screen.queryByTestId("provider-setup-dialog")).toBeNull();
    expect(screen.getByTestId("picked")).toHaveTextContent("gpt-5.5-mini");
  });

  it("fails closed when the contract marks the judge unavailable", () => {
    render(
      <Harness
        config={evalConfig({
          apiKeyAvailable: true,
          credentialMode: "unavailable",
          payer: null,
        })}
      />,
    );

    openMenu();
    fireEvent.click(optionRow("claude-sonnet-5"));

    expect(screen.getByTestId("provider-setup-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("picked")).toHaveTextContent("gpt-5.5-mini");
  });
});

describe("ModelSelectInput: BYOK and pre-contract evals are unchanged", () => {
  it("keeps provider-key setup for a pre-contract eval without a stored key", () => {
    render(<Harness config={evalConfig({ apiKeyAvailable: false })} />);

    openMenu();
    fireEvent.click(optionRow("claude-sonnet-5"));

    expect(screen.getByTestId("provider-setup-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("picked")).toHaveTextContent("gpt-5.5-mini");
    expect(screen.queryByText(MANAGED_PAYER_LABEL)).toBeNull();
  });

  it("selects normally for a pre-contract eval with a stored key", () => {
    render(<Harness config={evalConfig({ apiKeyAvailable: true })} />);

    openMenu();
    expect(optionRow("claude-sonnet-5").textContent).toBe("claude-sonnet-5");
    fireEvent.click(optionRow("claude-sonnet-5"));

    expect(screen.queryByTestId("provider-setup-dialog")).toBeNull();
    expect(screen.getByTestId("picked")).toHaveTextContent("claude-sonnet-5");
  });
});

describe("ModelSelectInput: the eval-config credits cut is disclosed", () => {
  it("shows the cut line for the real legacy emitter shape without a stored key", () => {
    render(<Harness config={evalConfig({ apiKeyAvailable: false })} />);

    expect(screen.getByText(CUT_LINE)).toBeInTheDocument();

    openMenu();
    fireEvent.click(optionRow("claude-sonnet-5"));
    expect(screen.getByTestId("picked")).toHaveTextContent("gpt-5.5-mini");
  });

  it("shows the cut line when the legacy key check is false and the contract mode is byok", () => {
    render(
      <Harness
        config={evalConfig({
          apiKeyAvailable: false,
          credentialMode: "byok",
          payer: "own_provider_key",
        })}
      />,
    );

    expect(screen.getByText(CUT_LINE)).toBeInTheDocument();
  });

  it("does not show the cut line when the legacy key check passes", () => {
    render(<Harness config={evalConfig({ apiKeyAvailable: true })} />);

    expect(screen.queryByText(CUT_LINE)).toBeNull();
  });
});
