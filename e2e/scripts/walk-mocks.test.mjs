import assert from "node:assert/strict";
import test from "node:test";
import { Readable } from "node:stream";
import { createHash, createPublicKey, verify } from "node:crypto";
import { createK2, K2_CLIENTS } from "../stack/walk-mocks/k2.mjs";
import { createSIEM, SIEM_TOKEN } from "../stack/walk-mocks/siem.mjs";

async function call(handler, method, url, body = "", headers = {}) {
  const req = Object.assign(Readable.from([Buffer.from(body)]), {
    method,
    url,
    headers,
  });
  const res = {
    status: 0,
    headers: {},
    raw: "",
    writeHead(status, h) {
      this.status = status;
      this.headers = h;
    },
    end(raw = "") {
      this.raw += raw;
    },
  };
  await handler(req, res);
  return {
    ...res,
    body:
      res.headers["Content-Type"] === "application/json"
        ? JSON.parse(res.raw)
        : undefined,
  };
}
const post = (handler, path, form) =>
  call(handler, "POST", path, new URLSearchParams(form).toString(), {
    "content-type": "application/x-www-form-urlencoded",
  });

test("K2 discovery, consent, PKCE, per-user identity, signed ID token, rotation and revocation for both shapes", async () => {
  let now = Date.now();
  const k = createK2(undefined, () => now);
  for (const provider of ["google", "microsoft"]) {
    const discovery =
      provider === "google"
        ? "/google/.well-known/openid-configuration"
        : "/microsoft/common/v2.0/.well-known/openid-configuration";
    const meta = (await call(k.data, "GET", discovery)).body;
    for (const key of [
      "issuer",
      "authorization_endpoint",
      "token_endpoint",
      "revocation_endpoint",
      "jwks_uri",
    ])
      assert.equal(new URL(meta[key]).hostname, "k2.e2e-public.test");
    const paths = Object.fromEntries(
      Object.entries(meta)
        .filter(([, v]) => typeof v === "string" && v.startsWith("https:"))
        .map(([key, v]) => [key, new URL(v).pathname]),
    );
    const client = K2_CLIENTS[provider],
      verifier = "v".repeat(48),
      challenge = createHash("sha256").update(verifier).digest("base64url");
    const credentials = { client_id: client.id, client_secret: client.secret };
    const tokens = [];
    for (const sub of ["member-one", "member-two"]) {
      const auth = {
        client_id: client.id,
        redirect_uri: "http://localhost:9999/callback",
        response_type: "code",
        login_hint: sub,
        state: "state-fixture",
        scope: "openid email",
        nonce: "nonce-fixture",
        code_challenge: challenge,
        code_challenge_method: "S256",
      };
      const page = await call(
        k.data,
        "GET",
        `${paths.authorization_endpoint}?${new URLSearchParams(auth)}`,
      );
      assert.equal(page.status, 200);
      assert.match(page.raw, /<button name="decision" value="approve">Approve/);
      const denied = await post(k.data, paths.authorization_endpoint, {
        ...auth,
        decision: "deny",
      });
      assert.equal(
        new URL(denied.headers.Location).searchParams.get("error"),
        "access_denied",
      );
      const approved = await post(k.data, paths.authorization_endpoint, {
        ...auth,
        decision: "approve",
      });
      const callback = new URL(approved.headers.Location);
      assert.equal(callback.searchParams.get("state"), auth.state);
      const form = {
        ...credentials,
        grant_type: "authorization_code",
        code: callback.searchParams.get("code"),
        redirect_uri: auth.redirect_uri,
        code_verifier: verifier,
      };
      assert.equal(
        (
          await post(k.data, paths.token_endpoint, {
            ...form,
            client_secret: "wrong",
          })
        ).status,
        401,
      );
      const response = await post(k.data, paths.token_endpoint, form);
      assert.equal(response.status, 200);
      assert.equal(
        (await post(k.data, paths.token_endpoint, form)).status,
        400,
      );
      const token = response.body;
      tokens.push(token);
      const identity = await call(k.data, "GET", paths.userinfo_endpoint, "", {
        authorization: `Bearer ${token.access_token}`,
      });
      assert.equal(identity.body.sub, sub);
      const keys = (await call(k.data, "GET", paths.jwks_uri)).body.keys;
      const [head, body, signature] = token.id_token.split(".");
      assert.equal(
        verify(
          "RSA-SHA256",
          Buffer.from(`${head}.${body}`),
          createPublicKey({ key: keys[0], format: "jwk" }),
          Buffer.from(signature, "base64url"),
        ),
        true,
      );
      assert.equal(
        JSON.parse(Buffer.from(body, "base64url")).nonce,
        "nonce-fixture",
      );
      assert.equal(
        (
          await post(k.data, paths.authorization_endpoint, {
            ...auth,
            redirect_uri: "https://accounts.google.com/callback",
            decision: "approve",
          })
        ).status,
        400,
      );
    }
    assert.notEqual(tokens[0].access_token, tokens[1].access_token);
    const refreshForm = {
      ...credentials,
      grant_type: "refresh_token",
      refresh_token: tokens[0].refresh_token,
    };
    const rotated = await post(k.data, paths.token_endpoint, refreshForm);
    assert.equal(rotated.status, 200);
    assert.equal(
      (await post(k.data, paths.token_endpoint, refreshForm)).status,
      400,
    );
    assert.equal(
      (
        await call(k.data, "GET", paths.userinfo_endpoint, "", {
          authorization: `Bearer ${rotated.body.access_token}`,
        })
      ).status,
      401,
    );
    assert.equal(
      (
        await post(k.data, paths.revocation_endpoint, {
          ...credentials,
          token: tokens[1].access_token,
        })
      ).status,
      200,
    );
    assert.equal(
      (
        await call(k.data, "GET", paths.userinfo_endpoint, "", {
          authorization: `Bearer ${tokens[1].access_token}`,
        })
      ).status,
      401,
    );
    now += 1000;
  }
  const receipts = (await call(k.control, "GET", "/__received")).body;
  assert.equal(receipts.revocations.length, 2);
  assert.match(receipts.revocations[0].token_sha256, /^sha256:/);
  assert.equal((await call(k.data, "GET", "/__received")).status, 404);
  assert.throws(() => createK2("https://accounts.google.com"));
});

test("SIEM HEC and HTTPS JSON auth, exact receipts, injected outage and channel isolation", async () => {
  const s = createSIEM();
  const send = (path, raw, auth) =>
    call(s.data, "POST", path, raw, { authorization: auth });
  assert.equal(
    (
      await send(
        "/services/collector/event?channel=a",
        '{"event":{"id":"a"}}',
        "Splunk bad",
      )
    ).status,
    403,
  );
  assert.deepEqual(
    (await call(s.control, "GET", "/__received/a")).body.events,
    [],
  );
  assert.equal(
    (
      await call(
        s.control,
        "POST",
        "/__failure/a",
        JSON.stringify({ count: 1, status: 503 }),
      )
    ).status,
    200,
  );
  const payload =
    '{"event":{"id":"first","text":"brace } inside"}}{"event":{"id":"second"}}';
  assert.equal(
    (
      await send(
        "/services/collector/event?channel=a",
        payload,
        `Splunk ${SIEM_TOKEN}`,
      )
    ).status,
    503,
  );
  assert.equal(
    (
      await send(
        "/services/collector/event?channel=a",
        payload,
        `Splunk ${SIEM_TOKEN}`,
      )
    ).body.code,
    0,
  );
  assert.equal(
    (await send("/json/b", '[{"id":"other"}]', `Bearer ${SIEM_TOKEN}`)).body
      .accepted,
    1,
  );
  assert.deepEqual(
    (await call(s.control, "GET", "/__received/a")).body.events.map(
      (e) => e.event.id,
    ),
    ["first", "second"],
  );
  assert.deepEqual(
    (await call(s.control, "GET", "/__received/b")).body.events,
    [{ id: "other" }],
  );
  const receipts = await call(s.control, "GET", "/__received/a");
  assert.deepEqual(
    receipts.body.attempts.map((a) => a.authorized),
    [false, true, true],
  );
  assert.equal(receipts.raw.includes(SIEM_TOKEN), false);
  for (const raw of [
    "{",
    "null",
    '{"not_event":1}',
    '{"event":1}junk',
    '[{"event":1}]',
  ])
    assert.equal(
      (await send("/services/collector/event", raw, `Splunk ${SIEM_TOKEN}`))
        .status,
      400,
    );
  assert.equal(
    (await call(s.control, "POST", "/__failure/a", '{"count":-1,"status":503}'))
      .status,
    400,
  );
  assert.equal((await call(s.data, "GET", "/__received/a")).status, 404);
  assert.equal((await call(s.control, "GET", "/__health")).status, 200);
});
