package tenancy

import (
	"context"
	"net/http"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// Pool.Client adds the entry guard around build's credential-lifetime guard.
// Reach the factory capture through both without changing the shared unwrap
// helper or any of the existing cache-identity assertions.
func personCredentialClient(t *testing.T, client mcp.UpstreamClient) *fakeClient {
	t.Helper()
	for {
		guard, ok := client.(*timeoutClient)
		if !ok {
			break
		}
		client = guard.UpstreamClient
	}
	captured, ok := client.(*fakeClient)
	if !ok {
		t.Fatal("person credential did not reach the expected fake factory client")
	}
	return captured
}

// The 708J union keeps the exact credential-field oracle, including zero values
// for every other field, while requiring the lifetime-bound header source.
func requireExactPersonCredential(t *testing.T, client mcp.UpstreamClient, got, want mcp.AuthConfig, headers http.Header) {
	t.Helper()
	if got.Source == nil {
		t.Fatal("supplied credential has no lifetime-bound source")
	}
	fields := got
	fields.Source = nil // Compare a copy; the actual client's Source stays intact.
	if !reflect.DeepEqual(fields, want) {
		t.Fatal("upstream credential fields differ or an extra credential field is set")
	}
	cfg := personCredentialClient(t, client).cfg
	if cfg.Lifetime == nil || cfg.Lifetime.Err() != nil {
		t.Fatal("supplied credential must have a live construction lifetime")
	}
	actual, err := got.Source.Header(context.Background())
	if err != nil || !reflect.DeepEqual(actual, headers) {
		t.Fatal("credential source did not return exactly the supplied headers")
	}
	// Returned headers must not alias the pinned credential or add a hidden
	// fallback: another read still yields the exact original supplied map.
	actual.Set("X-Source-Mutation-Control", "synthetic")
	again, err := got.Source.Header(context.Background())
	if err != nil || !reflect.DeepEqual(again, headers) {
		t.Fatal("credential source leaked mutable or additional credential headers")
	}
}

func requirePersonCredentialInvalidated(t *testing.T, r *rig, client mcp.UpstreamClient, got mcp.AuthConfig) {
	t.Helper()
	underlying := personCredentialClient(t, client)
	builds, calls := r.factory.totalBuilds(), len(underlying.up.called())
	// A real store invalidation must cancel the same connection/source even
	// without another Resolve; a mere non-nil Source is not sufficient.
	r.push("org-a", 2, `{"enabled":true,"servers":{"mine":{"url":"`+urlA+`","credential":{"mode":"per_user","state":"revoked"}}}}`)
	if underlying.cfg.Lifetime.Err() != context.Canceled {
		t.Fatal("credential source lifetime survived store invalidation")
	}
	headers, err := got.Source.Header(context.Background())
	requireCredentialRefusal(t, err)
	if headers != nil {
		t.Fatal("invalidated source returned credential headers")
	}
	requireCredentialRefusal(t, credentialCall(client))
	if client.Healthy() || r.factory.totalBuilds() != builds || len(underlying.up.called()) != calls {
		t.Fatal("invalidated credential remained usable or reached the upstream")
	}
}
