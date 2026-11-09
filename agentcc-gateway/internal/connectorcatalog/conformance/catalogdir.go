package conformance

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
)

// CatalogDir is where a run's catalogs are merged under its work directory:
// the gateway loads it as connectors.catalog_dir, so each case's catalog_ref
// is placed by the credential parts its provider declares (CN7), as the
// embedded catalog places a real one.
func CatalogDir(workDir string) string { return filepath.Join(workDir, "catalog") }

// WriteCatalog copies the records of every catalog (apps/ and platforms/,
// with each provider's tools, fixtures and evals) into dir, one catalog
// directory the gateway can load. Two catalogs that hold the same file are
// refused: the gateway would load only one of them.
func WriteCatalog(catalogs []Catalog, dir string) error {
	from := map[string]string{}
	for _, c := range catalogs {
		for _, root := range []string{"apps", "platforms"} {
			err := fs.WalkDir(c.FS, root, func(path string, d fs.DirEntry, err error) error {
				if err != nil {
					return err
				}
				if d.IsDir() || d.Name() == ".DS_Store" {
					return nil
				}
				if other, ok := from[path]; ok {
					return fmt.Errorf("catalogs %s and %s both hold %s: the gateway loads one catalog directory", other, c.Label, path)
				}
				from[path] = c.Label
				data, err := fs.ReadFile(c.FS, path)
				if err != nil {
					return err
				}
				out := filepath.Join(dir, filepath.FromSlash(path))
				if err := os.MkdirAll(filepath.Dir(out), 0o755); err != nil {
					return err
				}
				return os.WriteFile(out, data, 0o644)
			})
			if err != nil && !errors.Is(err, fs.ErrNotExist) {
				return err
			}
		}
	}
	return nil
}
