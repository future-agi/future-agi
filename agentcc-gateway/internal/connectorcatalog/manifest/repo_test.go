package manifest

import (
	"io/fs"
	"os"
	"path/filepath"
	"testing"
)

// repoCatalogRoot is agentcc-gateway/catalog/connectors, from this package.
var repoCatalogRoot = filepath.Join("..", "..", "..", "catalog", "connectors")

// validateRepoCatalog runs every check a data lane's commit must pass: the
// strict loader with L0, L1, and the open-data rule.
func validateRepoCatalog(fsys fs.FS) (*Catalog, error) {
	cat, err := LoadDir(fsys)
	if err != nil {
		return nil, err
	}
	if err := CheckL1(cat); err != nil {
		return nil, err
	}
	if err := CheckPublic(cat); err != nil {
		return nil, err
	}
	return cat, nil
}

// TestRepoCatalogValid validates whatever is under catalog/connectors/apps/
// and platforms/. Every data lane runs it.
func TestRepoCatalogValid(t *testing.T) {
	if _, err := os.Stat(filepath.Join(repoCatalogRoot, "schema", "connector-manifest.v1.json")); err != nil {
		t.Fatalf("catalog root %s not found: %v", repoCatalogRoot, err)
	}
	cat, err := validateRepoCatalog(os.DirFS(repoCatalogRoot))
	if err != nil {
		t.Fatalf("catalog/connectors is not valid:\n%v", err)
	}
	t.Logf("catalog/connectors: %d apps, %d platforms", len(cat.Apps), len(cat.Platforms))

	t.Run("the same checks pass the fixture and refuse a broken one", func(t *testing.T) {
		if _, err := validateRepoCatalog(fixture(t)); err != nil {
			t.Fatalf("fixture: %v", err)
		}
		for name, broken := range map[string]func(t *testing.T) fs.FS{
			"L0": func(t *testing.T) fs.FS {
				fsys := fixture(t)
				edit(t, fsys, "apps/linear/app.yaml", "\nname: Linear\n", "\nname: Linear\nprice: 0\n")
				return fsys
			},
			"L1": func(t *testing.T) fs.FS {
				fsys := fixture(t)
				edit(t, fsys, "apps/linear/providers/linear-mcp.yaml", "scopes: [read, write]\n", "scopes: [read, write, admin]\n")
				return fsys
			},
			"open data": func(t *testing.T) fs.FS {
				fsys := fixture(t)
				edit(t, fsys, "apps/github/providers/local.yaml", "publisher: vendor\n", "publisher: community\n")
				return fsys
			},
		} {
			if _, err := validateRepoCatalog(broken(t)); err == nil {
				t.Errorf("%s: a broken catalog passed", name)
			}
		}
	})
}
