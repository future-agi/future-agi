package connectors

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
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
