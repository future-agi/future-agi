package models

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
)

// InclusiveIdentity binds an attempt to the actual upstream credential and its
// organization scope. Only a one-way fingerprint is durable; never the token.
// SnapshotModels is the frozen router universe, not a refreshed catalogue.
// This private journal extension is NOT sent into the v1/v2 financial contract.
type InclusiveIdentity struct {
	Provider       string
	Credential     string
	Scope          string // "shared" or the exact organization id
	SnapshotModels string
}

func CredentialFingerprint(token string) string {
	if token == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(token))
	return "sha256:" + hex.EncodeToString(sum[:])
}

type inclusiveCredentialKey struct{}
type inclusiveCredential struct{ provider, token string }

// WithInclusiveCredential pins the credential for this invocation, including
// streams. It is never exported in request JSON, trace snapshots or logs.
func WithInclusiveCredential(ctx context.Context, provider, token string) context.Context {
	return context.WithValue(ctx, inclusiveCredentialKey{}, inclusiveCredential{provider, token})
}
func WithoutInclusiveCredential(ctx context.Context) context.Context {
	return context.WithValue(ctx, inclusiveCredentialKey{}, inclusiveCredential{})
}
func InclusiveCredential(ctx context.Context, provider, fallback string) string {
	if c, ok := ctx.Value(inclusiveCredentialKey{}).(inclusiveCredential); ok && c.provider == provider {
		return c.token
	}
	return fallback
}

// CopyInclusiveCredential preserves only invocation identity, not cancellation
// or deadlines, when streaming detaches from the startup failover context.
func CopyInclusiveCredential(dst, src context.Context) context.Context {
	if value, ok := src.Value(inclusiveCredentialKey{}).(inclusiveCredential); ok {
		return context.WithValue(dst, inclusiveCredentialKey{}, value)
	}
	return dst
}
