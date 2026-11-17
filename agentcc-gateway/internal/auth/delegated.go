package auth

import (
	"context"
	"strings"
	"time"
)

type delegatedContextKey struct{}
type delegatedContext struct {
	principal *APIKey
	grant     string
	claims    EnvelopeClaims
}

// WithDelegatedPrincipal is only called after the request envelope is verified.
// No network header can install this context value. A grant is never stored in
// metadata (which would leak into logs), or in the keystore.
func WithDelegatedPrincipal(ctx context.Context, c EnvelopeClaims, grant string) context.Context {
	exp := time.Unix(c.Exp, 0)
	metadata := map[string]string{"org_id": c.Org, "user_id": c.User, "workspace_id": "", "purpose": c.Kind}
	if c.Workspace != "-" {
		metadata["workspace_id"] = c.Workspace
	}
	if c.Kind == "playground" || c.Kind == "tryit" || c.Kind == "guardrail-test" {
		metadata["allow_policy_override"] = "true"
	}
	// Create currently leaves KeyType empty; keep the exact same default rather
	// than silently elevating a delegation to an internal or managed credential.
	principal := &APIKey{ID: "dlg_" + c.ID, Name: "delegated-" + c.Kind, Owner: c.User, Source: "delegated", Status: "active", Metadata: metadata, ExpiresAt: &exp}
	return context.WithValue(ctx, delegatedContextKey{}, delegatedContext{principal, grant, c})
}
func DelegatedPrincipal(ctx context.Context) *APIKey {
	d, _ := ctx.Value(delegatedContextKey{}).(delegatedContext)
	return d.principal
}

// DelegatedGrant is for the eval signer and in-process batch runner only.
func DelegatedGrant(ctx context.Context) (string, EnvelopeClaims, bool) {
	d, ok := ctx.Value(delegatedContextKey{}).(delegatedContext)
	return d.grant, d.claims, ok && d.principal != nil
}

// DelegatedModelCap is the model cap of a verified console-run grant: the
// ids its models field names (fe1c field 20) and true. It is nil and false
// for every other request, delegated or not. A console-run grant with no
// models (which validGrant refuses) is capped to nothing: empty and true. The
// grant itself is never returned.
func DelegatedModelCap(ctx context.Context) ([]string, bool) {
	d, ok := ctx.Value(delegatedContextKey{}).(delegatedContext)
	if !ok || d.principal == nil || d.claims.Kind != "console-run" {
		return nil, false
	}
	if d.claims.Models == "" {
		return []string{}, true
	}
	return strings.Split(d.claims.Models, ","), true
}

func WithDelegationFrom(ctx, from context.Context) context.Context {
	if d, ok := from.Value(delegatedContextKey{}).(delegatedContext); ok {
		return context.WithValue(ctx, delegatedContextKey{}, d)
	}
	return ctx
}
