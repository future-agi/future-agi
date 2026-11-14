import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const MARK = "SYNTHETIC-LINK-CODE-6b21";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));

import AuthClassicLayout from "../classic";

const renderAt = (path) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthClassicLayout>
        <div />
      </AuthClassicLayout>
    </MemoryRouter>,
  );

describe("auth layout page view", () => {
  beforeEach(() => {
    mocks.mixpanel.track.mockClear();
  });

  it.each([
    [`/auth/jwt/verify/user-1/${MARK}`, "/auth/jwt/verify/:uuid/:token"],
    [
      `/auth/jwt/invitation/accept/user-1/${MARK}`,
      "/auth/jwt/invitation/accept/:uuid/:token",
    ],
  ])("reports the route pattern for %s", (path, pattern) => {
    renderAt(path);

    expect(mocks.mixpanel.track).toHaveBeenCalledTimes(1);
    expect(mocks.mixpanel.track.mock.calls[0][1]).toEqual(
      expect.objectContaining({ path: pattern }),
    );
    expect(JSON.stringify(mocks.mixpanel.track.mock.calls)).not.toContain(MARK);
  });

  it("reports other auth paths as they are", () => {
    renderAt("/auth/jwt/login");

    expect(mocks.mixpanel.track.mock.calls[0][1]).toEqual(
      expect.objectContaining({ path: "/auth/jwt/login" }),
    );
  });
});
