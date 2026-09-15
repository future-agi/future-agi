// Escape-first HTML sanitizer for notification content.
//
// The previous regex blacklist (strip <script>, on*="...", javascript:)
// missed single-quoted/unquoted/slash-separated handlers
// (<img src=x onerror=...>, <svg/onload=...>), so attacker markup passed
// through to dangerouslySetInnerHTML unchanged. Instead, escape EVERYTHING
// first (same construction as WidgetMarkdown), then re-introduce only the
// tags this UI renders (<p>, <strong>, <em>, <br>, <a href="#">).
// Captures are already escaped, so attacker content cannot break out.
export function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function sanitizeNotificationHtml(data) {
  return escapeHtml(data)
    .replace(/&lt;p&gt;/g, "<p>")
    .replace(/&lt;\/p&gt;/g, "</p>")
    .replace(/&lt;strong&gt;/g, "<strong>")
    .replace(/&lt;\/strong&gt;/g, "</strong>")
    .replace(/&lt;em&gt;/g, "<em>")
    .replace(/&lt;\/em&gt;/g, "</em>")
    .replace(/&lt;br\s*\/?&gt;/g, "<br/>")
    .replace(/&lt;a href=&quot;#&quot;&gt;/g, '<a href="#">')
    .replace(/&lt;a href=&#39;#&#39;&gt;/g, '<a href="#">')
    .replace(/&lt;\/a&gt;/g, "</a>");
}
