package tenancy

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func TestCredentialLifecycleReactivationDoesNotReviveOldHandle(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "search")
	r.push("org-a", 1, credentialConfig("active", "synthetic-first", ""))
	_, pool := r.resolve("org-a")
	old, err := pool.Client("github", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	// Both writes complete without giving a background watcher a scheduling
	// opportunity. Reconnection cannot erase the preceding revocation event.
	r.push("org-a", 2, credentialConfig("revoked", "", ""))
	r.push("org-a", 3, credentialConfig("active", "synthetic-first", ""))
	requireCredentialRefusal(t, credentialCall(old))
	r.resolve("org-a")
	fresh, err := pool.Client("github", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	if err := credentialCall(fresh); err != nil {
		t.Fatal("fresh connection is not usable")
	}
}
