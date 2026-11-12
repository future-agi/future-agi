package catalogparts_test

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate/catalogparts"
)

func load(t *testing.T, dir string) credtemplate.Catalog {
	t.Helper()
	cat, err := manifest.LoadDir(os.DirFS(dir))
	if err != nil {
		t.Fatalf("LoadDir(%s): %v", dir, err)
	}
	return catalogparts.FromManifest(cat)
}

// TestFromManifestReadsDeclaredParts: a catalog the loader accepts gives
// each provider's version and auth.credentialParts, field for field, and a
// template built from it sends the constant part from the catalog
// (Close's constant Close-Scope).
func TestFromManifestReadsDeclaredParts(t *testing.T) {
	cat := load(t, "testdata")
	version, parts, ok := cat.CredentialParts("close", "close-mcp")
	want := []credtemplate.Declared{
		{In: credtemplate.InHeader, Name: "Close-API-Key", Secret: true},
		{In: credtemplate.InHeader, Name: "Close-Scope", Value: "fixture-scope"},
	}
	if !ok || version != "1.3.0" || len(parts) != len(want) || parts[0] != want[0] || parts[1] != want[1] {
		t.Fatalf("CredentialParts = %q %+v %v, want 1.3.0 %+v", version, parts, ok, want)
	}
	for _, missing := range [][2]string{{"close", "other"}, {"other", "close-mcp"}} {
		if _, _, ok := cat.CredentialParts(missing[0], missing[1]); ok {
			t.Errorf("CredentialParts(%s, %s) found a provider", missing[0], missing[1])
		}
	}

	var got http.Header
	srv := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) { got = r.Header.Clone() }))
	defer srv.Close()
	tpl, err := credtemplate.ForCatalogSecret(srv.URL+"/mcp",
		credtemplate.Secret{Type: "api_key", In: "header", Name: "Close-API-Key", Value: "close-test-value"}, cat, "close/close-mcp@1.0.0")
	if err != nil {
		t.Fatal(err)
	}
	resp, err := (&http.Client{Transport: tpl.RoundTripper(nil)}).Get(srv.URL + "/mcp")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if got.Get("Close-Api-Key") != "close-test-value" || got.Get("Close-Scope") != "fixture-scope" {
		t.Fatalf("the request carried the key: %t, the scope: %q", got.Get("Close-Api-Key") != "", got.Get("Close-Scope"))
	}
}

// TestFromManifestOnCN1Fixtures: the valid fixture catalog. Its Stripe
// provider declares a bearer key in Authorization, which a bearer push
// matches; its Close provider declares Close-Scope as non-secret with no
// value, which nothing can supply, so it is refused, never sent in part.
func TestFromManifestOnCN1Fixtures(t *testing.T) {
	cat := load(t, "../../../connectorcatalog/manifest/testdata/valid")
	if _, err := credtemplate.ForCatalogSecret("https://h.example.com/mcp",
		credtemplate.Secret{Type: "bearer", Value: "st-test-value"}, cat, "stripe/stripe-mcp@1.0.0"); err != nil {
		t.Fatalf("stripe: %v", err)
	}
	_, err := credtemplate.ForCatalogSecret("https://h.example.com/mcp",
		credtemplate.Secret{Type: "api_key", In: "header", Name: "Close-API-Key", Value: "close-test-value"}, cat, "close/close-mcp@1.0.0")
	var ce *credtemplate.Error
	if !errors.As(err, &ce) || ce.Field != "catalog_ref" {
		t.Fatalf("close: err %v, want a refusal on catalog_ref", err)
	}
}

// TestFromManifestNil: no catalog is a nil Catalog, so every catalog_ref
// is refused.
func TestFromManifestNil(t *testing.T) {
	if catalogparts.FromManifest(nil) != nil {
		t.Fatal("FromManifest(nil) is not nil")
	}
}
