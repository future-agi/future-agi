package mcp

// TestSIEMScalarCorpusExport writes the gateway's span-scalar decisions
// (telemetry.SanitizeText, with no org redactor and with each org redactor
// below, built by the tenant store) over the G16 frozen privacy corpus, the
// scalar shape fixtures, shape-fitting carriers of the same synthetic
// credentials and a seeded fuzz set, to AGENTCC_SIEM_CORPUS_OUT. The Python
// SIEM export parity test (futureagi/agentcc/tests/test_siem_scalar_parity.py)
// asserts SIEM export never keeps a value the gateway redacts. Values never
// reach a log; without the variable the test is skipped.

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"os"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/telemetry"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type siemCorpusFixture struct {
	Group   string            `json:"group"`
	Name    string            `json:"name"`
	Value   string            `json:"value"`
	Gateway map[string]string `json:"gateway"`
}

var siemCorpusPrivacy = map[string]json.RawMessage{
	"full":     json.RawMessage(`{"enabled":true,"mode":"full"}`),
	"patterns": json.RawMessage(`{"enabled":true,"mode":"patterns","redact_patterns":[{"name":"cust","pattern":"CUST-[0-9]+"},{"name":"run","pattern":"^run_"}]}`),
	"default":  json.RawMessage(`{"enabled":true,"redact_patterns":[{"name":"word","pattern":"(?i)plan+er"}]}`),
	"off":      json.RawMessage(`{"enabled":false,"mode":"full"}`),
}

func siemCorpusRedactors(t *testing.T) map[string]*privacy.Redactor {
	store := tenant.NewStore()
	redactors := map[string]*privacy.Redactor{"nil": nil}
	for name, section := range siemCorpusPrivacy {
		var cfg tenant.OrgConfig
		if json.Unmarshal([]byte(`{"privacy":`+string(section)+`}`), &cfg) != nil {
			t.Fatal("privacy section did not parse")
		}
		orgID := "siem-corpus-" + name
		store.Set(orgID, &cfg)
		redactors[name] = store.Redactor(orgID)
	}
	return redactors
}

func TestSIEMScalarCorpusExport(t *testing.T) {
	out := os.Getenv("AGENTCC_SIEM_CORPUS_OUT")
	if out == "" {
		t.Skip("AGENTCC_SIEM_CORPUS_OUT not set")
	}
	redactors := siemCorpusRedactors(t)
	var fixtures []siemCorpusFixture
	add := func(group, name, value string) {
		decisions := make(map[string]string, len(redactors))
		for rn, r := range redactors {
			decisions[rn] = telemetry.SanitizeText(value, r)
		}
		fixtures = append(fixtures, siemCorpusFixture{group, name, value, decisions})
	}
	raw64 := base64.RawURLEncoding.EncodeToString
	std64 := base64.StdEncoding.EncodeToString
	rawStd64 := base64.RawStdEncoding.EncodeToString

	for _, f := range independentPrivacyCases() {
		raw, err := json.Marshal(f.value)
		if err != nil {
			t.Fatal("fixture encoding failed")
		}
		add("frozen", f.name, string(raw))
		if m, ok := f.value.(map[string]any); ok {
			if text, ok := m["message"].(string); ok {
				add("frozen-raw", f.name+"-raw", text)
				add("frozen-b64url", f.name+"-b64url", raw64([]byte(text)))
				add("frozen-b64std", f.name+"-b64std", rawStd64([]byte(text)))
			}
		}
	}

	shapeOpaque := raw64([]byte(strings.Repeat("synthetic-scalar-fixture-", 2)))
	for i, v := range []string{
		`{"message":"` + shapeOpaque + `"}`, "Authorization: Basic\r\n " + shapeOpaque,
		"hello\n" + shapeOpaque, strings.Repeat("a", 257), "name with spaces",
		"https://" + "sk-" + shapeOpaque[:32] + ".invalid", "password=" + shapeOpaque,
		std64([]byte("Authorization: Basic " + shapeOpaque)), "tool_call_01\n",
	} {
		add("shape", fmt.Sprintf("shape-%02d", i), v)
	}

	opaque := raw64([]byte(strings.Repeat("independent-synthetic-fixture-", 2)))
	basic := std64([]byte("fixture-user:" + opaque))
	provider := "sk-" + opaque[:32]
	jwtHead := raw64([]byte(`{"alg":"HS256","typ":"JWT"}`))
	fields := []string{"AUTHORIZATION", "Proxy-Authorization", "Cookie", "Set-Cookie", "X-API-Key", "X_Auth_42", "Ocp-Apim-Subscription-Key", "X-Amz-Security-Token", "X-Hub-Signature-256", "CF-Access-Jwt-Assertion", "clientSecret", "privateKey", "refreshToken", "sessionId", "credential", "passWord", "X.C_o-o k/i/e99", "Assertion", "Subscription", "api_key", "passwd", "pwd", "passphrase", "clientPassphrase", "jwk", "p%61ssword", "%61%70%69%5F%6B%65%79"}
	for i, field := range fields {
		for j, sep := range []string{":", "=", ": ", "/", "::"} {
			add("derived", fmt.Sprintf("field-%02d-%d", i, j), field+sep+opaque)
		}
		add("derived", fmt.Sprintf("field-%02d-b64url", i), raw64([]byte(field+": "+opaque)))
		add("derived", fmt.Sprintf("field-%02d-b64url2", i), raw64([]byte(raw64([]byte(field+":"+opaque)))))
		add("derived", fmt.Sprintf("field-%02d-path", i), "tools/"+field+":"+opaque)
		add("derived", fmt.Sprintf("field-%02d-url", i), "https://service.invalid/"+field+":"+opaque)
	}
	for name, plain := range map[string]string{
		"basic": "Authorization: Basic " + basic, "provider": provider,
		"cookie": "Cookie: SID=" + opaque, "apikey": "X-API-Key: " + opaque,
	} {
		add("derived", "b64url-"+name, raw64([]byte(plain)))
		add("derived", "b64std-"+name, rawStd64([]byte(plain)))
		add("derived", "b64pad-"+name, base64.URLEncoding.EncodeToString([]byte(plain)))
		add("derived", "url-path-b64url-"+name, "https://service.invalid/"+raw64([]byte(plain)))
	}
	add("derived", "jwt", jwtHead+"."+raw64([]byte(`{"sub":"fixture"}`))+"."+opaque)
	add("derived", "jwt-spaced-header", raw64([]byte(` {"alg":"HS256","typ":"JWT"}`))+"."+raw64([]byte(`{"sub":"fixture"}`))+"."+opaque)
	add("derived", "provider", provider)

	for i, v := range []string{"tc_01M4ZF0A9KQ2V7W3X5Y8Z1B4C6", "fagi.web_search", "run_R2", "researcher", "vs_01M4ZEB9E0J3AADR85Z4T4428X", "2026-07-28", "fagi.web", "deep-research", "agent:planner/v2", "tool_call_01", "tools/call", "-32603", "agentcc-gateway", "alpha.beta:read", "42", "CUST-424242", "key_7f3a", "byok", "managed"} {
		add("control", fmt.Sprintf("control-%02d", i), v)
	}

	// A seeded fuzz set of shape-fitting values from identifier, credential
	// and encoding pieces, some encoded whole once or twice.
	pool := append([]string{
		"agent", "planner", "plannner", "run", "run_", "tool", "v2", "alpha", "beta", "read", "web_search",
		"fagi", "CUST-", "4242", "42", ":", ":", "/", ".", "-", "_", "", "token", "Key", "auth",
		"Secret", "session", "jwt", "bearer", "sk-", "AIza", "AKIA", "eyJ", jwtHead, opaque, opaque[:8],
		opaque[:21], provider, raw64([]byte("Cookie: SID=" + opaque)), raw64([]byte("{")), "%3A", "=",
	}, fields...)
	rng := rand.New(rand.NewPCG(16, 2610))
	fuzz := 0
	for n := 0; fuzz < 6000 && n < 400000; n++ {
		var b strings.Builder
		for k := 1 + rng.IntN(5); k > 0; k-- {
			b.WriteString(pool[rng.IntN(len(pool))])
		}
		value := b.String()
		switch rng.IntN(6) {
		case 0:
			value = raw64([]byte(value))
		case 1:
			value = rawStd64([]byte(value))
		case 2:
			value = raw64([]byte(raw64([]byte(value))))
		}
		if value == "" || len(value) > 256 || strings.ContainsAny(value, " %=+\n\"{[") {
			continue
		}
		add("fuzz", fmt.Sprintf("fuzz-%05d", fuzz), value)
		fuzz++
	}

	raw, err := json.Marshal(map[string]any{"redactors": siemCorpusPrivacy, "fixtures": fixtures})
	if err != nil {
		t.Fatal("corpus encoding failed")
	}
	if err := os.WriteFile(out, raw, 0o600); err != nil {
		t.Fatal("corpus write failed")
	}
	t.Logf("fixtures=%d fuzz=%d redactors=%d", len(fixtures), fuzz, len(redactors))
}
