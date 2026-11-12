package middleware

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// WithAuthorizationOf carries a request's key identity, or its license flag
// and claims, onto another context (an async job's), and nothing else.
func TestWithAuthorizationOf_CarriesOnlyTheAuthorization(t *testing.T) {
	type otherKey struct{}

	t.Run("key identity", func(t *testing.T) {
		from := context.WithValue(context.Background(), keyIdentityContextKey{}, auth.Identity{KeyID: "key_1", OrgID: "org-a"})
		from = context.WithValue(from, otherKey{}, "request-only")
		from = models.WithRequestID(from, "req-1")

		got := WithAuthorizationOf(context.Background(), from)
		if id, ok := KeyIdentity(got); !ok || id.KeyID != "key_1" || id.OrgID != "org-a" {
			t.Errorf("KeyIdentity = (%+v, %v), want key_1 in org-a", id, ok)
		}
		if IsLicenseAuthorized(got) {
			t.Error("a key-authenticated request became license-authorized")
		}
		if got.Value(otherKey{}) != nil || models.GetRequestID(got) != "" {
			t.Error("values other than the authorization were carried over")
		}
	})

	t.Run("license", func(t *testing.T) {
		from := context.WithValue(context.Background(), licenseAuthContextKey{}, true)
		from = context.WithValue(from, licenseClaimsContextKey{}, &licenseClaims{LicenseID: "lic_1", CustomerID: "cust_1", InstanceID: "inst_1"})

		got := WithAuthorizationOf(context.Background(), from)
		if !IsLicenseAuthorized(got) {
			t.Fatal("the license authorization was not carried over")
		}
		if c := GetLicenseClaims(got); c == nil || c.LicenseID != "lic_1" || c.CustomerID != "cust_1" || c.InstanceID != "inst_1" {
			t.Errorf("license claims = %+v, want lic_1/cust_1/inst_1", c)
		}
		if _, ok := KeyIdentity(got); ok {
			t.Error("a license-authorized request gained a key identity")
		}
	})

	t.Run("unauthenticated", func(t *testing.T) {
		got := WithAuthorizationOf(context.Background(), context.Background())
		if _, ok := KeyIdentity(got); ok || IsLicenseAuthorized(got) {
			t.Error("an unauthenticated request gained an authorization")
		}
	})
}
