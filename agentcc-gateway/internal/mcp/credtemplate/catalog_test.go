package credtemplate_test

import (
	"errors"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// fakeCatalog is a credtemplate.Catalog of providers keyed app/provider.
type fakeCatalog map[string]fakeProvider

type fakeProvider struct {
	version string
	parts   []credtemplate.Declared
}

func (c fakeCatalog) CredentialParts(app, provider string) (string, []credtemplate.Declared, bool) {
	p, ok := c[app+"/"+provider]
	return p.version, p.parts, ok
}

var testCatalog = fakeCatalog{
	// Close: a secret key header and a constant scope header.
	"close/close-mcp": {"1.2.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Close-API-Key", Secret: true},
		{In: credtemplate.InHeader, Name: "Close-Scope", Value: "test-scope"},
	}},
	// Datadog: two secret headers.
	"datadog/datadog-mcp": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "DD-API-KEY", Secret: true},
		{In: credtemplate.InHeader, Name: "DD-APPLICATION-KEY", Secret: true},
	}},
	// Stripe: a bearer key in Authorization.
	"stripe/stripe-mcp": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Authorization", Prefix: "Bearer ", Secret: true},
	}},
	// Browserbase: the key in a query parameter, and a constant one.
	"browserbase/browserbase-mcp": {"2.0.0", []credtemplate.Declared{
		{In: credtemplate.InQuery, Name: "browserbaseApiKey", Secret: true},
		{In: credtemplate.InQuery, Name: "client", Value: "futureagi"},
	}},
	// A provider that declares no parts (an OAuth provider).
	"linear/linear-mcp": {"1.0.0", nil},
	// A non-secret part with no value (the illustrative Close fixture).
	"close/close-novalue": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Close-API-Key", Secret: true},
		{In: credtemplate.InHeader, Name: "Close-Scope"},
	}},
	// A constant the gateway could not place.
	"bad/bad-constant": {"1.0.0", []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "X-Key", Secret: true},
		{In: credtemplate.InHeader, Name: "Content-Type", Value: "text/plain"},
	}},
}

// TestCatalogConstantPartsApplied: for a server added from the catalog
// (catalog_ref), the template carries the pushed secret parts and the
// constant parts the provider declares, each where it is declared, and
// sends them on every request (Close's Close-Scope).
func TestCatalogConstantPartsApplied(t *testing.T) {
	t.Run("Close: a secret header and a constant header", func(t *testing.T) {
		srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
		tpl, err := credtemplate.ForCatalogSecret(srv.URL+"/mcp",
			credtemplate.Secret{Type: "api_key", In: "header", Name: "Close-API-Key", Value: "close-test-value"},
			testCatalog, "close/close-mcp@1.0.0")
		if err != nil {
			t.Fatal(err)
		}
		get(t, tpl.RoundTripper(http.DefaultTransport), srv.URL+"/mcp")
		r := rec.all()[0]
		if r.header.Get("Close-Api-Key") != "close-test-value" || r.header.Get("Close-Scope") != "test-scope" {
			t.Fatalf("the request carried key %t and scope %q", r.header.Get("Close-Api-Key") != "", r.header.Get("Close-Scope"))
		}
		if got := strings.Join(tpl.HeaderNames(), ","); got != "Close-Api-Key,Close-Scope" {
			t.Fatalf("HeaderNames() = %s", got)
		}
		if tpl.Scrub("test-scope close-test-value") != "test-scope [redacted]" {
			t.Fatal("Scrub must remove the secret and leave the constant")
		}
	})

	t.Run("Browserbase: a secret and a constant query parameter", func(t *testing.T) {
		srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
		tpl, err := credtemplate.ForCatalogSecret(srv.URL+"/mcp?x=1",
			credtemplate.Secret{Type: "api_key", In: "query", Name: "browserbaseApiKey", Value: "bb-test-value"},
			testCatalog, "browserbase/browserbase-mcp@2.3.1")
		if err != nil {
			t.Fatal(err)
		}
		get(t, tpl.RoundTripper(http.DefaultTransport), srv.URL+"/mcp?x=1")
		if q := rec.all()[0].rawQuery; q != "x=1&browserbaseApiKey=bb-test-value&client=futureagi" {
			t.Fatalf("query %q", q)
		}
	})

	t.Run("Datadog, Stripe and a provider with no parts", func(t *testing.T) {
		for _, c := range []struct {
			ref string
			sec credtemplate.Secret
		}{
			{"datadog/datadog-mcp@1.0.0", credtemplate.Secret{Type: "api_key", In: "header", Name: "DD-API-KEY", Value: "dd-1",
				Parts: []credtemplate.SecretPart{{In: "header", Name: "DD-APPLICATION-KEY", Value: "dd-2"}}}},
			{"stripe/stripe-mcp@1.0.0", credtemplate.Secret{Type: "bearer", Header: "Authorization", Value: "st-1"}},
			{"stripe/stripe-mcp@1.0.0", credtemplate.Secret{Type: "api_key", In: "header", Name: "Authorization", Value: "st-1"}},
			{"linear/linear-mcp@1.0.0", credtemplate.Secret{Type: "bearer", Value: "ln-1"}},
		} {
			if _, err := credtemplate.ForCatalogSecret("https://h.example.com/mcp", c.sec, testCatalog, c.ref); err != nil {
				t.Errorf("%s: %v", c.ref, err)
			}
		}
	})

	t.Run("no catalog_ref is ForSecret", func(t *testing.T) {
		if _, err := credtemplate.ForCatalogSecret("https://h.example.com/mcp", credtemplate.Secret{Type: "bearer", Value: "v-1"}, nil, ""); err != nil {
			t.Fatal(err)
		}
	})
}

// TestCatalogRefusesWhatItCannotApply: a server added from the catalog is
// refused, naming the field, whenever its credential would go out partial
// or other than the provider declares; nothing is sent.
func TestCatalogRefusesWhatItCannotApply(t *testing.T) {
	closeKey := credtemplate.Secret{Type: "api_key", In: "header", Name: "Close-API-Key", Value: "close-test-value"}
	for _, tc := range []struct {
		name  string
		cat   credtemplate.Catalog
		ref   string
		sec   credtemplate.Secret
		field string
	}{
		{"no catalog loaded", nil, "close/close-mcp@1.2.0", closeKey, "catalog_ref"},
		{"a catalog_ref that does not parse", testCatalog, "close/close-mcp@1.2", closeKey, "catalog_ref"},
		{"a catalog_ref with an upper-case app", testCatalog, "Close/close-mcp@1.2.0", closeKey, "catalog_ref"},
		{"a provider the catalog does not have", testCatalog, "close/close-other@1.0.0", closeKey, "catalog_ref"},
		{"another major version", testCatalog, "close/close-mcp@2.0.0", closeKey, "catalog_ref"},
		{"a declared secret part missing (Datadog with one key)", testCatalog, "datadog/datadog-mcp@1.0.0",
			credtemplate.Secret{Type: "api_key", In: "header", Name: "DD-API-KEY", Value: "dd-1"}, "parts"},
		{"a pushed part the provider does not declare", testCatalog, "close/close-mcp@1.2.0",
			credtemplate.Secret{Type: "api_key", In: "header", Name: "Close-API-Key", Value: "close-test-value",
				Parts: []credtemplate.SecretPart{{In: "header", Name: "X-Extra", Value: "x-1"}}}, "parts[0].name"},
		{"the secret in another place than declared", testCatalog, "close/close-mcp@1.2.0",
			credtemplate.Secret{Type: "api_key", In: "query", Name: "Close-API-Key", Value: "close-test-value"}, "parts"},
		{"a constant part pushed", testCatalog, "close/close-mcp@1.2.0",
			credtemplate.Secret{Type: "api_key", In: "header", Name: "Close-API-Key", Value: "close-test-value",
				Parts: []credtemplate.SecretPart{{In: "header", Name: "Close-Scope", Value: "s-1"}}}, "parts[0].name: is a constant part"},
		{"a prefix other than declared (Bearer where a bare value is declared)", testCatalog, "close/close-mcp@1.2.0",
			credtemplate.Secret{Type: "bearer", Value: "close-test-value"}, "parts"},
		{"a declared non-secret part with no value", testCatalog, "close/close-novalue@1.0.0", closeKey, "catalog_ref"},
		{"a constant the gateway cannot place", testCatalog, "bad/bad-constant@1.0.0",
			credtemplate.Secret{Type: "api_key", In: "header", Name: "X-Key", Value: "k-1"}, "catalog_ref"},
		{"a pushed secret the gateway cannot apply at all", testCatalog, "close/close-mcp@1.2.0",
			credtemplate.Secret{Type: "api_key", In: "cookie", Name: "c", Value: "c-1"}, "in"},
	} {
		_, err := credtemplate.ForCatalogSecret("https://h.example.com/mcp", tc.sec, tc.cat, tc.ref)
		// field is the error's field, then optionally ": " and the start of
		// its reason.
		field, reason, _ := strings.Cut(tc.field, ": ")
		var ce *credtemplate.Error
		if !errors.As(err, &ce) || ce.Field != field || !strings.HasPrefix(ce.Reason, reason) {
			t.Errorf("%s: err %v, want a *credtemplate.Error on field %q", tc.name, err, tc.field)
			continue
		}
		if l := leaks(err.Error(), tc.sec.Value); len(l) > 0 {
			t.Errorf("%s: the error carries the value", tc.name)
		}
	}

	t.Run("the prefix the push gives must be the declared one", func(t *testing.T) {
		// Stripe declares "Bearer " in Authorization: a bearer push matches.
		// A provider declaring a bare Authorization value would be sent
		// "Bearer <key>" by the push's rule, so it is refused.
		bare := fakeCatalog{"vend/vend-mcp": {"1.0.0", []credtemplate.Declared{{In: credtemplate.InHeader, Name: "Authorization", Secret: true}}}}
		_, err := credtemplate.ForCatalogSecret("https://h.example.com/mcp", credtemplate.Secret{Type: "bearer", Value: "v-1"}, bare, "vend/vend-mcp@1.0.0")
		var ce *credtemplate.Error
		if !errors.As(err, &ce) || ce.Field != "header" || !strings.Contains(ce.Reason, `"Bearer "`) {
			t.Fatalf("err %v, want a refusal on header naming the Bearer prefix", err)
		}
	})
}

// TestParseRef: catalog_ref is <app-id>/<provider-id>@<semver>, the
// contract's pattern.
func TestParseRef(t *testing.T) {
	ref, err := credtemplate.ParseRef("google-calendar/gcal-mcp@10.0.3")
	if err != nil || ref != (credtemplate.Ref{App: "google-calendar", Provider: "gcal-mcp", Version: "10.0.3"}) {
		t.Fatalf("ParseRef = %+v, %v", ref, err)
	}
	for _, bad := range []string{"", "close", "close/close-mcp", "close/close-mcp@01.0.0", "close/close-mcp@1.0.0-rc1",
		"c/close-mcp@1.0.0", "close/Close@1.0.0", "close/close-mcp@1.0.0 ", "close//close-mcp@1.0.0"} {
		if _, err := credtemplate.ParseRef(bad); err == nil {
			t.Errorf("ParseRef(%q) accepted it", bad)
		}
	}
}
