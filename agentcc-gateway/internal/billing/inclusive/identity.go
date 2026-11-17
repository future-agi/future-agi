package inclusive

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func SnapshotContains(snapshot, model string) bool {
	if snapshot == "" {
		return true
	} // fixed listing, not a router
	var ids []string
	if model == "" || json.Unmarshal([]byte(snapshot), &ids) != nil {
		return false
	}
	for _, id := range ids {
		if id == model {
			return true
		}
	}
	return false
}

// CredentialResolver must select by org, provider, scope AND immutable
// fingerprint, never by the provider's currently active key alone. Retained
// keys can reconcile their old generations after rotation. Unavailable/revoked
// credentials remain pending with a durable reason; no current-key fallback.
type CredentialResolver func(context.Context, string, models.InclusiveIdentity) (string, error)
type IdentityHTTPAdapters struct {
	HTTPAdapters
	Resolve CredentialResolver
}

func (a IdentityHTTPAdapters) Accepts(string, string) bool { return true } // scoped per attempt below
func (a IdentityHTTPAdapters) Generation(context.Context, string) (Generation, error) {
	return Generation{}, fmt.Errorf("attempt credential identity required")
}
func (a IdentityHTTPAdapters) GenerationFor(ctx context.Context, org string, attempt models.InclusiveAttempt) (Generation, error) {
	identity := attempt.Identity
	if a.Resolve == nil || identity.Provider == "" || identity.Provider != a.ProviderID || identity.Credential == "" || org == "" || (identity.Scope != "shared" && identity.Scope != org) {
		return Generation{}, fmt.Errorf("attempt credential identity unavailable; legacy or unbound generation remains pending")
	}
	token, err := a.Resolve(ctx, org, identity)
	if err != nil || token == "" || models.CredentialFingerprint(token) != identity.Credential {
		return Generation{}, fmt.Errorf("original credential unavailable or rotated without retention; generation remains pending")
	}
	adapter := a.HTTPAdapters
	adapter.GenerationToken = func() string { return token }
	return adapter.Generation(ctx, attempt.GenerationID)
}

// RetainedCredential is operator-supplied secret material. No field is logged or
// persisted in the billing journal. Empty OrgID denotes a shared credential.
type RetainedCredential struct{ OrgID, Provider, Token string }

func RetainedResolver(entries []RetainedCredential) CredentialResolver {
	copyEntries := append([]RetainedCredential(nil), entries...)
	return func(_ context.Context, org string, id models.InclusiveIdentity) (string, error) {
		for _, entry := range copyEntries {
			scope := entry.OrgID
			if scope == "" {
				scope = "shared"
			}
			if entry.Provider == id.Provider && scope == id.Scope && (scope == "shared" || scope == org) && models.CredentialFingerprint(entry.Token) == id.Credential {
				return entry.Token, nil
			}
		}
		return "", fmt.Errorf("credential identity not retained")
	}
}
