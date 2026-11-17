// Settings > General shows the gateway Endpoint the Overview shows: the
// confirmed public address, else the placeholder with a note. The address
// the gateway reports stays on the page, with its Copy, under its own name,
// when it is not that Endpoint.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import GeneralTab from "../GeneralTab";
import { PLACEHOLDER_BASE_URL } from "../../utils/snippetBaseUrl";

vi.mock("../../shell/page-status", () => ({
  isGatewayControlHidden: () => true,
}));

const REPORTED = "Address the gateway reports";

const renderTab = (baseUrl, mode) =>
  render(
    <GeneralTab
      gateway={{ name: "default", baseUrl, status: "healthy" }}
      config={{}}
      health={null}
      {...mode}
    />,
  );
const identity = () => screen.getByTestId("gateway-identity");
// The value cell of a label/value row.
const valueOf = (label) =>
  within(identity()).getByText(label, { selector: "dt" }).nextElementSibling;

describe("Settings > General: the Endpoint", () => {
  it("shows the placeholder as the Endpoint and keeps a loopback address under its own name", () => {
    renderTab("http://localhost:8090/v1", {
      hosted: false,
      deploymentModeConfirmed: true,
    });
    const endpoint = valueOf("Endpoint");
    expect(
      within(endpoint).getByText(PLACEHOLDER_BASE_URL),
    ).toBeInTheDocument();
    expect(endpoint).not.toHaveTextContent("localhost");
    expect(
      within(endpoint).getByText(/^This is a placeholder\./),
    ).toBeVisible();
    expect(
      within(identity()).queryByRole("button", { name: "Copy endpoint" }),
    ).toBeNull();

    const reported = valueOf(REPORTED);
    expect(
      within(reported).getByText("http://localhost:8090/v1"),
    ).toBeInTheDocument();
    expect(
      within(reported).getByRole("button", { name: "Copy reported address" }),
    ).toBeInTheDocument();
    expect(reported).toHaveTextContent(
      "Not confirmed reachable from outside this network. Applications should call the endpoint above.",
    );
  });

  it("shows a confirmed public address once, with Copy, as the Overview does", () => {
    renderTab("https://gateway.example.com/v1/", {
      hosted: false,
      deploymentModeConfirmed: true,
    });
    const endpoint = valueOf("Endpoint");
    expect(
      within(endpoint).getByText("https://gateway.example.com/v1"),
    ).toBeInTheDocument();
    expect(
      within(endpoint).getByRole("button", { name: "Copy endpoint" }),
    ).toBeInTheDocument();
    expect(within(identity()).queryByText(REPORTED)).toBeNull();
  });

  it("shows the placeholder while the deployment mode is not confirmed, with the reported address under its own name", () => {
    renderTab("https://gateway.example.com/v1", {
      deploymentModeConfirmed: false,
    });
    const endpoint = valueOf("Endpoint");
    expect(
      within(endpoint).getByText(PLACEHOLDER_BASE_URL),
    ).toBeInTheDocument();
    expect(endpoint).toHaveTextContent(
      "isn't available from the console right now",
    );
    expect(valueOf(REPORTED)).toHaveTextContent(
      "https://gateway.example.com/v1",
    );
  });
});
