package manifest

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// The schema corpus: each fixture record, and fixture records with one
// change the published schema itself refuses. TestSchemaCorpus asserts the
// strict loader's verdict on each; with MANIFEST_CORPUS_DIR set it also
// writes them as <dir>/manifest/{valid,invalid}/<case>.json, so an external
// JSON Schema 2020-12 validator can be run on the same documents (see
// catalog/connectors/README.md).

type corpusCase struct {
	name   string
	source string // fixture file
	valid  bool
	mutate func(doc map[string]any)
}

const (
	cLinearApp = "apps/linear/app.yaml"
	cLinear    = "apps/linear/providers/linear-mcp.yaml"
	cClose     = "apps/close/providers/close-mcp.yaml"
	cGmailREST = "apps/gmail/providers/futureagi-rest.yaml"
	cGitHubLoc = "apps/github/providers/local.yaml"
	cRelay     = "apps/gmail/providers/relay.yaml"
	cPlatform  = "platforms/relay.yaml"
	cGenerated = "apps/gmail/providers/futureagi-rest/tools.generated.json"
)

// docPath walks a dotted path ("auth.oauth2.scopes.0") to its parent and key.
func docPath(doc map[string]any, path string) (parent any, key string) {
	parts := strings.Split(path, ".")
	var cur any = doc
	for _, p := range parts[:len(parts)-1] {
		switch c := cur.(type) {
		case map[string]any:
			cur = c[p]
		case []any:
			i, _ := strconv.Atoi(p)
			cur = c[i]
		}
	}
	return cur, parts[len(parts)-1]
}

func set(path string, v any) func(map[string]any) {
	return func(doc map[string]any) {
		parent, key := docPath(doc, path)
		switch p := parent.(type) {
		case map[string]any:
			p[key] = v
		case []any:
			i, _ := strconv.Atoi(key)
			p[i] = v
		}
	}
}

func del(path string) func(map[string]any) {
	return func(doc map[string]any) {
		parent, key := docPath(doc, path)
		delete(parent.(map[string]any), key)
	}
}

func both(fs ...func(map[string]any)) func(map[string]any) {
	return func(doc map[string]any) {
		for _, f := range fs {
			f(doc)
		}
	}
}

// partnerAuth is a partner auth block bound to the relay platform.
func partnerAuth(toolkit string) map[string]any {
	return map[string]any{"kind": "partner", "partner": map[string]any{
		"platform": "relay", "toolkit": toolkit, "version": "2026-10-01", "binding": "header", "toolMode": "fixed"}}
}

// evidence is one x-omega.publicClient evidence item, changed by change.
func evidence(change func(map[string]any)) map[string]any {
	e := map[string]any{"url": "https://linear.app/developers/oauth-2-0-authentication", "fetchedAt": "2026-10-22",
		"sha256": strings.Repeat("ab", 32), "quote": "Illustrative: no client secret with PKCE."}
	if change != nil {
		change(e)
	}
	return e
}

// publicClient is an x-omega.publicClient block.
func publicClient(supported any, items ...map[string]any) map[string]any {
	list := make([]any, len(items))
	for i, e := range items {
		list[i] = e
	}
	return map[string]any{"supported": supported, "evidence": list}
}

// withKey adds key to m.
func withKey(m map[string]any, key string, v any) map[string]any {
	m[key] = v
	return m
}

// keepSources keeps a record's first n sources.
func keepSources(n int) func(map[string]any) {
	return func(doc map[string]any) { doc["sources"] = doc["sources"].([]any)[:n] }
}

func corpusCases() []corpusCase {
	part := func(in, name string, secret bool, extra map[string]any) map[string]any {
		m := map[string]any{"in": in, "name": name, "secret": secret}
		for k, v := range extra {
			m[k] = v
		}
		return m
	}
	return []corpusCase{
		{"app-unknown-key", cLinearApp, false, set("clientId", "x")},
		{"app-null-optional", cLinearApp, false, set("icon", nil)},
		{"app-name-61", cLinearApp, false, set("name", strings.Repeat("n", 61))},
		{"app-id-uppercase", cLinearApp, false, set("id", "Linear")},
		{"app-category", cLinearApp, false, set("eeCategory", "sales")},
		{"app-x-gateway-key", cLinearApp, false, set("x-gateway", map[string]any{"pricing": "managed"})},
		{"app-x-omega-key", cLinearApp, false, set("x-omega", map[string]any{"clientId": "x"})},
		{"app-sunset-without-date", cLinearApp, false, set("lifecycle", map[string]any{"status": "sunset"})},
		{"app-no-categories", cLinearApp, false, set("categories", []any{})},
		{"app-http-docs", cLinearApp, false, set("docsUrl", "http://linear.app/docs")},
		{"app-fragment", cLinearApp, false, set("docsUrl", "https://linear.app/docs#mcp")},
		{"app-userinfo", cLinearApp, false, set("docsUrl", "https://user@linear.app/docs")},
		{"app-uppercase-host", cLinearApp, false, set("docsUrl", "https://Linear.app/docs")},
		{"app-bad-date", cLinearApp, false, set("sources.0.fetchedAt", "2026-1-9")},
		{"app-archetype", cLinearApp, false, set("archetype", "email")},
		{"app-ee-id-pattern", cLinearApp, false, set("x-omega", map[string]any{"eeId": "Linear_App"})},
		{"app-env-prefix", cLinearApp, false, set("x-omega", map[string]any{"envPrefix": "LINEAR"})},

		{"provider-vendor-mcp-stdio", cLinear, false, set("transport", map[string]any{"kind": "stdio", "command": "npx"})},
		{"provider-api-key-without-parts", cClose, false, del("auth.credentialParts")},
		{"provider-oauth2-with-parts", cLinear, false, set("auth.credentialParts", []any{part("header", "X-Key", true, nil)})},
		{"provider-secret-with-value", cClose, false, set("auth.credentialParts.0.value", "abc")},
		{"provider-query-prefix", cClose, false, set("auth.credentialParts.1", part("query", "scope", false, map[string]any{"prefix": "X"}))},
		{"provider-header-space", cClose, false, set("auth.credentialParts.1.name", "Close Scope")},
		{"provider-http-with-command", cLinear, false, set("transport.command", "npx")},
		{"provider-http-not-hosted", cLinear, false, set("hosted", false)},
		{"provider-trusted-at-listed", cLinear, false, set("trusted", true)},
		{"provider-annotation-missing", cLinear, false, del("tools.items.0.annotations.openWorldHint")},
		{"provider-generic-arguments-alone", cLinear, false, set("tools.items.0.genericArguments", []any{"q"})},
		{"provider-generated-upstream", cLinear, false, set("tools.generated", "tools.generated.json")},
		{"provider-status-without-source", cLinear, false, set("vendorStatus", map[string]any{"label": "ga"})},
		{"provider-redirect-uri", cGmailREST, false, set("auth.oauth2.extraAuthorizeParams.redirect_uri", "https://evil.example/cb")},
		{"provider-approval-on-api-key", cClose, false, set("auth.clientApproval", map[string]any{"required": true, "program": "P", "url": "https://close.com/apply", "source": "https://close.com/docs"})},
		{"provider-rest-without-upstream", cGmailREST, false, del("upstream")},
		{"provider-partner-without-binding", cRelay, false, del("auth.partner")},
		{"provider-verify-both", cLinear, false, set("auth.verify", map[string]any{"tool": "list_issues", "http": map[string]any{"method": "GET", "url": "https://api.linear.app/me"}})},
		{"provider-verify-empty", cLinear, false, set("auth.verify", map[string]any{})},
		{"provider-rate-without-source", cLinear, false, del("rateLimits.0.source")},
		{"provider-tool-name-65", cLinear, false, set("tools.items.0.name", strings.Repeat("t", 65))},
		{"provider-scope-space", cLinear, false, set("auth.oauth2.scopes.0", "read all")},
		{"provider-unanchored-pattern", cRelay, false, set("transport.urlVariables.serverId.pattern", "[A-Za-z0-9-]{8,64}")},
		{"provider-stdio-with-url", cGitHubLoc, false, set("transport.url", "https://example.com/mcp")},
		{"provider-local-api-key", cGitHubLoc, false, set("auth", map[string]any{"kind": "api_key", "credentialParts": []any{part("header", "X-Key", true, nil)}})},
		{"provider-no-account-modes", cLinear, false, set("accountModes", []any{})},
		{"provider-repeated-account-mode", cLinear, false, set("accountModes", []any{"agent", "agent"})},
		{"provider-retry-200", cGmailREST, false, set("retry.on", []any{200})},
		{"provider-null-in-tool", cLinear, false, set("tools.items.0.title", nil)},
		{"provider-trusted-and-reviewed", cLinear, true, both(set("trusted", true), set("quality.tier", "verified"))},
		{"provider-verify-http", cClose, true, both(
			set("auth.verify", map[string]any{"http": map[string]any{"method": "GET", "url": "https://api.close.com/api/v1/me/"}}),
			func(doc map[string]any) {
				doc["sources"] = append(doc["sources"].([]any), map[string]any{"fact": "auth.verify.http.url", "url": "https://developer.close.com/mcp", "fetchedAt": "2026-10-19"})
			})},
		{"provider-url-variable-part", cClose, true, both(
			set("transport.url", "https://mcp.close.com/{token}/mcp"),
			set("auth.credentialParts.1", part("url_variable", "token", true, nil)))},
		{"provider-extra-authorize-param", cGmailREST, true, set("auth.oauth2.extraAuthorizeParams.hd", "example.com")},
		// Only a partner provider authenticates with partner; the OAuth
		// sources go with the oauth2 block, so the kind rule alone refuses.
		{"provider-vendor-mcp-partner-auth", cLinear, false, both(set("auth", partnerAuth("linear")), keepSources(1))},
		{"provider-rest-partner-auth", cGmailREST, false, both(set("auth", partnerAuth("gmail")), keepSources(0))},

		// x-omega.publicClient (CN24pre-b): whether a person can sign in
		// with a public client, each evidence item a saved page.
		{"provider-public-client", cLinear, true, set("x-omega", map[string]any{"publicClient": publicClient(true, evidence(nil))})},
		{"provider-public-client-notes", cLinear, true, set("x-omega", map[string]any{"publicClient": withKey(publicClient(false, evidence(nil)), "notes", "The MCP guide asks for a client secret.")})},
		{"provider-public-client-no-evidence", cLinear, false, set("x-omega", map[string]any{"publicClient": map[string]any{"supported": true}})},
		{"provider-public-client-empty-evidence", cLinear, false, set("x-omega", map[string]any{"publicClient": map[string]any{"supported": true, "evidence": []any{}}})},
		{"provider-public-client-five-items", cLinear, false, set("x-omega", map[string]any{"publicClient": map[string]any{"supported": true, "evidence": []any{evidence(nil), evidence(nil), evidence(nil), evidence(nil), evidence(nil)}}})},
		{"provider-public-client-no-supported", cLinear, false, set("x-omega", map[string]any{"publicClient": map[string]any{"evidence": []any{evidence(nil)}}})},
		{"provider-public-client-string", cLinear, false, set("x-omega", map[string]any{"publicClient": publicClient("yes", evidence(nil))})},
		{"provider-public-client-key", cLinear, false, set("x-omega", map[string]any{"publicClient": withKey(publicClient(true, evidence(nil)), "clientId", "x")})},
		{"provider-public-client-no-sha256", cLinear, false, set("x-omega", map[string]any{"publicClient": publicClient(true, evidence(func(e map[string]any) { delete(e, "sha256") }))})},
		{"provider-public-client-sha256-upper", cLinear, false, set("x-omega", map[string]any{"publicClient": publicClient(true, evidence(func(e map[string]any) { e["sha256"] = strings.Repeat("AB", 32) }))})},
		{"provider-public-client-no-quote", cLinear, false, set("x-omega", map[string]any{"publicClient": publicClient(true, evidence(func(e map[string]any) { delete(e, "quote") }))})},
		{"provider-public-client-quote-301", cLinear, false, set("x-omega", map[string]any{"publicClient": publicClient(true, evidence(func(e map[string]any) { e["quote"] = strings.Repeat("q", 301) }))})},
		{"provider-public-client-http", cLinear, false, set("x-omega", map[string]any{"publicClient": publicClient(true, evidence(func(e map[string]any) { e["url"] = "http://linear.app/developers" }))})},
		{"provider-public-client-bad-date", cLinear, false, set("x-omega", map[string]any{"publicClient": publicClient(true, evidence(func(e map[string]any) { e["fetchedAt"] = "2026-10-22T10:00:00Z" }))})},
		{"provider-public-client-evidence-key", cLinear, false, set("x-omega", map[string]any{"publicClient": publicClient(true, evidence(func(e map[string]any) { e["by"] = "lane" }))})},
		{"provider-public-client-notes-501", cLinear, false, set("x-omega", map[string]any{"publicClient": withKey(publicClient(true, evidence(nil)), "notes", strings.Repeat("n", 501))})},

		{"platform-tool-mode", cPlatform, false, set("toolMode", "auto")},
		{"platform-unknown-key", cPlatform, false, set("secret", "x")},
		{"generated-no-tools", cGenerated, false, set("tools", []any{})},
		{"generated-tool-key", cGenerated, false, set("tools.0.cost", 1)},
		{"generated-null-in-schema", cGenerated, true, set("tools.1.inputSchema.properties.threadId.default", nil)},
	}
}

// fixtureDoc decodes one fixture file into a JSON document.
func fixtureDoc(t *testing.T, path string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", "valid", path))
	if err != nil {
		t.Fatal(err)
	}
	var v any
	if strings.HasSuffix(path, ".json") {
		v, err = decodeJSON(data)
	} else {
		v, err = decodeYAML(data)
	}
	if err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	return v.(map[string]any)
}

// recordValid runs the strict loader's record checks on a JSON document of
// the fixture file's kind.
func recordValid(source string, data []byte) (bool, []error) {
	var errs []error
	switch {
	case strings.HasSuffix(source, "app.yaml"):
		_, errs = parseApp("doc.json", data)
	case strings.HasPrefix(source, "platforms/"):
		_, errs = parsePlatform("doc.json", data)
	case strings.HasSuffix(source, GeneratedToolsFile):
		_, errs = parseGenerated("doc.json", data)
	default:
		_, errs = parseProvider("doc.json", data)
	}
	return len(errs) == 0, errs
}

func TestSchemaCorpus(t *testing.T) {
	out := os.Getenv("MANIFEST_CORPUS_DIR")
	write := func(valid bool, name string, data []byte) {
		if out == "" {
			return
		}
		dir := filepath.Join(out, "manifest", map[bool]string{true: "valid", false: "invalid"}[valid])
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, name+".json"), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// Every fixture record is valid on its own.
	for path := range fixture(t) {
		if strings.HasSuffix(path, "tools.yaml") {
			continue // the generator's overlay, not a record
		}
		data, _ := json.MarshalIndent(fixtureDoc(t, path), "", "  ")
		if ok, errs := recordValid(path, data); !ok {
			t.Errorf("fixture %s: %v", path, errs)
		}
		write(true, "fixture-"+strings.NewReplacer("/", "-", ".yaml", "", ".json", "").Replace(path), data)
	}
	for _, c := range corpusCases() {
		doc := fixtureDoc(t, c.source)
		c.mutate(doc)
		data, err := json.MarshalIndent(doc, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		if ok, errs := recordValid(c.source, data); ok != c.valid {
			t.Errorf("%s: loader says valid=%v, want %v (%v)", c.name, ok, c.valid, errs)
		}
		write(c.valid, c.name, data)
	}
}
