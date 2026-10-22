// The Model Database tab says what its overrides do, with the price
// precedence; a capability is Default (the model database's value), On or
// Off, and only a stored on or off changes anything; the batch price fields,
// which no gateway path reads, are not offered.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, userEvent } from "src/utils/test-utils";
import ModelDatabaseConfigTab from "./ModelDatabaseConfigTab";

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["gpt-4o"],
}));

const renderTab = (modelDatabase) => {
  const onChange = vi.fn();
  render(
    <ModelDatabaseConfigTab
      modelDatabase={modelDatabase}
      onChange={onChange}
    />,
  );
  return onChange;
};

const lastOverrides = (onChange) =>
  onChange.mock.calls[onChange.mock.calls.length - 1][0].overrides;

describe("ModelDatabaseConfigTab", () => {
  it("states the precedence and what limits, prices and capabilities do", () => {
    renderTab({ overrides: { "gpt-4o": {} } });
    expect(
      screen.getByText(/merged on top of the gateway's model database/),
    ).toHaveTextContent(
      "a Cost Tracking custom price wins over these, and managed models stay on the managed catalog price",
    );
    expect(
      screen.getByText(/one that sets no max_tokens is sent with that limit/),
    ).toHaveTextContent("400 max_tokens_exceeds_limit");
    expect(
      screen.getByText(/Prices calls made with your own provider keys/),
    ).toHaveTextContent(
      "Managed models are always billed at the managed catalog price",
    );
    expect(
      screen.getByText(/Default keeps the model database's value/),
    ).toHaveTextContent(
      "Prompt Caching only changes what /v1/models and /v1/capabilities report",
    );
  });

  it("offers no batch price fields", () => {
    renderTab({
      overrides: { "gpt-4o": { pricing: { batch_input_per_token: 0.000001 } } },
    });
    expect(screen.getByLabelText("Input per Token")).toBeInTheDocument();
    expect(screen.queryByLabelText("Batch Input per Token")).toBeNull();
    expect(screen.queryByLabelText("Batch Output per Token")).toBeNull();
  });

  it("shows an unset capability as Default and stores only on or off", async () => {
    const onChange = renderTab({
      overrides: { "gpt-4o": { capabilities: { vision: false } } },
    });
    expect(
      screen.getByRole("combobox", { name: "Function Calling" }),
    ).toHaveTextContent("Default");
    expect(screen.getByRole("combobox", { name: "Vision" })).toHaveTextContent(
      "Off",
    );

    await userEvent.click(
      screen.getByRole("combobox", { name: "Function Calling" }),
    );
    fireEvent.click(await screen.findByRole("option", { name: "Off" }));
    expect(lastOverrides(onChange)["gpt-4o"].capabilities).toEqual({
      vision: false,
      function_calling: false,
    });

    await userEvent.click(screen.getByRole("combobox", { name: "Vision" }));
    fireEvent.click(await screen.findByRole("option", { name: "Default" }));
    expect(lastOverrides(onChange)["gpt-4o"].capabilities).toEqual({});

    await userEvent.click(screen.getByRole("combobox", { name: "Streaming" }));
    fireEvent.click(await screen.findByRole("option", { name: "On" }));
    expect(lastOverrides(onChange)["gpt-4o"].capabilities).toEqual({
      vision: false,
      streaming: true,
    });
  });
});
