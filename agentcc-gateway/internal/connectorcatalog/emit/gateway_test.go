package emit

import (
	"bytes"
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// walk visits every object in v.
func walk(v any, visit func(canonjson.Object)) {
	switch x := v.(type) {
	case canonjson.Object:
		visit(x)
		for _, m := range x {
			walk(m.Value, visit)
		}
	case []any:
		for _, e := range x {
			walk(e, visit)
		}
	}
}

func TestGatewayFragmentHasNoSecrets(t *testing.T) {
	cat, meta := fixture(t)
	data := emitTarget(t, TargetGateway, cat, meta)["gateway-servers.json"]
	doc := parse(t, data)
	servers := list(t, doc, "servers")
	if len(servers) != 8 {
		t.Fatalf("%d servers; the fixture has 8 http providers", len(servers))
	}
	sawParts := false
	for _, entry := range servers {
		ref := member(entry, "catalogRef").(string)
		// The server member is exactly an org server entry: the gateway's
		// own type decodes it with no unknown member, and it carries no
		// credential, legacy auth or secret.
		raw, err := canonjson.Compact(member(entry, "server"))
		if err != nil {
			t.Fatal(err)
		}
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.DisallowUnknownFields()
		var srv tenant.MCPServerOrgConfig
		if err := dec.Decode(&srv); err != nil {
			t.Fatalf("%s: the server member is not an MCPServerOrgConfig: %v", ref, err)
		}
		if srv.Credential != nil || srv.Auth != nil || srv.OAuth != nil {
			t.Errorf("%s: the server member carries a credential", ref)
		}
		if srv.CatalogRef != ref {
			t.Errorf("%s: catalog_ref %q", ref, srv.CatalogRef)
		}
		for _, mode := range member(entry, "credentialModes").([]any) {
			if !slices.Contains([]any{"none", "shared", "per_user", "vendor_end_user"}, mode) {
				t.Errorf("%s: credential mode %v", ref, mode)
			}
		}
		if parts, ok := member(entry, "credentialParts").([]any); ok {
			sawParts = true
			for _, p := range parts {
				if got := keys(p.(canonjson.Object)); !slices.Equal(got, []string{"in", "name", "secret", "from"}) {
					t.Errorf("%s: credential part members %v", ref, got)
				}
			}
		}
	}
	if !sawParts {
		t.Fatal("the fixture's API-key providers emit no credential parts")
	}
	walk(doc, func(o canonjson.Object) {
		for _, banned := range []string{"value", "secret_value", "token", "credential", "auth_token", "client_secret", "client_id"} {
			if _, ok := o.Get(banned); ok {
				t.Errorf("an object carries %q: %v", banned, keys(o))
			}
		}
	})

	// The emitter's guard, with the loader bypassed: a secret part with a
	// value is refused, and a constant part's value is never written.
	keyed := vendorApps(1)
	p := keyed.Apps[0].Providers[0]
	p.Auth = manifest.Auth{Kind: manifest.AuthAPIKey, CredentialParts: []manifest.CredentialPart{
		{In: "header", Name: "X-Api-Key", Secret: true},
		{In: "header", Name: "X-Scope", Value: "constant-scope-7f3a"},
	}}
	files, err := Target(TargetGateway, keyed, testMeta)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(files["gateway-servers.json"]), "constant-scope-7f3a") {
		t.Error("a constant part's value was written; the gateway reads it from the catalog")
	}
	parts := member(list(t, parse(t, files["gateway-servers.json"]), "servers")[0], "credentialParts").([]any)
	if member(parts[1].(canonjson.Object), "from") != "catalog" || member(parts[0].(canonjson.Object), "from") != "customer" {
		t.Errorf("parts %v", parts)
	}
	p.Auth.CredentialParts[0].Value = "sk-not-in-data"
	_, err = Target(TargetGateway, keyed, testMeta)
	wantError(t, err, "is secret and carries a value")
}

func TestGatewayFragmentMapsTheManifest(t *testing.T) {
	cat := vendorApps(1)
	p := cat.Apps[0].Providers[0]
	p.AccountModes = []string{"user"}
	p.Transport.Variant, p.Transport.Era, p.Trusted = "sse", "2024-11-05", true
	p.XGateway = &manifest.ProviderXGateway{TimeoutMs: 45000, ForwardHeaders: []string{"X-Trace"}}
	entry := list(t, parse(t, emitTarget(t, TargetGateway, cat, testMeta)["gateway-servers.json"]), "servers")[0]
	srv := member(entry, "server").(canonjson.Object)
	want := `{"url": "https://mcp.app00.example.com/mcp", "transport": "sse", "era": "2024-11-05", "trusted_annotations": true, ` +
		`"timeout_ms": 45000, "forward_headers": ["X-Trace"], "catalog_ref": "app00/mcp@1.0.0"}`
	if got, _ := canonjson.Compact(srv); string(got) != want {
		t.Errorf("server\n got %s\nwant %s", got, want)
	}
	if got, _ := canonjson.Compact(member(entry, "credentialModes")); string(got) != `["per_user"]` {
		t.Errorf("credentialModes %s", got)
	}
	p.Kind = manifest.KindPartner
	entry = list(t, parse(t, emitTarget(t, TargetGateway, cat, testMeta)["gateway-servers.json"]), "servers")[0]
	if got, _ := canonjson.Compact(member(entry, "credentialModes")); string(got) != `["vendor_end_user"]` {
		t.Errorf("a partner provider's credentialModes %s", got)
	}
}
