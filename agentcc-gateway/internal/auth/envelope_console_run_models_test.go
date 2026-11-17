package auth

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"strconv"
	"strings"
	"testing"
	"time"
)

// consoleRunTestFASecret is the test-only FA grant secret fe1Fixture signs
// grants with. The gateway never verifies a grant's MAC.
const consoleRunTestFASecret = "test-only-fa-secret-unavailable-to-gateway"

// The fixed fe1c console-run grant FA reproduces byte for byte: fixed claims
// (iat 1700000000, exp 1700003600, id "c"*32, fe1TestOrg, fe1TestUser, sub
// chat:33333333-3333-4333-8333-333333333333, models
// "anthropic/claude-sonnet-5.5,gpt-4o-mini") MACed with
// consoleRunTestFASecret under fe1's HKDF (salt "fe1", info
// "fa>fa-eval-grant|k1" and 0x01).
const consoleRunFixtureCanonical = "fe1c\ngrant\nk1\nfa\nfa-eval-grant\n-\n-\n1700000000\n1700003600\ncccccccccccccccccccccccccccccccc\nconsole-run\n11111111-1111-4111-8111-111111111111\n-\n22222222-2222-4222-8222-222222222222\nperson\nchat:33333333-3333-4333-8333-333333333333\neval\n-\n-\nanthropic/claude-sonnet-5.5,gpt-4o-mini"
const consoleRunFixtureToken = "fe1c.ZmUxYwpncmFudAprMQpmYQpmYS1ldmFsLWdyYW50Ci0KLQoxNzAwMDAwMDAwCjE3MDAwMDM2MDAKY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2MKY29uc29sZS1ydW4KMTExMTExMTEtMTExMS00MTExLTgxMTEtMTExMTExMTExMTExCi0KMjIyMjIyMjItMjIyMi00MjIyLTgyMjItMjIyMjIyMjIyMjIyCnBlcnNvbgpjaGF0OjMzMzMzMzMzLTMzMzMtNDMzMy04MzMzLTMzMzMzMzMzMzMzMwpldmFsCi0KLQphbnRocm9waWMvY2xhdWRlLXNvbm5ldC01LjUsZ3B0LTRvLW1pbmk.cgDPJ4BZ3NreH1Zjq-Bi5m2i8DNvYd0Cs-04CSQE1l4"

// fe1cRaw is an independent fe1c wire encoder (it calls no production
// validation or MAC): "fe1c." + base64url(canonical) + "." + base64url(mac),
// the canonical being fields joined by "\n" exactly as given, the MAC fe1's
// HKDF-SHA256 (salt "fe1", info iss>aud|kid and 0x01) over the canonical.
func fe1cRaw(fields []string, secret string) string {
	raw := []byte(strings.Join(fields, "\n"))
	extract := hmac.New(sha256.New, []byte("fe1"))
	extract.Write([]byte(secret))
	expand := hmac.New(sha256.New, extract.Sum(nil))
	expand.Write([]byte(fields[3] + ">" + fields[4] + "|" + fields[2]))
	expand.Write([]byte{1})
	mac := hmac.New(sha256.New, expand.Sum(nil))
	mac.Write(raw)
	return "fe1c." + base64.RawURLEncoding.EncodeToString(raw) + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

// fe1cGrantFields spells out the 20 fe1c fields of g with models as field 20,
// independently of EnvelopeClaims.fields.
func fe1cGrantFields(g EnvelopeClaims, models string) []string {
	return []string{"fe1c", g.Mode, g.Kid, g.Iss, g.Aud, g.Method, g.Route, strconv.FormatInt(g.Iat, 10), strconv.FormatInt(g.Exp, 10), g.ID, g.Kind, g.Org, g.Workspace, g.User, g.Via, g.Sub, g.Scope, g.Body, g.Grant, models}
}

// verifyConsoleRunGrant signs an fe1c grant of kind with models, binds a
// request envelope on the delegated chat route to it, and verifies the pair.
func verifyConsoleRunGrant(kind, models string) (EnvelopeClaims, string, error) {
	c, g, _, body, now := fe1Fixture()
	g.Kind = kind
	grant := fe1cRaw(fe1cGrantFields(g, models), consoleRunTestFASecret)
	c.Scope = kind
	c.Grant = EnvelopeDigest([]byte(grant))
	v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-10*time.Second))
	_, got, err := v.VerifyDelegated(fe1Raw(c.fields(), fe1TestSecret), grant, "POST", c.Route, body, now)
	return got, grant, err
}

// The fixed fixture: the canonical bytes and the token are pinned literally,
// the independent encoder reproduces both, and the gateway reads the claims,
// models included, from exactly these bytes.
func TestEnvelopeConsoleRunModelsFixture(t *testing.T) {
	g := EnvelopeClaims{Mode: "grant", Kid: "k1", Iss: "fa", Aud: "fa-eval-grant", Method: "-", Route: "-", Iat: 1700000000, Exp: 1700003600, ID: strings.Repeat("c", 32), Kind: "console-run", Org: fe1TestOrg, Workspace: "-", User: fe1TestUser, Via: "person", Sub: "chat:33333333-3333-4333-8333-333333333333", Scope: "eval", Body: "-", Grant: "-"}
	const models = "anthropic/claude-sonnet-5.5,gpt-4o-mini"
	fields := fe1cGrantFields(g, models)
	if got := strings.Join(fields, "\n"); got != consoleRunFixtureCanonical {
		t.Fatalf("canonical = %q, want %q", got, consoleRunFixtureCanonical)
	}
	if got := fe1cRaw(fields, consoleRunTestFASecret); got != consoleRunFixtureToken {
		t.Fatalf("token = %q, want %q", got, consoleRunFixtureToken)
	}
	g.Models = models
	if got := strings.Join(g.fields(), "\n"); got != consoleRunFixtureCanonical {
		t.Fatalf("production canonical = %q, want %q", got, consoleRunFixtureCanonical)
	}
	parsed, raw, mac, err := parseEnvelope(consoleRunFixtureToken)
	if err != nil {
		t.Fatalf("the fixture grant was refused: %v", err)
	}
	if parsed != g || string(raw) != consoleRunFixtureCanonical {
		t.Fatalf("parsed = %+v (canonical %q), want %+v", parsed, raw, g)
	}
	if !hmac.Equal(mac, envelopeMAC(consoleRunTestFASecret, parsed, raw)) {
		t.Fatal("the fixture MAC is not fe1's HKDF MAC under the FA test secret")
	}
	// Bound by digest to a request envelope at the fixture's iat, as today.
	now := time.Unix(1700000000, 100000000)
	body := []byte(`{"model":"gpt-4o-mini","messages":[]}`)
	c := EnvelopeClaims{Mode: "request", Kid: "k1", Iss: "fa", Aud: "gw-delegated", Method: "POST", Route: "/-/delegated/v1/chat/completions", Iat: now.Unix(), Exp: now.Unix() + 60, ID: strings.Repeat("d", 32), Kind: "-", Org: fe1TestOrg, Workspace: "-", User: fe1TestUser, Via: "grant:" + g.ID, Sub: "-", Scope: "console-run", Body: EnvelopeDigest(body), Grant: EnvelopeDigest([]byte(consoleRunFixtureToken))}
	v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-10*time.Second))
	_, bound, err := v.VerifyDelegated(fe1Raw(c.fields(), fe1TestSecret), consoleRunFixtureToken, "POST", c.Route, body, now)
	if err != nil {
		t.Fatalf("the fixture grant did not bind: %v", err)
	}
	if bound.Models != models || !bound.PermitsModel("gpt-4o-mini") || !bound.PermitsModel("anthropic/claude-sonnet-5.5") {
		t.Fatalf("bound grant models = %q", bound.Models)
	}
	for _, model := range []string{"", "GPT-4o-mini", "gpt-4o", "gpt-4o-mini ", "anthropic/claude-sonnet-5.5,gpt-4o-mini"} {
		if bound.PermitsModel(model) {
			t.Errorf("the grant permits %q", model)
		}
	}
}

// An fe1c console-run grant is accepted and VerifyDelegated returns its
// models; an fe1 console-run grant is refused.
func TestEnvelopeConsoleRunGrantMustBeFe1cWithModels(t *testing.T) {
	g, _, err := verifyConsoleRunGrant("console-run", "anthropic/claude-sonnet-5.5,gpt-4o-mini")
	if err != nil {
		t.Fatalf("an fe1c console-run grant was refused: %v", err)
	}
	if g.Kind != "console-run" || g.Models != "anthropic/claude-sonnet-5.5,gpt-4o-mini" {
		t.Fatalf("grant = %+v", g)
	}

	c, fe1, _, body, now := fe1Fixture()
	fe1.Kind = "console-run"
	grant := fe1Raw(fe1.fields(), consoleRunTestFASecret)
	if !strings.HasPrefix(grant, "fe1.") {
		t.Fatalf("fe1 console-run grant %q", grant)
	}
	if _, _, _, err := parseEnvelope(grant); err == nil {
		t.Error("an fe1 console-run grant parsed")
	}
	c.Scope = "console-run"
	c.Grant = EnvelopeDigest([]byte(grant))
	v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-10*time.Second))
	if _, _, err := v.VerifyDelegated(fe1Raw(c.fields(), fe1TestSecret), grant, "POST", c.Route, body, now); err == nil {
		t.Error("an fe1 console-run grant was accepted")
	}
}

// fe1c is a console-run grant's alone: any other grant kind in fe1c is
// refused (the same claims in fe1 are accepted), as is an fe1c request
// envelope, and the version and the field count must agree.
func TestEnvelopeFe1cOnlyForConsoleRunGrants(t *testing.T) {
	_, base, _, _, _ := fe1Fixture()
	for _, kind := range []string{"playground", "tryit", "guardrail-test", "batch"} {
		g := base
		g.Kind = kind
		if kind == "batch" {
			g.Sub = "batch:33333333-3333-4333-8333-333333333333"
		}
		if _, _, _, err := parseEnvelope(fe1Raw(g.fields(), consoleRunTestFASecret)); err != nil {
			t.Fatalf("control: the fe1 %s grant was refused: %v", kind, err)
		}
		if _, _, _, err := parseEnvelope(fe1cRaw(fe1cGrantFields(g, "gpt-4o-mini"), consoleRunTestFASecret)); err == nil {
			t.Errorf("an fe1c %s grant was accepted", kind)
		}
		// An empty field 20 does not make an fe1c token an fe1 grant.
		if _, _, _, err := parseEnvelope(fe1cRaw(fe1cGrantFields(g, ""), consoleRunTestFASecret)); err == nil {
			t.Errorf("an fe1c %s grant with an empty models field was accepted", kind)
		}
	}

	c, _, grant, body, now := fe1Fixture()
	request := append(c.fields(), "gpt-4o-mini")
	request[0] = "fe1c"
	v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-10*time.Second))
	if _, _, err := v.VerifyDelegated(fe1cRaw(request, fe1TestSecret), grant, "POST", c.Route, body, now); err == nil {
		t.Error("an fe1c request envelope was accepted")
	}
	if _, _, _, err := parseEnvelope(fe1cRaw(request, fe1TestSecret)); err == nil {
		t.Error("an fe1c request envelope parsed")
	}
	c.Models = "gpt-4o-mini"
	if _, err := SignEnvelope(fe1TestSecret, c); err == nil {
		t.Error("the gateway signed an fe1c request envelope")
	}

	g := base
	g.Kind = "console-run"
	twenty := fe1cGrantFields(g, "gpt-4o-mini")
	fe1With20 := append([]string(nil), twenty...)
	fe1With20[0] = "fe1"
	if _, _, _, err := parseEnvelope(fe1Raw(fe1With20, consoleRunTestFASecret)); err == nil {
		t.Error("an fe1 token with 20 fields was accepted")
	}
	if _, _, _, err := parseEnvelope(fe1cRaw(twenty[:19], consoleRunTestFASecret)); err == nil {
		t.Error("an fe1c token with 19 fields was accepted")
	}
	mixed := fe1cRaw(twenty, consoleRunTestFASecret)
	if _, _, _, err := parseEnvelope("fe1." + strings.TrimPrefix(mixed, "fe1c.")); err == nil {
		t.Error("an fe1c canonical under the fe1 prefix was accepted")
	}
	if _, _, _, err := parseEnvelope("fe1c." + strings.Repeat("A", 4092)); err == nil {
		t.Error("a token over 4096 bytes was accepted")
	}
}

// The models field's grammar: 1 to 8 ids, each
// ^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$, strictly ascending, at most 1700
// bytes. A valid 8-id field is accepted, even when the token passes fe1's
// 2048-byte cap.
func TestEnvelopeConsoleRunModelsGrammar(t *testing.T) {
	long := func(n int) []string {
		ids := make([]string, n)
		for i := range ids {
			ids[i] = string(rune('a'+i)) + strings.Repeat("x", 199)
		}
		return ids
	}
	shortNine := []string{"a", "b", "c", "d", "e", "f", "g", "h", "i"}
	for name, models := range map[string]string{
		"empty":                    "",
		"dash":                     "-",
		"unsorted":                 "gpt-4o-mini,anthropic/claude-sonnet-5.5",
		"duplicate":                "gpt-4o-mini,gpt-4o-mini",
		"nine ids":                 strings.Join(shortNine, ","),
		"id with a space":          "gpt 4o-mini",
		"empty id between commas":  "a,,b",
		"leading comma":            ",a",
		"trailing comma":           "a,",
		"id of 201 chars":          strings.Repeat("a", 201),
		"id starting with a dash":  "-gpt",
		"field over 1700 bytes":    strings.Join(long(9), ","),
		"id with a non-ascii rune": "gpt-4o-minié",
	} {
		if _, _, err := verifyConsoleRunGrant("console-run", models); err == nil {
			t.Errorf("%s: models %q accepted", name, models)
		}
	}
	if len(strings.Join(long(9), ",")) <= 1700 {
		t.Fatal("the over-1700 row is not over 1700 bytes")
	}

	eight := strings.Join(long(8), ",")
	g, grant, err := verifyConsoleRunGrant("console-run", eight)
	if err != nil {
		t.Fatalf("a valid 8-id models field was refused: %v", err)
	}
	if g.Models != eight || len(grant) <= 2048 || len(grant) > 4096 {
		t.Fatalf("models %q, grant length %d", g.Models, len(grant))
	}
	for _, models := range []string{"a", strings.Repeat("a", 200), "A,a", "anthropic/claude-sonnet-5.5,gpt-4o-mini", "Org.v2:model@1+beta/x_y-z"} {
		if _, _, err := verifyConsoleRunGrant("console-run", models); err != nil {
			t.Errorf("valid models %q refused: %v", models, err)
		}
	}
}

// Every other grant kind and every request envelope is fe1, byte for byte as
// before fe1c: fe1Fixture's playground grant still verifies, with no models.
func TestEnvelopeFe1GrantsUnchangedByFe1c(t *testing.T) {
	c, g, grant, body, now := fe1Fixture()
	if f := c.fields(); len(f) != 19 || f[0] != "fe1" {
		t.Fatalf("request fields = %q", f)
	}
	if f := g.fields(); len(f) != 19 || f[0] != "fe1" || grant != fe1Raw(f, consoleRunTestFASecret) {
		t.Fatalf("grant fields = %q", f)
	}
	v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-10*time.Second))
	_, got, err := v.VerifyDelegated(fe1Raw(c.fields(), fe1TestSecret), grant, "POST", c.Route, body, now)
	if err != nil {
		t.Fatalf("the fe1 playground grant was refused: %v", err)
	}
	if got != g || got.Models != "" {
		t.Fatalf("grant = %+v, want %+v", got, g)
	}
	token, err := SignEnvelope(fe1TestSecret, c)
	if err != nil || !strings.HasPrefix(token, "fe1.") || token != fe1Raw(c.fields(), fe1TestSecret) {
		t.Fatalf("request signing changed: %q %v", token, err)
	}
}
