// A long provider display name may be cut in the Provider column; the full
// name and the provider id are then on hover (and in the drawer).
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import ProviderList from "../ProviderList";

vi.mock("ag-grid-react", async () => await import("./grid-mock"));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/hooks/use-responsive", () => ({ useResponsive: () => false }));
vi.mock("../hooks/useGatewayConfig", () => ({
  isSavedButNotApplied: () => false,
  useRemoveProvider: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../AddProviderDialog", () => ({ default: () => null }));
vi.mock("../ProviderDetailDrawer", () => ({ default: () => null }));

const LONG_NAME = "Bedrock-style aws_session_token null field provider";

const row = {
  id: "bedrock-carried",
  name: "bedrock-carried",
  displayName: LONG_NAME,
  status: "healthy",
  baseUrl: "https://bedrock-runtime.us-east-1.amazonaws.com",
  apiFormat: "bedrock",
  models: ["anthropic.claude-3-haiku"],
  config: {},
};

describe("Providers list: a long display name", () => {
  it("puts the full display name and the provider id on hover", () => {
    render(
      <ProviderList
        rows={[row]}
        loading={false}
        gatewayId="default"
        canWrite
      />,
    );
    expect(screen.getByText(LONG_NAME)).toHaveAttribute("title", LONG_NAME);
    expect(screen.getByText("bedrock-carried")).toHaveAttribute(
      "title",
      "bedrock-carried",
    );
  });
});
