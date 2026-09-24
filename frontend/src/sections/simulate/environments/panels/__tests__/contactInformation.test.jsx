import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent } from "@testing-library/react";

import { render } from "src/utils/test-utils";
import ContactInformation from "../ContactInformation";

const base = {
  mode: "web",
  onMode: vi.fn(),
  countryIso: "US",
  onCountryIso: vi.fn(),
  contactNumber: "",
  onContactNumber: vi.fn(),
  inboundCalls: true,
  onInboundCalls: vi.fn(),
  agentSpeaksFirst: false,
  onAgentSpeaksFirst: vi.fn(),
};

describe("ContactInformation", () => {
  it("web mode: shows the WebRTC header + Agent speaks first, hides the phone fields", () => {
    render(<ContactInformation {...base} mode="web" />);
    expect(screen.getByText("Web simulation (WebRTC)")).toBeInTheDocument();
    expect(screen.getByText("Agent speaks first")).toBeInTheDocument();
    expect(screen.queryByText("Country Code")).toBeNull();
    expect(screen.queryByText("Contact Number")).toBeNull();
    expect(screen.queryByText("Inbound Calls")).toBeNull();
  });

  it("phone mode: shows the PSTN header, Country Code, Contact Number and Inbound Calls", () => {
    render(<ContactInformation {...base} mode="phone" />);
    expect(screen.getByText("Telephony simulation (PSTN)")).toBeInTheDocument();
    expect(screen.getByText("Country Code")).toBeInTheDocument();
    expect(screen.getByText("Contact Number")).toBeInTheDocument();
    expect(screen.getByText("Inbound Calls")).toBeInTheDocument();
  });

  it("phone mode: Inbound Calls is locked on, even when the draft says off", () => {
    render(<ContactInformation {...base} mode="phone" inboundCalls={false} />);
    const inbound = screen.getByRole("checkbox", { name: "Inbound Calls" });
    expect(inbound).toBeChecked();
    expect(inbound).toBeDisabled();
    // Agent speaks first stays a free choice.
    expect(screen.getByRole("checkbox", { name: "Agent speaks first" })).toBeEnabled();
  });

  it("phoneOnly: no mode header, but the phone fields are shown", () => {
    render(<ContactInformation {...base} mode="web" phoneOnly />);
    expect(screen.queryByText("Web simulation (WebRTC)")).toBeNull();
    expect(screen.queryByText("Telephony simulation (PSTN)")).toBeNull();
    expect(screen.getByText("Country Code")).toBeInTheDocument();
    expect(screen.getByText("Contact Number")).toBeInTheDocument();
  });

  it("fires onMode when the Phone pill is clicked", () => {
    const onMode = vi.fn();
    render(<ContactInformation {...base} onMode={onMode} />);
    fireEvent.click(screen.getByText("Phone"));
    expect(onMode).toHaveBeenCalledWith("phone");
  });
});
