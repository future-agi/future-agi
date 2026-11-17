import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import SiemExportDrawer from "./SiemExportDrawer";

// The SIEM export drawer (G16-SIEM): an admin's destinations, the signing
// secret shown once, the deployment switch, and dead letters with retry.

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: api.get,
      post: api.post,
      patch: api.patch,
    },
  };
});

const DESTINATIONS = "/agentcc/siem/destinations/";
const DELIVERIES = "/agentcc/siem/deliveries/";

const destination = (overrides = {}) => ({
  id: "6f1c2b8e-9a0d-4f61-8a43-2b9a1d0c5e71",
  name: "SOC webhook",
  kind: "webhook",
  url: "https://siem.example.test/hooks/agentcc",
  streams: ["audit_events"],
  enabled: false,
  auth_header: "",
  has_secret: true,
  secret_hint: "abcd",
  pending_count: 0,
  dead_letter_count: 1,
  ...overrides,
});

const deadLetter = {
  id: "0d7e3f52-1b4c-4e8a-9f60-7c2d5a9b1e33",
  destination_id: destination().id,
  destination_name: "SOC webhook",
  stream: "audit_events",
  event_count: 3,
  status: "dead_letter",
  attempts: 6,
  max_attempts: 6,
  last_response_code: 503,
  last_error: "HTTP 503",
  last_attempt_at: "2026-10-27T10:00:00Z",
  created_at: "2026-10-27T09:00:00Z",
};

function routeGets({
  available = true,
  destinations = [destination()],
  status,
} = {}) {
  api.get.mockImplementation(async (url) => {
    if (url === DESTINATIONS) {
      if (status) {
        const error = new Error("refused");
        error.response = { status, data: { message: "refused" } };
        throw error;
      }
      return {
        data: {
          result: {
            can_manage: true,
            export_available: available,
            destinations,
          },
        },
      };
    }
    if (url === DELIVERIES)
      return { data: { result: { deliveries: [deadLetter] } } };
    throw new Error(`unexpected GET ${url}`);
  });
}

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  api.patch.mockReset();
});

describe("SiemExportDrawer", () => {
  it("does not expose destination controls after a not-found response", async () => {
    routeGets({ status: 404 });
    renderMcp(<SiemExportDrawer open onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("refused");
    expect(screen.queryByText("SOC webhook")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Add destination" }),
    ).not.toBeInTheDocument();
    expect(api.get.mock.calls.some(([url]) => url === DELIVERIES)).toBe(false);
  });

  it("turns a destination on through the API", async () => {
    routeGets();
    api.patch.mockResolvedValue({
      data: { result: destination({ enabled: true }) },
    });
    renderMcp(<SiemExportDrawer open onClose={() => {}} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Turn on SOC webhook" }),
    );
    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith(
        `${DESTINATIONS}${destination().id}/`,
        {
          enabled: true,
        },
      ),
    );
  });

  it("adds a webhook and shows its signing secret once", async () => {
    routeGets({ destinations: [] });
    // Built at runtime: no secret-shaped literal in the source.
    const secret = ["whsec", `${"A".repeat(43)}=`].join("_");
    api.post.mockResolvedValue({
      data: {
        result: { ...destination({ name: "New SOC" }), signing_secret: secret },
      },
    });
    renderMcp(<SiemExportDrawer open onClose={() => {}} />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Name" }), {
      target: { value: "New SOC" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Webhook URL" }), {
      target: { value: "https://siem.example.test/hooks" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add destination" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(DESTINATIONS, {
        name: "New SOC",
        kind: "webhook",
        url: "https://siem.example.test/hooks",
        streams: ["audit_events"],
      }),
    );
    expect(await screen.findByText(secret)).toBeInTheDocument();
    expect(screen.getByText(/it is not shown again/)).toBeInTheDocument();
  });

  it("lists dead letters and retries one", async () => {
    routeGets();
    api.post.mockResolvedValue({
      data: { result: { ...deadLetter, status: "pending" } },
    });
    renderMcp(<SiemExportDrawer open onClose={() => {}} />);
    expect(await screen.findByText(/HTTP 503/)).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(DELIVERIES, {
      params: { status: "dead_letter" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        `${DELIVERIES}${deadLetter.id}/retry/`,
        {},
      ),
    );
  });

  it("tells a member only an owner or admin manages export", async () => {
    routeGets({ status: 403 });
    renderMcp(<SiemExportDrawer open onClose={() => {}} />);
    expect(
      await screen.findByText(
        "Only an organization Owner or Admin can manage SIEM export.",
      ),
    ).toBeInTheDocument();
    expect(api.get.mock.calls.some(([url]) => url === DELIVERIES)).toBe(false);
  });
});
