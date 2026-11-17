import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ProviderDetailDrawer from "./ProviderDetailDrawer";

// R-LIT: literal synthetic provider rows; example.com hosts never receive requests.
// Retarget dev's deleted ProviderConfigView suite to its console-v2 replacement.
const PROVIDER_ROWS = [
  {
    name: "typesafe",
    baseUrl: "https://api.typesafe.ai",
    apiFormat: "systemone",
    models: ["jev-1.13.0"],
    expected: "System One (decision models)",
  },
  {
    name: "custom-systemone",
    baseUrl: "https://decider.example.com",
    apiFormat: "typesafe",
    models: ["strands-decider"],
    expected: "System One (decision models)",
  },
  {
    name: "openai",
    baseUrl: "https://api.openai.com/v1",
    apiFormat: "openai",
    models: [],
    expected: "openai",
  },
  {
    name: "legacy",
    baseUrl: "https://legacy.example.com",
    apiFormat: undefined,
    models: [],
    expected: "—",
  },
];

describe("ProviderConfigView replacement API format", () => {
  it.each(PROVIDER_ROWS)(
    "labels $name without changing its wire format",
    (row) => {
      render(
        <ProviderDetailDrawer
          row={{ ...row, id: row.name, displayName: row.name }}
          onClose={vi.fn()}
          onEdit={vi.fn()}
          onRemove={vi.fn()}
          canWrite={false}
          orgProviders={[]}
        />,
      );

      expect(
        screen.getByText("API format").nextElementSibling,
      ).toHaveTextContent(row.expected);
      expect(
        screen.getByText("Stored encrypted. Never shown again."),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Remove" })).toBeDisabled();
    },
  );
});
