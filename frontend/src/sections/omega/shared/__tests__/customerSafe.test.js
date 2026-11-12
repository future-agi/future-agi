// The COPY1 check against EE's real words (unit, regression): a customer's own
// secret name is data, an operator's variable is not, and system errors from a
// failed docker run never reach a page.
import { describe, expect, it } from "vitest";

import { isCustomerSafe } from "../customerSafe";
import { createOmegaClient } from "../omegaClient";
import { OMEGA_STATE } from "../omegaStates";

// EE project-deployments/secrets.ts at omega-ee ee/p-e-auth 28498cc5, verbatim,
// with the customer's reference interpolated where EE puts `${name}`.
const secretMessages = (name) => ({
  missing: `No active credential named ${name} is in this workspace's vault. Add it under Credentials, then deploy again.`,
  ambiguous: `More than one active credential is named ${name}; revoke the extra one so the reference is unambiguous.`,
  invalid: `Secret references are upper-case environment names (A-Z, 0-9, _); ${name} is not.`,
  unbound: `The credential ${name} could not be bound to this deployment.`,
  noVault: `This deployment references the secret ${name}, but no credential vault is mounted on this control plane.`,
});
const VAULT_REFUSED =
  "The vault refused to materialize this deployment's secrets: Environment credential binding ids must be unique..";
const FALLBACK =
  "A secret this deployment references isn't available in this workspace's vault. Check it under Credentials, then deploy again.";

const eeRefusal = (code, message, status = 400) =>
  new Response(
    JSON.stringify({
      schemaVersion: "omega-workspace/v1",
      error: { code, message },
    }),
    { status, headers: { "content-type": "application/json" } },
  );
const deployWith = async (response) =>
  createOmegaClient({
    mintAssertion: async () => ({ token: "omega_delegate_x" }),
    fetchImpl: async () => response,
  }).omegaFetch("/workspace/deployments", { method: "POST", json: {} });

describe("secret_unavailable keeps the customer's secret name (verifier finding 2 on 1bf593b37)", () => {
  it("shows EE's actionable message for real secret names", async () => {
    for (const name of [
      "DATABASE_URL",
      "OPENAI_API_KEY",
      "STRIPE_SECRET_KEY",
      "TOKEN",
    ]) {
      const { missing, ambiguous, unbound } = secretMessages(name);
      for (const message of [missing, ambiguous, unbound]) {
        const result = await deployWith(
          eeRefusal("secret_unavailable", message),
        );
        expect(result, message).toMatchObject({
          ok: false,
          state: OMEGA_STATE.ERROR,
          status: 400,
          code: "secret_unavailable",
          title: "A secret this deployment needs isn't ready",
          message,
        });
      }
    }
    const typo = secretMessages("database-url").invalid;
    expect(
      (await deployWith(eeRefusal("secret_unavailable", typo))).message,
    ).toBe(typo);
  });

  it("still replaces operator words, and an operator's own variable, with Future AGI copy", async () => {
    for (const message of [
      secretMessages("DATABASE_URL").noVault,
      VAULT_REFUSED,
      secretMessages("OMEGA_HOST_ASSERTIONS").missing,
      secretMessages("FA_INTERNAL_TOKEN").ambiguous,
    ]) {
      const result = await deployWith(eeRefusal("secret_unavailable", message));
      expect(result, message).toMatchObject({
        state: OMEGA_STATE.ERROR,
        code: "secret_unavailable",
        message: FALLBACK,
      });
    }
  });

  it("allows customer names only for a code that says so", async () => {
    const message = secretMessages("DATABASE_URL").missing;
    expect(isCustomerSafe(message)).toBe(false);
    expect(isCustomerSafe(message, { names: true })).toBe(true);
    // The names are data; the words around them are still checked.
    expect(
      isCustomerSafe("Valid Omega API key required for DATABASE_URL.", {
        names: true,
      }),
    ).toBe(false);
    expect(
      isCustomerSafe("Set OMEGA_VAULT_KEY on the server for OPENAI_API_KEY.", {
        names: true,
      }),
    ).toBe(false);
    const other = await deployWith(eeRefusal("validation", message));
    expect(other.message).toBe("Omega could not complete the request.");
    // A sandbox offer's reason that names the operator's variable
    // (EE sandboxes/firewall/helper.ts:90) stays blocked on `validation`.
    const firewall = await deployWith(
      eeRefusal(
        "validation",
        "The Locked-down network is not available: no image is named for the firewall helper (an operator names one with OMEGA_WORKSPACE_SANDBOX_FIREWALL_IMAGE)",
      ),
    );
    expect(firewall.message).toBe("Omega could not complete the request.");
  });
});

describe("system errors from a failed run (verifier finding 3 on 1bf593b37)", () => {
  it("blocks Node error codes, a failed spawn and docker command lines", () => {
    for (const bad of [
      "Could not run docker: spawn docker ENOENT",
      "Revision 3 could not be started: spawn docker EACCES",
      "open /var/lib/omega/deploy.json: EPERM",
      "write failed: ENOSPC: no space left on device",
      "EMFILE: too many open files",
      "ENFILE, EEXIST, EISDIR, ENOTDIR, ENOTEMPTY, EBUSY",
      "EAGAIN: resource temporarily unavailable",
      "EROFS: read-only file system",
      "Revision 2 failed: docker run failed (exit 125): Unable to find image",
      "docker stop failed: Error response from daemon",
      "docker image rm failed: conflict",
      "spawn podman ENOENT",
    ]) {
      expect(isCustomerSafe(bad), bad).toBe(false);
      expect(isCustomerSafe(bad, { names: true }), bad).toBe(false);
    }
    for (const good of [
      "The bundle has no Dockerfile at its root.",
      "The Docker build failed at step 3 of 7.",
      "Your Docker image is larger than this workspace allows.",
      "Revision 4 is still building; try again when it is live or failed.",
    ]) {
      expect(isCustomerSafe(good), good).toBe(true);
    }
  });
});
