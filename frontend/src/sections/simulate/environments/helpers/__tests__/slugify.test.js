import { describe, expect, it } from "vitest";
import { slugify } from "../slugify";

describe("slugify", () => {
  it("makes a shell-safe slug, with a fallback for an empty name", () => {
    expect(slugify("My Env!")).toBe("my-env");
    expect(slugify("")).toBe("environment");
    expect(slugify("---")).toBe("environment");
    expect(slugify("Café Ñandú")).toBe("cafe-nandu");
  });

  it("takes another separator and fallback", () => {
    expect(slugify("Sam  Lee", { separator: "_", fallback: "actor" })).toBe(
      "sam_lee",
    );
    expect(slugify(null, { separator: "_", fallback: "actor" })).toBe("actor");
  });
});
