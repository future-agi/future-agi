import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import KeyExpiryStatus, { KeyStatusChip } from "../KeyExpiryStatus";

describe("KeyExpiryStatus", () => {
  it("reads Never for a key without expires_at", () => {
    render(<KeyExpiryStatus row={{ expires_at: null, is_expired: false }} />);

    expect(screen.getByText("Never")).toBeInTheDocument();
  });

  it("shows the expiry date", () => {
    render(
      <KeyExpiryStatus
        row={{ expires_at: "2099-01-15T12:00:00Z", is_expired: false }}
      />,
    );

    expect(screen.getByText("01-15-2099")).toBeInTheDocument();
  });
});

describe("KeyStatusChip", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows nothing for an enabled key that has not expired", () => {
    const { container } = render(
      <KeyStatusChip
        row={{ enabled: true, expires_at: null, is_expired: false }}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("shows Disabled for a key an admin switched off", () => {
    render(
      <KeyStatusChip
        row={{ enabled: false, expires_at: null, is_expired: false }}
      />,
    );

    expect(screen.getByText("Disabled")).toBeInTheDocument();
    expect(screen.queryByText("Expired")).not.toBeInTheDocument();
  });

  it("shows Expired rather than Disabled for an expired key", () => {
    render(
      <KeyStatusChip
        row={{
          enabled: false,
          expires_at: "2026-01-15T12:00:00Z",
          is_expired: true,
        }}
      />,
    );

    expect(screen.getByText("Expired")).toBeInTheDocument();
    expect(screen.queryByText("Disabled")).not.toBeInTheDocument();
  });

  it("trusts the server's is_expired over the local clock", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2099-06-01T00:00:00Z"));

    render(
      <KeyStatusChip
        row={{
          enabled: true,
          expires_at: "2099-01-15T12:00:00Z",
          is_expired: false,
        }}
      />,
    );

    expect(screen.queryByText("Expired")).not.toBeInTheDocument();
  });

  it("falls back to the local clock when is_expired is missing", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2099-06-01T00:00:00Z"));

    render(
      <KeyStatusChip
        row={{ enabled: true, expires_at: "2099-01-15T12:00:00Z" }}
      />,
    );

    expect(screen.getByText("Expired")).toBeInTheDocument();
  });
});
