package credtemplate_test

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"testing"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// prefixCatalog declares a key in Authorization after a prefix other than
// "Bearer " (PagerDuty's "Token token="), after "Bearer ", after none, and
// after one the gateway cannot place.
var prefixCatalog = fakeCatalog{
	"pager/api-key": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Authorization", Prefix: "Token token=", Secret: true},
	}},
	"stripe/stripe-mcp": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Authorization", Prefix: "Bearer ", Secret: true},
	}},
	"bare/api-key": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Authorization", Secret: true},
	}},
	"evil/api-key": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Authorization", Prefix: "Token\r\nX-Injected: 1 ", Secret: true},
	}},
}

// TestCatalogKeySentAfterTheDeclaredPrefix: a server added from a catalog
// provider that declares "Token token=" before its key in Authorization
// pushes the key alone, typed api_key, as the vault stores it. The gateway
// places the record's prefix before it, verbatim, on every request to the
// server's origin: "Authorization: Token token=<key>", never "Bearer <key>",
// in one header and never in the query; a request to another origin
// carries nothing.
func TestCatalogKeySentAfterTheDeclaredPrefix(t *testing.T) {
	const key = "pd-test-value-3a9e"
	srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	other, otherRec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	for name, sec := range map[string]credtemplate.Secret{
		"pushed as the vault stores it":     {Type: "api_key", Header: "Authorization", Value: key},
		"in header, by name":                {Type: "api_key", In: "header", Name: "Authorization", Value: key},
		"the header named by the reference": {Type: "api_key", ReferenceHeader: "Authorization", Value: key},
	} {
		t.Run(name, func(t *testing.T) {
			before := len(rec.all())
			tpl, err := credtemplate.ForCatalogSecret(srv.URL+"/mcp", sec, prefixCatalog, "pager/api-key@1.0.0")
			if err != nil {
				t.Fatalf("ForCatalogSecret: %v", err)
			}
			rt := tpl.RoundTripper(http.DefaultTransport)
			get(t, rt, srv.URL+"/mcp")
			get(t, rt, srv.URL+"/mcp?cursor=2")
			get(t, rt, other.URL+"/elsewhere")
			reqs := rec.all()[before:]
			if len(reqs) != 2 {
				t.Fatalf("%d requests reached the server, want 2", len(reqs))
			}
			for i, r := range reqs {
				got := r.header.Values("Authorization")
				if len(got) != 1 || got[0] != "Token token="+key {
					t.Errorf("request %d: Authorization has %d value(s), Token token=<key> %t, Bearer %t",
						i, len(got), len(got) == 1 && got[0] == "Token token="+key, strings.Contains(fmt.Sprint(got), "Bearer"))
				}
				if carrying := headerCarrying(r.header, key); len(carrying) != 1 {
					t.Errorf("request %d: %d headers carry the key, want only Authorization", i, len(carrying))
				}
				if len(leaks(r.rawQuery, key)) > 0 {
					t.Errorf("request %d: the query carries the key", i)
				}
			}
			for _, r := range otherRec.all() {
				if r.header.Get("Authorization") != "" || len(headerCarrying(r.header, key)) > 0 {
					t.Error("a request to another origin carried the credential")
				}
			}
			if got := tpl.Scrub("Authorization: Token token=" + key); got != "Authorization: Token token=[redacted]" {
				t.Errorf("Scrub kept the key %t", len(leaks(got, key)) > 0)
			}
		})
	}
}

// TestCatalogPrefixIsOnlyTheRecords: the prefix placed is the catalog
// record's and nothing else's. A bearer push for that provider is refused
// (a bearer goes only as "Bearer "), naming both prefixes and never the key;
// a provider that declares "Bearer ", or no prefix, and a server with no
// catalog_ref behave as before; a pushed secret has no prefix field, so a
// config naming one is refused by the admin contract and, read leniently,
// ignored; and a declared prefix the gateway cannot place in a header (a
// control character) is refused before anything is sent.
func TestCatalogPrefixIsOnlyTheRecords(t *testing.T) {
	const key = "pd-test-value-77c0"
	srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	sent := func(t *testing.T, tpl *credtemplate.Template) []string {
		t.Helper()
		before := len(rec.all())
		get(t, tpl.RoundTripper(http.DefaultTransport), srv.URL+"/mcp")
		reqs := rec.all()[before:]
		if len(reqs) != 1 {
			t.Fatalf("%d requests reached the server, want 1", len(reqs))
		}
		return reqs[0].header.Values("Authorization")
	}

	for _, tc := range []struct {
		name, ref string
		sec       credtemplate.Secret
		field     string
	}{
		{"a bearer token for a Token token= provider", "pager/api-key@1.0.0", credtemplate.Secret{Type: "bearer", Header: "Authorization", Value: key}, "header"},
		{"a bearer token in header, by name", "pager/api-key@1.0.0", credtemplate.Secret{Type: "bearer", In: "header", Name: "Authorization", Value: key}, "name"},
		{"an api_key for a provider that declares no prefix", "bare/api-key@1.0.0", credtemplate.Secret{Type: "api_key", Header: "Authorization", Value: key}, "header"},
	} {
		_, err := credtemplate.ForCatalogSecret(srv.URL+"/mcp", tc.sec, prefixCatalog, tc.ref)
		var ce *credtemplate.Error
		if !errors.As(err, &ce) || ce.Field != tc.field || !strings.Contains(ce.Reason, `"Bearer "`) {
			t.Errorf("%s: err %v, want a refusal on %s naming the Bearer prefix", tc.name, err, tc.field)
			continue
		}
		if len(leaks(err.Error(), key)) > 0 {
			t.Errorf("%s: the refusal carries the key", tc.name)
		}
	}

	_, err := credtemplate.ForCatalogSecret(srv.URL+"/mcp", credtemplate.Secret{Type: "api_key", Header: "Authorization", Value: key},
		prefixCatalog, "evil/api-key@1.0.0")
	var ce *credtemplate.Error
	if !errors.As(err, &ce) || ce.Field != "catalog_ref" || !strings.Contains(ce.Reason, "prefix") || len(leaks(err.Error(), key)) > 0 {
		t.Errorf("a declared prefix with CR/LF: err %v, want a refusal on catalog_ref naming the prefix, without the key", err)
	}

	for name, tc := range map[string]struct {
		ref string
		sec credtemplate.Secret
	}{
		"an api_key for a provider that declares Bearer": {"stripe/stripe-mcp@1.0.0", credtemplate.Secret{Type: "api_key", Header: "Authorization", Value: key}},
		"a bearer for a provider that declares Bearer":   {"stripe/stripe-mcp@1.0.0", credtemplate.Secret{Type: "bearer", Value: key}},
		"an api_key in Authorization with no catalog":    {"", credtemplate.Secret{Type: "api_key", Header: "Authorization", Value: key}},
	} {
		tpl, err := credtemplate.ForCatalogSecret(srv.URL+"/mcp", tc.sec, prefixCatalog, tc.ref)
		if err != nil {
			t.Errorf("%s: %v", name, err)
			continue
		}
		if got := sent(t, tpl); len(got) != 1 || got[0] != "Bearer "+key {
			t.Errorf("%s: Authorization is not Bearer <key> (%d value(s))", name, len(got))
		}
	}

	// A pushed config that names a prefix: the admin PUT decodes the
	// contract strictly, which has no such field, so it is refused there;
	// read leniently, as the pool reads its store, the field is dropped and
	// the record's prefix is placed.
	pushed := `{"type":"api_key","header":"Authorization","value":"` + key + `","prefix":"Basic "}`
	var contract gatewayadmin.MCPCredentialSecret
	dec := json.NewDecoder(strings.NewReader(pushed))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&contract); err == nil {
		t.Error("the admin contract accepted a pushed secret that names a prefix")
	}
	var stored tenant.MCPCredentialSecret
	if err := json.Unmarshal([]byte(pushed), &stored); err != nil {
		t.Fatal(err)
	}
	tpl, err := credtemplate.ForCatalogSecret(srv.URL+"/mcp", tenant.TemplateSecret(&stored, ""), prefixCatalog, "pager/api-key@1.0.0")
	if err != nil {
		t.Fatalf("the stored secret: %v", err)
	}
	if got := sent(t, tpl); len(got) != 1 || got[0] != "Token token="+key {
		t.Errorf("a pushed prefix was honoured: Authorization Basic %t, Token token= %t",
			strings.HasPrefix(fmt.Sprint(got), "[Basic"), len(got) == 1 && got[0] == "Token token="+key)
	}
}
