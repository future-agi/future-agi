package config

import (
	"bytes"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The catalogue sync is on by default; an empty URL in the file turns it off,
// and both keys are read, so neither draws the unread-key warning.
func TestLoad_ModelCatalogueKeys(t *testing.T) {
	var logs bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	load := func(body string) *Config {
		path := filepath.Join(t.TempDir(), "config.yaml")
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatalf("writing config: %v", err)
		}
		cfg, err := Load(path)
		if err != nil {
			t.Fatalf("Load: %v", err)
		}
		return cfg
	}

	def := load("server:\n  port: 8080\n")
	if def.ModelDatabase.CatalogueURL == "" || def.ModelDatabase.CatalogueSyncInterval != 24*time.Hour {
		t.Errorf("defaults = %q, %v", def.ModelDatabase.CatalogueURL, def.ModelDatabase.CatalogueSyncInterval)
	}

	off := load("model_database:\n  catalogue_url: \"\"\n  catalogue_sync_interval: 6h\n")
	if off.ModelDatabase.CatalogueURL != "" || off.ModelDatabase.CatalogueSyncInterval != 6*time.Hour {
		t.Errorf("explicit = %q, %v", off.ModelDatabase.CatalogueURL, off.ModelDatabase.CatalogueSyncInterval)
	}
	if strings.Contains(logs.String(), "does not read") {
		t.Errorf("catalogue keys drew an unread-key warning:\n%s", logs.String())
	}
}
