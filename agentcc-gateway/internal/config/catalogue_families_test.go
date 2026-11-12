package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCAT2ConfigValidation(t *testing.T) {
	for _, value := range []string{"[unknown]", "[cat2, cat2]", "cat2", "true", "[cat2, mixed]", "[CAT2]"} {
		t.Run(value, func(t *testing.T) {
			_, err := loadYAML(t, "managed:\n  catalogue_families: "+value+"\n")
			if err == nil {
				t.Fatal("invalid catalogue_families accepted")
			}
		})
	}
	for _, value := range []string{"[]", "[cat2]"} {
		if _, err := loadYAML(t, "managed:\n  catalogue_families: "+value+"\n"); err != nil {
			t.Fatal(err)
		}
	}
}

func TestCAT2EnvironmentValidation(t *testing.T) {
	for _, value := range []string{"true", "mixed", "cat2,mixed", "cat2,cat2", "cat2,", "CAT2"} {
		t.Run(value, func(t *testing.T) {
			t.Setenv("AGENTCC_MANAGED_CATALOGUE_FAMILIES", value)
			_, err := Load("")
			if err == nil || !strings.Contains(err.Error(), "catalogue_families") {
				t.Fatalf("invalid environment must fail load: %v", err)
			}
		})
	}
}

// Node config is a startup snapshot, not the hot-reloaded org config. Loading
// another file does not mutate the already loaded node's policy.
func TestCAT2ConfigRestartSnapshot(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	if err := os.WriteFile(path, []byte("managed:\n  catalogue_families: []\n"), 0600); err != nil {
		t.Fatal(err)
	}
	before, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("managed:\n  catalogue_families: [cat2]\n"), 0600); err != nil {
		t.Fatal(err)
	}
	after, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	// The behavioral catalogue test checks these snapshots' admission decisions.
	if before == after || before.Managed.CAT2Enabled() || !after.Managed.CAT2Enabled() {
		t.Fatal("startup snapshots aliased")
	}
}
