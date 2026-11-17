import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import LinearCredentials from "../AddIntegrationWizard/credentials/LinearCredentials";

// Connecting Linear creates the connection right after the key is checked;
// when the create is refused (Linear already connected in this workspace),
// the step says why instead of staying silent.

const h = vi.hoisted(() => ({ createError: null }));

vi.mock("src/api/integrations", () => ({
  useValidateCredentials: () => ({
    mutate: vi.fn(),
    isPending: false,
    error: null,
  }),
  useCreateConnection: () => ({
    mutate: vi.fn(),
    isPending: false,
    error: h.createError,
  }),
}));

describe("Connect Linear", () => {
  it("shows why its connection was not created", () => {
    h.createError = {
      response: {
        data: {
          result:
            "Linear is already connected for this workspace. Disconnect it in Settings > Integrations to connect another key.",
        },
      },
    };
    render(
      <LinearCredentials
        data={{ credentials: {} }}
        onUpdate={vi.fn()}
        onNext={vi.fn()}
        onBack={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );
    expect(
      screen.getByText(
        "Linear is already connected for this workspace. Disconnect it in Settings > Integrations to connect another key.",
      ),
    ).toBeInTheDocument();
  });
});
