/**
 * Fill the connection from a curl command.
 *
 * Anyone who has tested their own agent endpoint already has one of these — in
 * a terminal, a runbook, or the provider's docs. It carries the URL, the auth
 * scheme, the token and any custom headers together, which is four fields we
 * would otherwise ask for one at a time.
 *
 * Deliberately forgiving: real commands arrive wrapped across lines, with
 * smart quotes from a doc site, and with flags in any order.
 */

const unquote = (v = "") => v.trim().replace(/^['"“”]|['"“”]$/g, "");

/** Header lines, in the order they appeared. */
const headerPairs = (text) => {
  const out = [];
  const re = /(?:-H|--header)\s+(['"“”])([\s\S]*?)\1/g;
  let m;
  while ((m = re.exec(text))) {
    const raw = m[2];
    const i = raw.indexOf(":");
    if (i > 0) out.push([raw.slice(0, i).trim(), raw.slice(i + 1).trim()]);
  }
  return out;
};

export const parseCurl = (input) => {
  const text = (input || "").replace(/\\\r?\n/g, " ").trim();
  if (!/curl/i.test(text) && !/https?:\/\//i.test(text)) return null;

  // The URL: an explicit --url wins, otherwise the first http(s) token that is
  // not part of a header value.
  const explicit = text.match(/--url\s+(['"“”]?)(\S+?)\1(?:\s|$)/);
  const bare = text.match(/(?:^|\s)(['"“”]?)(https?:\/\/[^\s'"“”]+)\1/);
  const endpoint = unquote(explicit?.[2] || bare?.[2] || "");
  if (!endpoint) return null;

  const headers = headerPairs(text);
  const rest = [];
  let auth = "none";
  let token = "";

  headers.forEach(([k, v]) => {
    const key = k.toLowerCase();
    if (key === "authorization") {
      const bearer = v.match(/^Bearer\s+(.+)$/i);
      const basic = v.match(/^Basic\s+(.+)$/i);
      if (bearer) { auth = "bearer"; token = bearer[1]; return; }
      if (basic) { auth = "basic"; token = basic[1]; return; }
      auth = "bearer"; token = v; return;
    }
    if (/^(x-api-key|api-key|apikey)$/.test(key)) { auth = "apikey"; token = v; return; }
    if (key === "content-type") return;   // implied, not worth carrying
    rest.push({ key: k, value: v });
  });

  // -u user:pass is basic auth by another name.
  const userFlag = text.match(/(?:-u|--user)\s+(['"“”]?)([^\s'"“”]+)\1/);
  if (userFlag && auth === "none") { auth = "basic"; token = userFlag[2]; }

  return {
    endpoint,
    auth,
    ...(token ? { token } : {}),
    ...(rest.length ? { headers: rest } : {}),
    streaming: /text\/event-stream|--no-buffer|"stream"\s*:\s*true/i.test(text) || undefined,
  };
};

/** What we filled, for a one-line confirmation. */
export const describeFill = (values) => {
  const bits = [];
  if (values.endpoint) bits.push("endpoint");
  if (values.auth && values.auth !== "none") bits.push(`${values.auth} auth`);
  if (values.headers?.length) bits.push(`${values.headers.length} header${values.headers.length === 1 ? "" : "s"}`);
  if (values.streaming) bits.push("SSE");
  return bits.join(" · ");
};
