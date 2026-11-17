package auth

import (
	"testing"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The request carries a key's platform-set billing as key_billing, written
// from the key itself; a key without it leaves none, whatever came before.
func TestKeyBillingReachesTheRequestFromTheKeyOnly(t *testing.T) {
	funded := &authpkg.APIKey{ID: "key-run", Name: "run", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-1", "billing": "platform_funded"}}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	writeKeyAuthority(rc, funded, funded.Metadata)
	if got := rc.Metadata["key_billing"]; got != "platform_funded" {
		t.Fatalf("key_billing = %q, want platform_funded", got)
	}

	plain := &authpkg.APIKey{ID: "key-plain", Name: "plain", KeyType: "byok", Metadata: map[string]string{"org_id": "org-1"}}
	rc2 := models.AcquireRequestContext()
	defer rc2.Release()
	rc2.Metadata["key_billing"] = "platform_funded" // a stale value never survives the key's own authority
	writeKeyAuthority(rc2, plain, plain.Metadata)
	if got, set := rc2.Metadata["key_billing"]; set {
		t.Fatalf("a key without billing left key_billing %q", got)
	}

	other := &authpkg.APIKey{ID: "key-x", Name: "x", KeyType: "byok", Metadata: map[string]string{"billing": "something_else"}}
	rc3 := models.AcquireRequestContext()
	defer rc3.Release()
	writeKeyAuthority(rc3, other, other.Metadata)
	if got, set := rc3.Metadata["key_billing"]; set {
		t.Fatalf("an unknown billing value reached the request as %q", got)
	}
}
