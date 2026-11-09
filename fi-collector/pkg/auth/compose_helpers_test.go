package auth

import (
	"context"
	"testing"
	"time"
)

// Shared outcome retains both sides of the authentication contract.
type composeAuthOutcome struct {
	result *ResolveResult
	err    error
}

type composeResolver struct {
	publicationFunc
	grant func(context.Context, *GrantClaims) (*ResolveResult, error)
}

func (r composeResolver) ValidateGrant(ctx context.Context, c *GrantClaims) (*ResolveResult, error) {
	return r.grant(ctx, c)
}

func composeCredential(t *testing.T, grant bool) (string, string, *ResolveResult, Config) {
	t.Helper()
	result := refreshResult()
	result.Principal, result.KeyType = PrincipalPersonal, "user"
	if !grant {
		result.APIKeyDigest = APIKeyDigest("k")
		return "k", "s", result, Config{}
	}
	fields := sandboxFields(time.Now())
	api, token := "fit_"+fields[9], signedGrantFields(fields, goldenPepper)
	cfg := Config{Peppers: mustPeppers(t, goldenPepper), EnvelopeKids: []string{"k1"}}
	claims, err := VerifyCollectorGrant(api, token, cfg.Peppers, cfg.EnvelopeKids, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	result.Grant, result.Principal, result.KeyType = claims, PrincipalSandbox, ""
	return api, token, result, cfg
}

func composeWait(t *testing.T, ch <-chan struct{}) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(2 * time.Second):
		t.Fatal("synchronization barrier not reached")
	}
}
func composeOutcome(t *testing.T, ch <-chan composeAuthOutcome) composeAuthOutcome {
	t.Helper()
	select {
	case got := <-ch:
		return got
	case <-time.After(2 * time.Second):
		t.Fatal("authentication did not settle")
	}
	return composeAuthOutcome{}
}
