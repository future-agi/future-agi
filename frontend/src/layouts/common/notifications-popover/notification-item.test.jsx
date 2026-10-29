import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import NotificationItem from "./notification-item";
import { sanitizeNotificationHtml } from "./notification-sanitize";

// The old regex blacklist passed event-handler markup straight through to
// dangerouslySetInnerHTML. Escape-first must neutralize every variant while
// preserving the <p>/<strong>/<em>/<br>/<a href="#"> the UI renders.
// No executable element may survive UNESCAPED: the browser only creates
// elements from literal `<tag` sequences, so assert none exist outside the
// allow-list. (Escaped text like `&lt;img` may still contain the word
// "onerror" — that is inert text, not a handler.)
const executableTag = /<(img|svg|script|details|iframe|a\s|input|button)\b/i;

describe("sanitizeNotificationHtml", () => {
  it("neutralizes unquoted event-handler markup", () => {
    const out = sanitizeNotificationHtml("<img src=x onerror=alert(1)>");
    expect(out).not.toMatch(executableTag);
    expect(out).toContain("&lt;img");
  });

  it("neutralizes slash-separated handlers", () => {
    const out = sanitizeNotificationHtml("<svg/onload=alert(1)>");
    expect(out).not.toMatch(executableTag);
  });

  it("neutralizes single-quoted handlers", () => {
    const out = sanitizeNotificationHtml("<img src=x onerror='alert(1)'>");
    expect(out).not.toMatch(executableTag);
  });

  it("neutralizes script tags and javascript: hrefs", () => {
    expect(sanitizeNotificationHtml("<script>alert(1)</script>")).not.toMatch(
      executableTag,
    );
    expect(
      sanitizeNotificationHtml('<a href="javascript:alert(1)">x</a>'),
    ).not.toMatch(executableTag);
  });

  it("preserves the allow-listed rich-text tags", () => {
    const out = sanitizeNotificationHtml(
      '<p><strong>Hi</strong> <em>there</em><br/><a href="#">link</a></p>',
    );
    expect(out).toContain("<p>");
    expect(out).toContain("<strong>Hi</strong>");
    expect(out).toContain("<em>there</em>");
    expect(out).toContain("<br/>");
    expect(out).toContain('<a href="#">link</a>');
  });

  it("keeps stray self-closing anchors inert instead of creating elements", () => {
    // Legacy mock typo was `<a/>` as a closing token; browsers parse that
    // as an <a> start tag, but the allow-list only restores </a>, so it
    // renders as inert text rather than an unclosed anchor.
    const out = sanitizeNotificationHtml("<p>x<a/></p>");
    expect(out).toContain("<p>");
    expect(out).toContain("&lt;a/&gt;");
    expect(out).not.toMatch(executableTag);
  });

  it("returns empty string for missing titles instead of throwing", () => {
    expect(sanitizeNotificationHtml(undefined)).toBe("");
    expect(sanitizeNotificationHtml(null)).toBe("");
  });
});

describe("NotificationItem", () => {
  const base = {
    id: "1",
    type: "mail",
    created_at: new Date().toISOString(),
    category: "Communication",
    is_un_read: false,
  };

  it("renders no executable elements for malicious titles", () => {
    const { container } = render(
      <NotificationItem
        notification={{ ...base, title: "<img src=x onerror=alert(1)>" }}
      />,
    );
    // Scope to the sanitized title box: the component legitimately renders
    // its own avatar <img> icon elsewhere, which must not match.
    const titleBox = container.querySelector(
      '[data-testid="notification-title"]',
    );
    expect(titleBox).not.toBeNull();
    expect(titleBox.querySelector("img")).toBeNull();
    expect(titleBox.querySelector("svg")).toBeNull();
    expect(titleBox.innerHTML).not.toMatch(executableTag);
  });

  it("renders allow-listed markup for benign titles", () => {
    render(
      <NotificationItem
        notification={{
          ...base,
          title: "<p><strong>Deja Brady</strong> sent a request</p>",
        }}
      />,
    );
    expect(screen.getByText("Deja Brady")).toBeInTheDocument();
  });
});
