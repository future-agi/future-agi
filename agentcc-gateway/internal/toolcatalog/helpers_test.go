package toolcatalog

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// validCard is a card that passes every check, as decoded JSON, for tests to
// mutate.
func validCard() map[string]any {
	return map[string]any{
		"vendor":     "acme",
		"capability": "search",
		"skus": []any{
			map[string]any{"sku": "acme.search", "unit": "request", "usd_per_unit": 0.005, "free_on_failure": true},
		},
		"resale_status":       "unconfirmed",
		"resale_terms_ref":    "",
		"terms_url":           "https://acme.example/terms",
		"terms_checked_at":    "2026-10-15",
		"zdr_capable":         false,
		"trains_on_input":     "no",
		"improvement_license": false,
		"regions":             []any{"us"},
		"results_retention":   "full",
		"cacheable":           "none",
		"eval_export":         "allowed",
		"no_divert":           false,
		"no_perf_disclosure":  false,
		"no_benchmark":        false,
	}
}

func mustJSON(t *testing.T, v any) []byte {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// writeCards writes cards as <capability>/<vendor>.json under a new temp dir
// and returns the dir.
func writeCards(t *testing.T, cards ...map[string]any) string {
	t.Helper()
	dir := t.TempDir()
	for _, c := range cards {
		writeFile(t, dir, c["capability"].(string)+"/"+c["vendor"].(string)+".json", mustJSON(t, c))
	}
	return dir
}

func writeFile(t *testing.T, dir, rel string, data []byte) {
	t.Helper()
	p := filepath.Join(dir, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, data, 0o600); err != nil {
		t.Fatal(err)
	}
}

// with returns a copy of card with key set to v (or removed when v is
// deleteKey).
func with(card map[string]any, key string, v any) map[string]any {
	out := make(map[string]any, len(card))
	for k, x := range card {
		out[k] = x
	}
	if v == deleteKey {
		delete(out, key)
	} else {
		out[key] = v
	}
	return out
}

type deleteMarker struct{}

var deleteKey = deleteMarker{}

func seedCatalog(t *testing.T) *Catalog {
	t.Helper()
	cat, err := SeedCatalog()
	if err != nil {
		t.Fatalf("the embedded seed cards do not load: %v", err)
	}
	return cat
}

// today is the current UTC date.
func today() time.Time {
	t, _ := parseDate(time.Now().UTC().Format(time.DateOnly))
	return t
}
