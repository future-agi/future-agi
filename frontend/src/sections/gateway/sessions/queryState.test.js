import { describe, expect, it } from "vitest";
import { readSessionQuery, updateSessionQuery } from "./queryState";

describe("Sessions URL state", () => {
  it("defaults to the first unfiltered page with no drawer", () => {
    expect(readSessionQuery(new URLSearchParams())).toEqual({
      session: null,
      status: "",
      search: "",
      page: 1,
    });
  });

  it("round trips the database id and list context without changing other parameters", () => {
    const original = new URLSearchParams("gateway=west&tag=one&tag=two");
    const next = updateSessionQuery(original, {
      session: "row-12",
      status: "closed",
      search: "checkout & returns",
      page: 3,
      token: "not-a-query-field",
    });
    expect(readSessionQuery(new URLSearchParams(next.toString()))).toEqual({
      session: "row-12",
      status: "closed",
      search: "checkout & returns",
      page: 3,
    });
    expect(next.get("gateway")).toBe("west");
    expect(next.getAll("tag")).toEqual(["one", "two"]);
    expect(next.has("token")).toBe(false);
    expect(original.toString()).toBe("gateway=west&tag=one&tag=two");
  });

  it.each([
    "0",
    "-2",
    "1.5",
    "2oops",
    "1e3",
    "Infinity",
    "9007199254740992",
    "",
  ])("falls back for malformed page %s and unsupported status", (page) => {
    const params = new URLSearchParams({
      page,
      status: "deleted",
      gateway: "west",
    });
    expect(readSessionQuery(params)).toMatchObject({ page: 1, status: "" });
    const updated = updateSessionQuery(params, { search: "orders" });
    expect(updated.get("gateway")).toBe("west");
    expect(updated.has("status")).toBe(false);
    expect(updated.has("page")).toBe(false);
  });

  it.each(["../requests", "row/12", " ", "row?secret=value", "a".repeat(129)])(
    "does not forward malformed drawer identity %s",
    (session) => {
      expect(
        readSessionQuery(new URLSearchParams({ session })).session,
      ).toBeNull();
    },
  );

  it("removes only the drawer identity when closing", () => {
    const params = new URLSearchParams(
      "session=row-12&status=active&search=orders&page=2&gateway=west",
    );
    const closed = updateSessionQuery(params, { session: null });
    expect(readSessionQuery(closed)).toEqual({
      session: null,
      status: "active",
      search: "orders",
      page: 2,
    });
    expect(closed.get("gateway")).toBe("west");
    expect(closed.has("session")).toBe(false);
  });
});
