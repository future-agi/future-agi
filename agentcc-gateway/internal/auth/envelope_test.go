package auth

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

const fe1TestSecret = "test-only-fe1-admin-secret-not-a-deployment-secret"
const fe1TestOrg = "11111111-1111-4111-8111-111111111111"
const fe1TestUser = "22222222-2222-4222-8222-222222222222"

func fe1Fixture() (EnvelopeClaims, EnvelopeClaims, string, []byte, time.Time) {
	now := time.Unix(1700000000, 100000000)
	body := []byte(`{"model":"test","messages":[]}`)
	g := EnvelopeClaims{Mode: "grant", Kid: "k1", Iss: "fa", Aud: "fa-eval-grant", Method: "-", Route: "-", Iat: now.Unix(), Exp: now.Unix() + 600, ID: strings.Repeat("a", 32), Kind: "playground", Org: fe1TestOrg, Workspace: "-", User: fe1TestUser, Via: "person", Sub: "chat:33333333-3333-4333-8333-333333333333", Scope: "eval", Body: "-", Grant: "-"}
	grant := fe1Raw(g.fields(), "test-only-fa-secret-unavailable-to-gateway")
	c := EnvelopeClaims{Mode: "request", Kid: "k1", Iss: "fa", Aud: "gw-delegated", Method: "POST", Route: "/-/delegated/v1/chat/completions", Iat: now.Unix(), Exp: now.Unix() + 60, ID: strings.Repeat("b", 32), Kind: "-", Org: g.Org, Workspace: g.Workspace, User: g.User, Via: "grant:" + g.ID, Sub: "-", Scope: g.Kind, Body: EnvelopeDigest(body), Grant: EnvelopeDigest([]byte(grant))}
	return c, g, grant, body, now
}

// Independent RFC5869/wire encoder: does not call production validation or MAC.
func fe1Raw(fields []string, secret string) string {
	raw := []byte(strings.Join(fields, "\n"))
	extract := hmac.New(sha256.New, []byte("fe1"))
	extract.Write([]byte(secret))
	expand := hmac.New(sha256.New, extract.Sum(nil))
	expand.Write([]byte(fields[3] + ">" + fields[4] + "|" + fields[2]))
	expand.Write([]byte{1})
	mac := hmac.New(sha256.New, expand.Sum(nil))
	mac.Write(raw)
	return "fe1." + base64.RawURLEncoding.EncodeToString(raw) + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}
func TestEnvelope733CanonicalAndEvalSigner(t *testing.T) {
	c, _, grant, body, now := fe1Fixture()
	token, err := SignEnvelope(fe1TestSecret, c)
	if err != nil || token != fe1Raw(c.fields(), fe1TestSecret) {
		t.Fatal("request HKDF/MAC mismatch")
	}
	v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-10*time.Second))
	if _, _, err = v.VerifyDelegated(token, grant, "POST", c.Route, body, now); err != nil {
		t.Fatal(err)
	}
	c.Iss = "gateway"
	c.Aud = "fa-eval"
	c.Route = "/sdk/api/v1/eval/"
	c.Scope = "eval"
	if _, err = SignEnvelope(fe1TestSecret, c); err != nil {
		t.Fatal("gateway eval signing rejected:", err)
	}
	c.Mode = "grant"
	if _, err = SignEnvelope(fe1TestSecret, c); err == nil {
		t.Fatal("gateway signer issued a grant")
	}
}
func TestEnvelope733QuickRestartAndConcurrentReplay(t *testing.T) {
	c, _, grant, body, now := fe1Fixture()
	c.Iat += 4
	c.Exp += 4
	token := fe1Raw(c.fields(), fe1TestSecret)
	v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute))
	var wg sync.WaitGroup
	results := make(chan error, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, _, err := v.VerifyDelegated(token, grant, "POST", c.Route, body, now)
			results <- err
		}()
	}
	wg.Wait()
	close(results)
	success := 0
	for err := range results {
		if err == nil {
			success++
		}
	}
	if success != 1 {
		t.Fatalf("accepted %d replays", success)
	}
	epoch := now.Add(time.Second)
	reboot := NewEnvelopeVerifier(fe1TestSecret, epoch)
	if _, _, err := reboot.VerifyDelegated(token, grant, "POST", c.Route, body, epoch); err == nil {
		t.Fatal("future-dated pre-restart replay accepted")
	}
	if reboot.Ready(epoch.Add(EnvelopeSkew-time.Nanosecond)) || !reboot.Ready(epoch.Add(EnvelopeSkew)) {
		t.Fatal("readiness does not honor exact epoch+skew")
	}
	// Fractional epoch must not be rounded down, including a subsecond restart.
	before := time.Unix(now.Unix(), 0)
	c.Iat = before.Unix() + 5
	c.Exp = c.Iat + 60
	token = fe1Raw(c.fields(), fe1TestSecret)
	previous := NewEnvelopeVerifier(fe1TestSecret, before.Add(-time.Minute))
	if _, _, err := previous.VerifyDelegated(token, grant, "POST", c.Route, body, before); err != nil {
		t.Fatal(err)
	}
	next := NewEnvelopeVerifier(fe1TestSecret, before.Add(time.Nanosecond))
	if _, _, err := next.VerifyDelegated(token, grant, "POST", c.Route, body, before.Add(time.Nanosecond)); err == nil {
		t.Fatal("same-second restart replay accepted")
	}
}
func TestEnvelope733AllFieldsAndBindings(t *testing.T) {
	c, _, grant, body, now := fe1Fixture()
	verify := func(token, forwarded, method, path string, b []byte, at time.Time) error {
		_, _, err := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute)).VerifyDelegated(token, forwarded, method, path, b, at)
		return err
	}
	for i := range c.fields() {
		f := c.fields()
		f[i] = "invalid"
		if err := verify(fe1Raw(f, fe1TestSecret), grant, "POST", c.Route, body, now); err == nil {
			t.Fatalf("accepted invalid field %d", i)
		}
	}
	token := fe1Raw(c.fields(), fe1TestSecret)
	for name, err := range map[string]error{
		"body":         verify(token, grant, "POST", c.Route, []byte("tampered"), now),
		"method":       verify(token, grant, "GET", c.Route, body, now),
		"route":        verify(token, grant, "POST", "/-/batches", body, now),
		"grant":        verify(token, grant+"x", "POST", c.Route, body, now),
		"expired":      verify(token, grant, "POST", c.Route, body, now.Add(61*time.Second)),
		"future":       verify(token, grant, "POST", c.Route, body, now.Add(-6*time.Second)),
		"wrong secret": verify(fe1Raw(c.fields(), "test-only-different-secret"), grant, "POST", c.Route, body, now),
		"oversize":     verify(strings.Repeat("x", 2049), grant, "POST", c.Route, body, now),
	} {
		if err == nil {
			t.Errorf("accepted %s", name)
		}
	}
	c.Via = "grant:" + strings.Repeat("c", 32)
	if err := verify(fe1Raw(c.fields(), fe1TestSecret), grant, "POST", c.Route, body, now); err == nil {
		t.Fatal("mismatched jti accepted")
	}
}
func TestEnvelope733GrantGrammarAndNoEELaunch(t *testing.T) {
	_, g, _, _, _ := fe1Fixture()
	for name, mutate := range map[string]func(*EnvelopeClaims){
		"kind":     func(c *EnvelopeClaims) { c.Kind = "ee-serve" },
		"aud":      func(c *EnvelopeClaims) { c.Aud = "collector-ingest" },
		"subject":  func(c *EnvelopeClaims) { c.Sub += "extra" },
		"scope":    func(c *EnvelopeClaims) { c.Scope = "traces" },
		"gateway":  func(c *EnvelopeClaims) { c.Iss = "gateway" },
		"person":   func(c *EnvelopeClaims) { c.User = "-" },
		"via":      func(c *EnvelopeClaims) { c.Via = "svc" },
		"lifetime": func(c *EnvelopeClaims) { c.Exp = c.Iat + 901 },
		"method":   func(c *EnvelopeClaims) { c.Method = "POST" },
		"route":    func(c *EnvelopeClaims) { c.Route = "/" },
		"body":     func(c *EnvelopeClaims) { c.Body = strings.Repeat("a", 64) },
		"grant":    func(c *EnvelopeClaims) { c.Grant = strings.Repeat("a", 64) },
	} {
		bad := g
		mutate(&bad)
		if _, _, _, err := parseEnvelope(fe1Raw(bad.fields(), fe1TestSecret)); err == nil {
			t.Errorf("accepted %s", name)
		}
	}
}
func TestDelegated733PrincipalMetadataAndNoKeyStore(t *testing.T) {
	_, g, grant, _, _ := fe1Fixture()
	keys := NewKeyStore(config.AuthConfig{})
	for _, kind := range []string{"playground", "tryit", "guardrail-test", "batch"} {
		g.Kind = kind
		ctx := WithDelegatedPrincipal(context.Background(), g, grant)
		key := DelegatedPrincipal(ctx)
		if key.ID != "dlg_"+g.ID || key.Metadata["org_id"] != g.Org || key.Metadata["user_id"] != g.User || key.Owner != g.User {
			t.Fatal("principal differs from claims")
		}
		if (key.Metadata["allow_policy_override"] == "true") != (kind != "batch") {
			t.Fatal("override scope escalated")
		}
		defaultKey, _ := keys.Create("test", "person", nil, nil, nil)
		if key.KeyType != defaultKey.KeyType {
			t.Fatal("delegated key type differs from Create")
		}
		if key.KeyHash != "" {
			t.Fatal("ephemeral principal has a credential")
		}
	}
	if len(keys.List()) != 4 {
		t.Fatal("delegation changed key count")
	}
}
