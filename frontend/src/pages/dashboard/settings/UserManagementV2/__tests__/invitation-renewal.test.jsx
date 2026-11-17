/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ShowActionMenus from "../ShowActionMenus";

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: true, isSuccess: true }),
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("@mui/material", () => ({
  Box: ({ children }) => <div>{children}</div>,
  Popover: ({ children }) => <div>{children}</div>,
  Portal: ({ children }) => <div>{children}</div>,
  Typography: ({ children }) => <span>{children}</span>,
}));

function menu(canResend = true) {
  return render(<ShowActionMenus
    id="synthetic-invitation-menu" open actionRef={{ current: null }}
    onClose={vi.fn()} setOpenActionForm={vi.fn()} canResend={canResend}
    data={{ status: "Expired", type: "invite" }}
  />);
}

describe("Expired invitation renewal", () => {
  it("offers an explicit OSS renewal action instead of hiding resend", () => {
    menu();
    expect(screen.getByText("Renew invitation")).toBeTruthy();
    expect(screen.queryByText("Resend invite")).toBeNull();
  });
  it("still withholds renewal when the viewer cannot resend", () => {
    menu(false);
    expect(screen.queryByText("Renew invitation")).toBeNull();
  });
});
