package enduser

import (
	"strings"
	"testing"
)

func TestIdentityTrustBoundary(t *testing.T) {
	for _, tc := range []struct {
		name, kind, user, asserted string
		allow                      bool
		source, reason             string
	}{
		{"oauth", "oauth", "member-a", "", false, "member", ""},
		{"personal", "key", "member-a", "member-a", false, "member", ""},
		{"conflicting oauth", "oauth", "member-a", "member-b", true, "", "end_user_conflict"},
		{"conflicting personal", "key", "member-a", "member-b", true, "", "end_user_conflict"},
		{"asserted org key", "key", "", "customer-a", true, "asserted", ""},
		{"member only", "key", "", "customer-a", false, "", "end_user_identity_required"},
		{"never key creator", "key", "", "", true, "", "end_user_identity_required"},
		{"reserved", "key", "", "fau_member", true, "", "end_user_identity_required"},
		{"invalid", "key", "", "a\nb", true, "", "end_user_identity_required"},
		{"runtime", "runtime", "member-a", "", true, "", "end_user_identity_required"},
		{"callback", "callback", "member-a", "", true, "", "end_user_identity_required"},
		{"admin", "admin", "member-a", "", true, "", "end_user_identity_required"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := Resolve(Principal{Kind: tc.kind, OrgID: "org-a", UserID: tc.user}, tc.asserted, Policy{AllowAsserted: tc.allow})
			if tc.reason != "" {
				if err == nil || err.Error() != tc.reason {
					t.Fatalf("expected refusal %s", tc.reason)
				}
				return
			}
			if err != nil || got.Source != tc.source || len(got.Hash) != 16 || strings.Contains(got.Hash, "member") {
				t.Fatal("identity or hash mismatch")
			}
			other, _ := Resolve(Principal{Kind: tc.kind, OrgID: "org-b", UserID: tc.user}, tc.asserted, Policy{AllowAsserted: tc.allow})
			if got.Hash == other.Hash {
				t.Fatal("hash must be org scoped")
			}
		})
	}
}
