// v2.1 g8 (translate:field-or-cell): Translate cell… is offered only on
// text that is not English.
import { describe, expect, it } from "vitest";
import { canTranslateCell, looksEnglish } from "../fieldCollabModel";

const col = { dataType: "text", col: { is_user_data: true } };

describe("canTranslateCell", () => {
  it("not on English text", () => {
    expect(looksEnglish("Hi, my order A-3920 was due Monday.")).toBe(true);
    expect(canTranslateCell(col, "Where is my order?")).toBe(false);
  });
  it("on Spanish, German, French, Hindi and Hinglish text", () => {
    expect(
      canTranslateCell(col, "Hola, mi pedido no ha llegado todavía."),
    ).toBe(true);
    expect(canTranslateCell(col, "Hallo, meine Bestellung ist nicht da.")).toBe(
      true,
    );
    expect(canTranslateCell(col, "Bonjour, ma commande est en retard.")).toBe(
      true,
    );
    expect(canTranslateCell(col, "मेरा ऑर्डर कहाँ है?")).toBe(true);
    expect(
      canTranslateCell(
        col,
        "Mera order kab aayega? Kya aap check kar sakte hai",
      ),
    ).toBe(true);
  });
  it("never on empty or non-text cells", () => {
    expect(canTranslateCell(col, "  ")).toBe(false);
    expect(canTranslateCell({ dataType: "integer" }, "Hola mi pedido")).toBe(
      false,
    );
  });
});
