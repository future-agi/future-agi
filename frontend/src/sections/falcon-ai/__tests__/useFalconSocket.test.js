import { describe, expect, it } from "vitest";
import {
  buildFalconContextPayload,
  buildFalconSocketUrl,
  isFalconSocketPongFresh,
  selectFalconSocketToken,
  shouldReplaceSharedFalconSocket,
} from "../hooks/useFalconSocket";

describe("buildFalconContextPayload", () => {
  it("keeps full route context in auto mode", () => {
    const context = {
      page: "tracing",
      entity_type: "trace",
      entity_id: "trace-1",
      project_id: "project-1",
    };

    expect(buildFalconContextPayload(context, "auto")).toEqual(context);
  });

  it("strips stale entity fields when a context is selected explicitly", () => {
    const context = {
      page: "tracing",
      entity_type: "trace",
      entity_id: "trace-1",
      project_id: "project-1",
    };

    expect(buildFalconContextPayload(context, "datasets")).toEqual({
      page: "datasets",
    });
  });
});

describe("shouldReplaceSharedFalconSocket", () => {
  it("reconnects when workspace becomes known after an initial socket", () => {
    expect(shouldReplaceSharedFalconSocket(null, "workspace-1")).toBe(true);
  });

  it("keeps the socket for the same workspace or missing next workspace", () => {
    expect(shouldReplaceSharedFalconSocket("workspace-1", "workspace-1")).toBe(
      false,
    );
    expect(shouldReplaceSharedFalconSocket("workspace-1", null)).toBe(false);
  });
});

describe("isFalconSocketPongFresh", () => {
  it("requires a recent backend pong before treating the socket as healthy", () => {
    expect(isFalconSocketPongFresh(0, 100000)).toBe(false);
    expect(isFalconSocketPongFresh(60000, 100000)).toBe(true);
    expect(isFalconSocketPongFresh(54000, 100000)).toBe(false);
  });
});

describe("selectFalconSocketToken", () => {
  it("prefers the current stored token over the auth context token", () => {
    expect(selectFalconSocketToken("stored-token", "user-token")).toBe(
      "stored-token",
    );
  });

  it("falls back to the auth context token when storage is empty", () => {
    expect(selectFalconSocketToken(null, "user-token")).toBe("user-token");
  });
});

describe("buildFalconSocketUrl", () => {
  it("builds a direct backend websocket URL by default", () => {
    expect(
      buildFalconSocketUrl({
        hostApi: "http://localhost:8016",
        pageProtocol: "http:",
        pageHost: "localhost:3006",
        sameOrigin: false,
        token: "token-1",
        workspaceId: "workspace-1",
      }),
    ).toBe(
      "ws://localhost:8016/ws/falcon-ai/?token=token-1&workspace_id=workspace-1",
    );
  });

  it("builds a same-origin websocket URL for local dev proxy mode", () => {
    expect(
      buildFalconSocketUrl({
        hostApi: "http://localhost:8016",
        pageProtocol: "http:",
        pageHost: "localhost:3006",
        sameOrigin: true,
        token: "token-1",
        workspaceId: "workspace-1",
      }),
    ).toBe(
      "ws://localhost:3006/ws/falcon-ai/?token=token-1&workspace_id=workspace-1",
    );
  });
});
