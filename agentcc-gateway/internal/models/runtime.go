package models

import "time"

// RuntimeTokenType is the typ claim of every runtime token.
const RuntimeTokenType = "futureagi-runtime-token"

// Runtime-token scopes: the gateway surfaces a token may call.
const (
	ScopeGatewayChat       = "gateway:chat"
	ScopeGatewayResponses  = "gateway:responses"
	ScopeGatewayMessages   = "gateway:messages"
	ScopeGatewayEmbeddings = "gateway:embeddings"
	ScopeGatewayModels     = "gateway:models"
)

// RuntimeIdentity is what a verified runtime token says about the call
// (runtime-token-claims-v1). Its org,
// deployment, agent and root run scope are authoritative: headers may add
// per-call fields, name descendant runs under the root and lower budgets, but
// never move the call to another org, agent or budget scope.
type RuntimeIdentity struct {
	TokenID   string // jti, used for revocation
	Issuer    string
	IssuedAt  time.Time
	ExpiresAt time.Time

	OrgID        string
	DeploymentID string
	AgentID      string
	WorkspaceID  string
	AgentVersion string
	Env          string
	Framework    string

	// RootRunID is the root of the run scope the token covers. A call with
	// no run header runs as the root; a descendant run is accepted under the
	// runtime token's root run scope rules (ApplyRuntimeClaims).
	RootRunID   string
	ParentRunID string
	SessionID   string

	// BudgetScope defaults to RootRunID; every descendant run shares it.
	BudgetScope string
	BudgetUSD   *float64
	BillingMode string // "byok", "managed" or "any"; it can only restrict

	Scopes    []string
	KeyPolicy *RuntimeKeyPolicy

	// DescendantVerified is set when the request's run is a descendant the
	// gateway has already accepted under this root (its lineage set).
	DescendantVerified bool
}

// RuntimeKeyPolicy is the ACL a runtime token applies as its synthetic key's.
type RuntimeKeyPolicy struct {
	AllowedModels    []string
	AllowedProviders []string
}

// HasScope reports whether the token grants scope.
func (r *RuntimeIdentity) HasScope(scope string) bool {
	if r == nil {
		return false
	}
	for _, s := range r.Scopes {
		if s == scope {
			return true
		}
	}
	return false
}

// Clone returns a deep copy of r, or nil.
func (r *RuntimeIdentity) Clone() *RuntimeIdentity {
	if r == nil {
		return nil
	}
	c := *r
	c.BudgetUSD = cloneFloat(r.BudgetUSD)
	c.Scopes = cloneStrings(r.Scopes)
	if r.KeyPolicy != nil {
		c.KeyPolicy = &RuntimeKeyPolicy{
			AllowedModels:    cloneStrings(r.KeyPolicy.AllowedModels),
			AllowedProviders: cloneStrings(r.KeyPolicy.AllowedProviders),
		}
	}
	return &c
}
