import { afterEach, expect, it, vi } from "vitest";
import { prewarmSocket } from "../clusterAnalyzeSocket";

vi.mock("src/config-global", () => ({
  HOST_API: "http://api.example.test/",
  CURRENT_ENVIRONMENT: "local",
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconAPI", () => ({
  createConversation: vi.fn(),
}));

afterEach(() => vi.unstubAllGlobals());

it("prewarms the Falcon handshake in the selected workspace", () => {
  const socket = vi.fn(function () {});
  vi.stubGlobal("WebSocket", socket);

  prewarmSocket({ token: "padded==", workspaceId: "ws/1" });

  expect(socket).toHaveBeenCalledWith(
    "ws://api.example.test/ws/falcon-ai/?workspace_id=ws%2F1",
    ["fi.auth.v1", "bearer.padded%3D%3D"],
  );
});
