// Vendor confirmation is independent of the gateway eviction barrier.
export function cleanupNotice(revocation, pending) {
  if (revocation === "unconfirmed") {
    return (
      "The vendor did not confirm revocation. You can also revoke access in your vendor account settings." +
      (pending
        ? " Gateway cleanup is still pending; retry disconnect before connecting again."
        : "")
    );
  }
  if (revocation === "pending") {
    return "The vendor has not confirmed revocation of your old account. Retry disconnect, or connect again once gateway cleanup finishes; a new sign-in will make one final revocation attempt.";
  }
  return pending
    ? "Your account is revoked locally, but gateway cache cleanup is not confirmed. Retry disconnect before connecting again."
    : null;
}

export function disconnectNotice(id, result) {
  const notice = cleanupNotice(
    result.upstream_revocation,
    result.cleanup_pending,
  );
  if (result.gateway_synced !== true) {
    return {
      message: `Your account on ${id} is revoked locally; gateway cleanup needs retry.`,
      variant: "warning",
    };
  }
  if (notice) return { message: notice, variant: "warning" };
  return {
    message: `Your account on ${id} is disconnected.`,
    variant: "success",
  };
}
