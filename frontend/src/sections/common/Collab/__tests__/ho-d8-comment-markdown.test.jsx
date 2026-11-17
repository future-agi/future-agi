// HO D8 (W5 C5): comment bodies render through the one safe Markdown
// renderer: lists and links format, raw HTML shows as text, links carry
// rel="noopener noreferrer nofollow", remote images are not loaded.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CommentCard from "../CommentCard";

afterEach(cleanup);

const comment = (body) => ({
  id: "c1",
  body,
  author: { id: "u2", name: "Maya Chen" },
  created_at: "2026-10-20T10:00:00Z",
  reactions: {},
  anchor: { kind: "row" },
});
const renderBody = (body) =>
  render(
    <MemoryRouter>
      <CommentCard
        comment={comment(body)}
        root
        thread={{ id: "t1", mention_ids: [] }}
        data={{ me: { id: "u1", perms: { view: true } }, limits: {} }}
        mutations={{ post: vi.fn(), patch: vi.fn(), remove: vi.fn() }}
      />
    </MemoryRouter>,
  );

describe("HO D8 · comment bodies as safe Markdown", () => {
  it("formats a list and a link, with safe link attributes", () => {
    renderBody(
      "Two issues:\n\n- the refund amount\n- [policy](https://example.com/p)",
    );
    const body = screen.getByTestId("comment-body");
    expect(body.querySelector('[data-testid="safe-markdown"]')).toBeTruthy();
    expect(body.querySelectorAll("li")).toHaveLength(2);
    const a = body.querySelector("a");
    expect(a.getAttribute("href")).toBe("https://example.com/p");
    expect(a.getAttribute("rel")).toBe("noopener noreferrer nofollow");
    expect(a.getAttribute("target")).toBe("_blank");
  });

  it("shows raw HTML as text and never runs it", () => {
    renderBody('Look <script>alert("x")</script> <b>bold</b>');
    const body = screen.getByTestId("comment-body");
    expect(body.querySelector("script")).toBeNull();
    expect(body.querySelector("b")).toBeNull();
    expect(body.textContent).toContain('<script>alert("x")</script>');
  });

  it("does not load a remote image and keeps mentions in bold", () => {
    renderBody("![chart](https://example.com/x.png) cc @[Maya Chen](user:u2)");
    const body = screen.getByTestId("comment-body");
    expect(body.querySelector("img")).toBeNull();
    expect(body.textContent).toContain("[image: chart]");
    expect(body.querySelector("strong")?.textContent).toBe("@Maya Chen");
  });

  it("drops a javascript: link target", () => {
    renderBody("[click](javascript:alert(1))");
    const a = screen.getByTestId("comment-body").querySelector("a");
    expect(a?.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
  });
});
