import { beforeEach, expect, it, vi } from "vitest";
import { useRotateApiKey } from "../useApiKeys";
import approvals from "../../__tests__/rotation-copy.activation.json";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  patch: vi.fn(),
  invalidateQueries: vi.fn(),
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(),
  keepPreviousData: vi.fn(),
  useQueryClient: () => ({ invalidateQueries: mocks.invalidateQueries }),
  useMutation: (options) => options,
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post, patch: mocks.patch },
  endpoints: {
    gateway: {
      createApiKey: "/fixture/keys/",
      updateApiKey: (id) => `/fixture/keys/${id}/`,
      revokeApiKey: (id) => `/fixture/keys/${id}/revoke/`,
    },
  },
}));

beforeEach(() => vi.resetAllMocks());

it("sends a distinct revoke command beside the canonical ownership marker in one request", async () => {
  mocks.post.mockResolvedValueOnce({ data: { result: {
    id: "new-row",
    status: "active", key: "fixture-once",
    rotation: { status: "revoked", expiry_copied: true, gateway_revocation_confirmed: true },
  } } });
  const result = await useRotateApiKey().mutationFn({
    id: "old-row",
    gatewayKeyId: "not-the-row-id",
    name: "fixture",
    user: "actor",
    status: "active",
    expiresAt: "2027-01-01T00:00:00Z",
  });
  expect(mocks.post).toHaveBeenCalledExactlyOnceWith("/fixture/keys/", expect.objectContaining({
    rotates_key_id: "old-row",
    revoke_rotated_key: true,
  }));
  expect(mocks.patch).not.toHaveBeenCalled();
  expect(result).toMatchObject({ secret: "fixture-once", oldRevoked: true, gatewayRevoked: true });
});

it("keeps KEYS-ROTATE-2 refusal exactly equal to the activated bytes", async () => {
  const expected = approvals.rows["KEYS-ROTATE-2"].afterExact;
  const error = await useRotateApiKey().mutationFn({ name: "fixture" }).catch((value) => value);
  expect(error.message).toBe(expected);
  expect(new TextEncoder().encode(error.message)).toEqual(new TextEncoder().encode(expected));
  expect(mocks.post).not.toHaveBeenCalled();
});

it("never sends a follow-up request when the server refuses the rotation marker", async () => {
  mocks.post.mockRejectedValueOnce(new Error("Key rotation is not permitted"));
  await expect(useRotateApiKey().mutationFn({ id: "peer-row", name: "fixture" }))
    .rejects.toThrow("Key rotation is not permitted");
  expect(mocks.post).toHaveBeenCalledTimes(1);
  expect(mocks.patch).not.toHaveBeenCalled();
});

it("refuses a legacy reply instead of silently degrading to non-atomic revoke", async () => {
  mocks.post.mockResolvedValueOnce({ data: { result: { id: "new-row", status: "active", key: "fixture-once" } } });
  await expect(useRotateApiKey().mutationFn({ id: "old-row", name: "fixture" }))
    .rejects.toThrow("Key rotation is not permitted");
  expect(mocks.post).toHaveBeenCalledTimes(1);
  expect(mocks.patch).not.toHaveBeenCalled();
});

it.each([
  { status: "revoked" },
  { status: "revoked", gateway_revocation_confirmed: false },
  { status: "active", gateway_revocation_confirmed: true },
  { status: "revoked", gateway_revocation_confirmed: true, warning: "Fixture pending" },
])("does not infer enforcement from an incomplete or contradictory atomic result", async (rotation) => {
  mocks.post.mockResolvedValueOnce({ data: { result: {
    id: "new-row", status: "active", key: "fixture-once", rotation,
  } } });
  const result = await useRotateApiKey().mutationFn({ id: "old-row", name: "fixture" });
  expect(result.gatewayRevoked).toBe(false);
});
