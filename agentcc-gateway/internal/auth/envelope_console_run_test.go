package auth

import (
	"context"
	"testing"
	"time"
)

// consoleRunFixture signs a console-run person grant that lives life seconds
// and a request envelope for route bound to it, as FA signs a Console model
// call. A console-run grant is fe1c and names the models the run may call
// (fe1Fixture's body asks for "test"); any other kind stays an fe1 grant.
func consoleRunFixture(kind, route string, life int64) (string, string, []byte, time.Time) {
	c, g, _, body, now := fe1Fixture()
	g.Kind = kind
	g.Exp = g.Iat + life
	grant := fe1Raw(g.fields(), "test-only-fa-secret-unavailable-to-gateway")
	if kind == "console-run" {
		grant = fe1cRaw(fe1cGrantFields(g, "test"), "test-only-fa-secret-unavailable-to-gateway")
	}
	c.Route = route
	c.Scope = kind
	c.Grant = EnvelopeDigest([]byte(grant))
	return fe1Raw(c.fields(), fe1TestSecret), grant, body, now
}

// One EE grant kind, console-run, is accepted, and only on the delegated chat
// route: a Console run's model calls, for up to an hour per grant. Every other
// EE kind stays refused, and a console-run grant opens no other route.
func TestEnvelopeConsoleRunGrantOnTheChatRouteOnly(t *testing.T) {
	const chat = "/-/delegated/v1/chat/completions"
	verify := func(kind, route string, life int64) (EnvelopeClaims, error) {
		token, grant, body, now := consoleRunFixture(kind, route, life)
		v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-10*time.Second))
		_, g, err := v.VerifyDelegated(token, grant, "POST", route, body, now)
		return g, err
	}
	g, err := verify("console-run", chat, 3600)
	if err != nil {
		t.Fatalf("a console-run grant of an hour on the chat route was refused: %v", err)
	}
	if g.Kind != "console-run" || g.Sub != "chat:33333333-3333-4333-8333-333333333333" || g.Models != "test" {
		t.Fatalf("grant = %+v", g)
	}
	if _, err := verify("console-run", chat, 3601); err == nil {
		t.Error("a console-run grant longer than an hour was accepted")
	}
	if _, err := verify("console-run", "/-/batches", 3600); err == nil {
		t.Error("a console-run grant opened the delegated batch route")
	}
	for _, kind := range []string{"ee-serve", "console", "workforce", "console-runs", "agent-run"} {
		if _, err := verify(kind, chat, 600); err == nil {
			t.Errorf("EE grant kind %q was accepted", kind)
		}
	}
}

// A console-run principal is the org's person, as the Try it principal is,
// with no key type of its own and no guardrail policy override.
func TestConsoleRunPrincipalIsThePersonWithoutPolicyOverride(t *testing.T) {
	_, g, grant, _, _ := fe1Fixture()
	g.Kind = "console-run"
	key := DelegatedPrincipal(WithDelegatedPrincipal(context.Background(), g, grant))
	if key == nil || key.Metadata["org_id"] != g.Org || key.Metadata["user_id"] != g.User || key.Metadata["purpose"] != "console-run" {
		t.Fatalf("principal = %+v", key)
	}
	if key.KeyType != "" {
		t.Fatalf("console-run principal key type %q, want the default", key.KeyType)
	}
	if key.Metadata["allow_policy_override"] == "true" {
		t.Fatal("console-run principal may override the org's guardrail policy")
	}
}
