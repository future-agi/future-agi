// Synthetic OAuth servers. Endpoint shapes are fixtures, never vendor origins.
import { createHash, randomUUID, generateKeyPairSync, sign } from "node:crypto";
import { pathToFileURL } from "node:url";
import { json, read, listen, escape, localRedirect } from "./http.mjs";
export const K2_CLIENTS = {
  google: { id: "e2e-google-client", secret: "e2e-google-secret-not-real" },
  microsoft: {
    id: "e2e-microsoft-client",
    secret: "e2e-microsoft-secret-not-real",
  },
};
export function createK2(
  origin = "https://k2.e2e-public.test:8443",
  now = Date.now,
) {
  if (!/^https:\/\/(k2\.e2e-public\.test|localhost)(:\d+)?$/.test(origin))
    throw new Error("fixture origin only");
  const codes = new Map(),
    tokens = new Map(),
    refresh = new Map(),
    revoked = new Set(),
    receipts = [];
  const { publicKey, privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
  });
  const jwk = {
    ...publicKey.export({ format: "jwk" }),
    kid: "fixture",
    use: "sig",
    alg: "RS256",
  };
  const routes = Object.fromEntries(
    Object.keys(K2_CLIENTS).map((provider) => [
      provider,
      provider === "google"
        ? {
            issuer: `${origin}/google`,
            discovery: "/google/.well-known/openid-configuration",
            authorize: "/google/o/oauth2/v2/auth",
            token: "/google/token",
            revoke: "/google/revoke",
            userinfo: "/google/userinfo",
            jwks: "/google/certs",
          }
        : {
            issuer: `${origin}/microsoft/common/v2.0`,
            discovery:
              "/microsoft/common/v2.0/.well-known/openid-configuration",
            authorize: "/microsoft/common/oauth2/v2.0/authorize",
            token: "/microsoft/common/oauth2/v2.0/token",
            revoke: "/microsoft/common/oauth2/v2.0/revoke",
            userinfo: "/microsoft/oidc/userinfo",
            jwks: "/microsoft/discovery/v2.0/keys",
          },
    ]),
  );
  const identity = (grant) => ({
    sub: grant.sub,
    email: `${grant.sub}@fixture.invalid`,
    name: grant.sub,
  });
  const issue = (res, grant) => {
    const access = `k2-at-${randomUUID()}`,
      rt = `k2-rt-${randomUUID()}`;
    const issued = { ...grant, exp: now() + 900000 };
    tokens.set(access, issued);
    refresh.set(rt, issued);
    const head = Buffer.from(
      JSON.stringify({ alg: "RS256", kid: "fixture", typ: "JWT" }),
    ).toString("base64url");
    const body = Buffer.from(
      JSON.stringify({
        ...identity(grant),
        iss: routes[grant.provider].issuer,
        aud: K2_CLIENTS[grant.provider].id,
        iat: Math.floor(now() / 1000),
        exp: Math.floor(issued.exp / 1000),
        nonce: grant.nonce,
      }),
    ).toString("base64url");
    const signed = `${head}.${body}`;
    json(res, 200, {
      access_token: access,
      refresh_token: rt,
      token_type: "Bearer",
      expires_in: 900,
      scope: grant.scope,
      id_token: `${signed}.${sign("RSA-SHA256", Buffer.from(signed), privateKey).toString("base64url")}`,
    });
  };
  const data = async (req, res) => {
    const u = new URL(req.url, origin);
    const provider = Object.keys(routes).find((p) =>
      Object.values(routes[p]).includes(u.pathname),
    );
    if (!provider) return json(res, 404, { error: "not_found" });
    const r = routes[provider],
      client = K2_CLIENTS[provider];
    if (u.pathname === r.discovery && req.method === "GET")
      return json(res, 200, {
        issuer: r.issuer,
        authorization_endpoint: origin + r.authorize,
        token_endpoint: origin + r.token,
        revocation_endpoint: origin + r.revoke,
        userinfo_endpoint: origin + r.userinfo,
        jwks_uri: origin + r.jwks,
        response_types_supported: ["code"],
        grant_types_supported: ["authorization_code", "refresh_token"],
        code_challenge_methods_supported: ["S256"],
        token_endpoint_auth_methods_supported: [
          "client_secret_post",
          "client_secret_basic",
        ],
        subject_types_supported: ["public"],
        id_token_signing_alg_values_supported: ["RS256"],
      });
    if (u.pathname === r.jwks && req.method === "GET")
      return json(res, 200, { keys: [jwk] });
    if (u.pathname === r.userinfo && req.method === "GET") {
      const grant = tokens.get(
        (req.headers.authorization ?? "").replace(/^Bearer /, ""),
      );
      return grant &&
        grant.provider === provider &&
        grant.exp > now() &&
        !revoked.has(grant.family)
        ? json(res, 200, identity(grant))
        : json(res, 401, { error: "invalid_token" });
    }
    const form =
      req.method === "GET"
        ? u.searchParams
        : new URLSearchParams(await read(req));
    if (u.pathname === r.authorize && ["GET", "POST"].includes(req.method)) {
      if (
        form.get("client_id") !== client.id ||
        !localRedirect(form.get("redirect_uri")) ||
        form.get("response_type") !== "code" ||
        (form.has("code_challenge") &&
          (form.get("code_challenge_method") !== "S256" ||
            !/^[A-Za-z0-9_-]{43}$/.test(form.get("code_challenge"))))
      )
        return json(res, 400, { error: "invalid_request" });
      if (req.method === "GET") {
        res.writeHead(200, {
          "Content-Type": "text/html; charset=utf-8",
          "Cache-Control": "no-store",
        });
        return res.end(
          `<!doctype html><title>Synthetic K2 consent</title><h1>Synthetic ${provider} fixture</h1><p>No vendor account is used.</p><form method="post" action="${r.authorize}">${[
            ...form,
          ]
            .filter(([k]) => k !== "decision")
            .map(
              ([k, v]) =>
                `<input type="hidden" name="${escape(k)}" value="${escape(v)}">`,
            )
            .join(
              "",
            )}<button name="decision" value="approve">Approve</button><button name="decision" value="deny">Deny</button></form>`,
        );
      }
      const redirect = new URL(form.get("redirect_uri"));
      redirect.searchParams.set("state", form.get("state") ?? "");
      if (form.get("decision") !== "approve")
        redirect.searchParams.set("error", "access_denied");
      else {
        const code = `k2-code-${randomUUID()}`;
        codes.set(code, {
          provider,
          redirect: form.get("redirect_uri"),
          challenge: form.get("code_challenge"),
          sub: form.get("login_hint") || "fixture-user",
          scope: form.get("scope") || "openid email",
          nonce: form.get("nonce"),
          family: randomUUID(),
          exp: now() + 60000,
        });
        redirect.searchParams.set("code", code);
      }
      redirect.searchParams.set("iss", r.issuer);
      res.writeHead(302, { Location: redirect.href });
      return res.end();
    }
    if (req.method !== "POST")
      return json(res, 405, { error: "method_not_allowed" });
    let id = form.get("client_id"),
      secret = form.get("client_secret");
    if (req.headers.authorization?.startsWith("Basic "))
      [id, secret] = Buffer.from(req.headers.authorization.slice(6), "base64")
        .toString()
        .split(":");
    if (id !== client.id || secret !== client.secret)
      return json(res, 401, { error: "invalid_client" });
    if (u.pathname === r.revoke) {
      const tok = form.get("token");
      if (!tok) return json(res, 400, { error: "invalid_request" });
      const grant = tokens.get(tok) ?? refresh.get(tok);
      if (grant?.provider === provider) revoked.add(grant.family);
      receipts.push({
        provider,
        subject: grant?.provider === provider ? grant.sub : null,
        token_sha256: `sha256:${createHash("sha256").update(tok).digest("hex")}`,
      });
      return json(res, 200, {});
    }
    if (u.pathname !== r.token) return json(res, 404, { error: "not_found" });
    if (form.get("grant_type") === "authorization_code") {
      const code = form.get("code"),
        grant = codes.get(code);
      codes.delete(code);
      if (
        !grant ||
        grant.provider !== provider ||
        grant.exp <= now() ||
        grant.redirect !== form.get("redirect_uri") ||
        (grant.challenge &&
          createHash("sha256")
            .update(form.get("code_verifier") || "")
            .digest("base64url") !== grant.challenge)
      )
        return json(res, 400, { error: "invalid_grant" });
      return issue(res, grant);
    }
    if (form.get("grant_type") === "refresh_token") {
      const grant = refresh.get(form.get("refresh_token"));
      if (grant?.provider === provider && grant.used) revoked.add(grant.family);
      if (!grant || grant.provider !== provider || revoked.has(grant.family))
        return json(res, 400, { error: "invalid_grant" });
      grant.used = true;
      return issue(res, { ...grant, used: false });
    }
    return json(res, 400, { error: "unsupported_grant_type" });
  };
  const control = (req, res) =>
    req.method === "GET" && req.url === "/__received"
      ? json(res, 200, { revocations: receipts })
      : req.method === "GET" && req.url === "/__health"
        ? json(res, 200, { ok: true })
        : json(res, 404, { error: "not_found" });
  return { data, control };
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const k2 = createK2();
  listen(k2.data, k2.control);
}
