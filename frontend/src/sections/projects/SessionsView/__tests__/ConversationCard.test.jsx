import { describe, expect, it } from "vitest";
import { CONVERSATION_CARD_CONTENT_SX } from "../ConversationCard";

describe("ConversationCard layout", () => {
  it("caps conversation content to the parent container", () => {
    expect(CONVERSATION_CARD_CONTENT_SX).toMatchObject({
      width: "100%",
      maxWidth: "40vw",
      minWidth: 0,
      overflow: "hidden",
    });
  });
});
