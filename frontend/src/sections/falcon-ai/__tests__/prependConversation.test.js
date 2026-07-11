import { describe, it, expect, beforeEach } from "vitest";
import useFalconStore from "../store/useFalconStore";
import prependConversation from "../helpers/prependConversation";

beforeEach(() => {
  useFalconStore.getState().resetAll();
});

describe("prependConversation (unit)", () => {
  it("prepends the new conversation so a later title_generated finds a row to update", () => {
    useFalconStore.getState().setConversations([{ id: "c1", title: "Old" }]);

    prependConversation({
      id: "c2",
      title: "New chat",
      created_at: "2026-06-09T00:00:00Z",
    });

    const convs = useFalconStore.getState().conversations;
    expect(convs.map((c) => c.id)).toEqual(["c2", "c1"]);
    expect(convs[0].title).toBe("New chat");
  });

  it("falls back to the provided title and a fresh created_at when the API returns none", () => {
    prependConversation({ id: "c3" }, "First 50 chars of message");

    const [conv] = useFalconStore.getState().conversations;
    expect(conv.title).toBe("First 50 chars of message");
    expect(conv.created_at).toBeTruthy();
  });
});
