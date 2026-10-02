import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import EmailAlertDialog from "./EmailAlertDialog";

const mockCreateMutate = vi.fn();
const mockUpdateMutate = vi.fn();
const mockTestMutate = vi.fn();

vi.mock("./hooks/useEmailAlerts", () => ({
  useCreateEmailAlert: () => ({ mutate: mockCreateMutate, isPending: false }),
  useUpdateEmailAlert: () => ({ mutate: mockUpdateMutate, isPending: false }),
  useTestEmailAlert: () => ({ mutate: mockTestMutate, isPending: false }),
}));

describe("EmailAlertDialog", () => {
  beforeEach(() => {
    mockCreateMutate.mockReset();
    mockUpdateMutate.mockReset();
    mockTestMutate.mockReset();
  });

  it("prefills edit form values returned by the snake_case email alert API", async () => {
    const user = userEvent.setup();

    render(
      <EmailAlertDialog
        open
        onClose={vi.fn()}
        alert={{
          id: "alert-1",
          name: "Browser settings alert",
          recipients: ["ops@example.com"],
          events: ["budget.exceeded"],
          provider: "smtp",
          provider_config: {
            host: "smtp.example.com",
            port: 2525,
            username: "ops-user",
            from_email: "alerts@example.com",
          },
          cooldown_minutes: 15,
          is_active: false,
        }}
      />,
    );

    expect(screen.getByText("Edit Email Alert")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("Browser settings alert"),
    ).toBeInTheDocument();
    expect(screen.getByText("ops@example.com")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Budget Exceeded" }),
    ).toBeChecked();
    expect(await screen.findByLabelText(/SMTP Host/)).toHaveValue(
      "smtp.example.com",
    );
    expect(screen.getByLabelText("SMTP Port")).toHaveValue(2525);
    expect(screen.getByLabelText("Username")).toHaveValue("ops-user");
    expect(screen.getByLabelText("From Email")).toHaveValue(
      "alerts@example.com",
    );
    expect(screen.getByLabelText("Cooldown (minutes)")).toHaveValue(15);
    expect(
      screen.getByRole("checkbox", { name: "Alert is active" }),
    ).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Update" }));

    expect(mockUpdateMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "alert-1",
        provider: "smtp",
        cooldown_minutes: 15,
        provider_config: expect.objectContaining({
          host: "smtp.example.com",
          port: 2525,
          from_email: "alerts@example.com",
          username: "ops-user",
        }),
      }),
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
  });
});
