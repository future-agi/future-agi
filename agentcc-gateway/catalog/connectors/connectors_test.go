package connectors

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate/catalogparts"
)

// The embedded bundle is dist/catalog.json byte for byte, and it loads: the
// loader accepts every record. Close's API-key provider carries its secret
// Close-API-Key part and the constant Close-Scope part the gateway adds from
// the catalog, which is what a catalog_ref to it is placed by.
func TestEmbeddedCatalogLoads(t *testing.T) {
	disk, err := os.ReadFile(filepath.Join("dist", "catalog.json"))
	if err != nil {
		t.Fatal(err)
	}
	if string(disk) != string(BundleJSON) {
		t.Fatal("the embedded bundle differs from dist/catalog.json")
	}
	cat, err := Embedded()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(cat.Source, "embedded bundle ") {
		t.Errorf("source %q", cat.Source)
	}
	if again, _ := Embedded(); again != cat {
		t.Error("the embedded catalog is decoded more than once")
	}
	app := cat.Records.App("close")
	if app == nil || app.Provider("api-key") == nil {
		t.Fatal("the embedded catalog has no close/api-key")
	}
	parts := app.Provider("api-key").Auth.CredentialParts
	var secret, constant bool
	for _, p := range parts {
		switch {
		case p.In == "header" && p.Name == "Close-API-Key" && p.Secret:
			secret = true
		case p.In == "header" && p.Name == "Close-Scope" && !p.Secret && p.Value == "mcp.read":
			constant = true
		}
	}
	if !secret || !constant {
		t.Fatalf("close/api-key parts %+v: want a secret Close-API-Key and the constant Close-Scope mcp.read", parts)
	}
	if loaded, err := Load(""); err != nil || loaded != cat {
		t.Fatalf("Load(\"\") = %v, %v; want the embedded catalog", loaded, err)
	}
}

// PagerDuty's key goes in Authorization after "Token token=", its record's
// prefix. The push carries the key alone, typed api_key, and the gateway
// places the record's prefix before it: "Authorization: Token token=<key>",
// never "Bearer <key>". A bearer push is refused, naming the secret's field
// and both prefixes, never the key: a bearer goes only as "Bearer ".
func TestEmbeddedPagerDutyKeyIsSentAfterItsPrefix(t *testing.T) {
	cat, err := Embedded()
	if err != nil {
		t.Fatal(err)
	}
	parts := catalogparts.FromManifest(cat.Records)
	version, declared, ok := parts.CredentialParts("pagerduty", "api-key")
	want := credtemplate.Declared{In: credtemplate.InHeader, Name: "Authorization", Secret: true, Prefix: "Token token="}
	if !ok || len(declared) != 1 || declared[0] != want {
		t.Fatalf("pagerduty/api-key in the embedded catalog: found %t, parts %+v; want one secret Authorization part with the prefix Token token=", ok, declared)
	}
	const key = "pd-test-value-7c41"
	var mu sync.Mutex
	var got []string
	srv := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		mu.Lock()
		got = r.Header.Values("Authorization")
		mu.Unlock()
	}))
	defer srv.Close()
	for _, sec := range []credtemplate.Secret{
		{Type: "api_key", Header: "Authorization", Value: key},
		{Type: "api_key", In: "header", Name: "Authorization", Value: key},
	} {
		tpl, err := credtemplate.ForCatalogSecret(srv.URL+"/mcp", sec, parts, "pagerduty/api-key@"+version)
		if err != nil {
			t.Errorf("api_key secret, in %q: %v", sec.In, strings.ReplaceAll(err.Error(), key, "<key>"))
			continue
		}
		mu.Lock()
		got = nil
		mu.Unlock()
		resp, err := (&http.Client{Transport: tpl.RoundTripper(nil)}).Get(srv.URL + "/mcp")
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		mu.Lock()
		got := got
		mu.Unlock()
		if len(got) != 1 || got[0] != "Token token="+key {
			t.Errorf("api_key secret, in %q: Authorization has %d value(s), Token token=<key> %t", sec.In, len(got), len(got) == 1 && got[0] == "Token token="+key)
		}
	}
	for _, tc := range []struct {
		sec   credtemplate.Secret
		field string
	}{
		{credtemplate.Secret{Type: "bearer", Header: "Authorization", Value: key}, "header"},
		{credtemplate.Secret{Type: "bearer", In: "header", Name: "Authorization", Value: key}, "name"},
	} {
		tpl, err := credtemplate.ForCatalogSecret("https://mcp.pagerduty.com/mcp", tc.sec, parts, "pagerduty/api-key@"+version)
		var ce *credtemplate.Error
		if tpl != nil || !errors.As(err, &ce) || ce.Field != tc.field ||
			!strings.Contains(ce.Reason, `"Bearer "`) || !strings.Contains(ce.Reason, `"Token token="`) {
			t.Errorf("%s secret, in %q: err %v, want a refusal on %s naming both prefixes", tc.sec.Type, tc.sec.In, err, tc.field)
			continue
		}
		if strings.Contains(err.Error(), key) {
			t.Errorf("%s secret, in %q: the refusal carries the key", tc.sec.Type, tc.sec.In)
		}
	}
}

// An operator's catalog directory replaces the embedded one, checked the same
// way; a directory that is missing, not one, empty or holds an invalid record
// is refused, never read as an empty catalog.
func TestLoadDir(t *testing.T) {
	const valid = "../../internal/connectorcatalog/manifest/testdata/valid"
	cat, err := Load(valid)
	if err != nil {
		t.Fatal(err)
	}
	if cat.Records.App("close") == nil || cat.Source != "connectors.catalog_dir "+valid {
		t.Fatalf("loaded %q without close", cat.Source)
	}

	file := filepath.Join(t.TempDir(), "file")
	if err := os.WriteFile(file, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	empty := t.TempDir()
	bad := t.TempDir()
	if err := os.MkdirAll(filepath.Join(bad, "apps", "x"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(bad, "apps", "x", "app.yaml"), []byte("schema: nope\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	for name, cs := range map[string]struct{ dir, want string }{
		"missing":   {filepath.Join(empty, "nope"), "is not a directory"},
		"a file":    {file, "is not a directory"},
		"empty":     {empty, "holds no app"},
		"a bad app": {bad, "the records are not valid"},
	} {
		if _, err := LoadDir(cs.dir); err == nil || !strings.Contains(err.Error(), cs.want) {
			t.Errorf("%s: err %v, want %q", name, err, cs.want)
		}
	}
}
