package pricing

import (
	"context"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

var (
	// The last instant of 2026 (to the millisecond), midnight of 2027, and the day after.
	t26 = time.Date(2026, 12, 31, 23, 59, 59, 999_000_000, time.UTC)
	t27 = time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC)
	j2  = time.Date(2027, 1, 2, 0, 0, 0, 0, time.UTC)
)

const million = 1_000_000

func nearly(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

func costAt(t *testing.T, tbl *Table, model string, at time.Time) float64 {
	t.Helper()
	c, ok := tbl.Cost(model, million, million, at)
	if !ok {
		t.Fatalf("%s is not priced", model)
	}
	return c
}

// The bare, gemini/ and vertex_ai/ keys of gemini-3.7-flash bill 0.75/3.75
// until the end of 2026 and 1.5/7.5 from midnight.
func TestCostFollowsTheDatedRow(t *testing.T) {
	tbl, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"gemini-3.7-flash", "gemini/gemini-3.7-flash", "vertex_ai/gemini-3.7-flash"} {
		if got := costAt(t, tbl, key, t26); !nearly(got, 4.5) {
			t.Errorf("%s at %s = %v, want 4.5", key, t26, got)
		}
		if got := costAt(t, tbl, key, t27); !nearly(got, 9.0) {
			t.Errorf("%s at %s = %v, want 9.0", key, t27, got)
		}
	}
}

// A bare key reached through its provider prefix is dated too, and an
// OpenRouter key (never in the schedule) is not.
func TestPrefixedAndOpenRouterCosts(t *testing.T) {
	tbl, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	const prefixed = "vertex_ai-language-models/gemini-3.7-flash"
	for at, want := range map[time.Time]float64{t26: 4.5, t27: 9.0} {
		got, ok := tbl.ProviderPrefixedCost(prefixed, million, million, at)
		if !ok || !nearly(got, want) {
			t.Errorf("%s at %s = %v, %v; want %v", prefixed, at, got, ok, want)
		}
	}
	got, ok := tbl.OpenRouterCost("google/gemini-3.7-flash", million, million, j2)
	if !ok || !nearly(got, 4.5) {
		t.Errorf("OpenRouterCost at %s = %v, %v; want 4.5, its own undated price", j2, got, ok)
	}
}

// The schedule is held apart from every table, so a table built without
// LoadTable is dated all the same.
func TestScheduleDoesNotLiveOnTheTable(t *testing.T) {
	bare := &Table{prices: map[string]modelPrice{
		"gemini-3.7-flash": {InputCostPerToken: 7.5e-7, OutputCostPerToken: 3.75e-6},
	}}
	got, ok := New(bare, nil).TokenCost(context.Background(), "org", "gemini-3.7-flash", million, million, j2)
	if !ok || !nearly(got, 9.0) {
		t.Errorf("TokenCost on a bare table at %s = %v, %v; want 9.0", j2, got, ok)
	}
}

// pricesWithout writes the embedded price file minus the named keys to a temp dir.
func pricesWithout(t *testing.T, keys ...string) string {
	t.Helper()
	var entries map[string]json.RawMessage
	if err := json.Unmarshal(embeddedPrices, &entries); err != nil {
		t.Fatal(err)
	}
	for _, key := range keys {
		delete(entries, key)
	}
	data, err := json.Marshal(entries)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "prices.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

// LoadTable reports the dated keys a file lacks, and bills the others.
func TestLoadTableReportsMissingDatedKeys(t *testing.T) {
	whole, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	if len(whole.MissingDated) != 0 {
		t.Errorf("the embedded table lacks dated keys %v", whole.MissingDated)
	}

	partial, err := LoadTable(pricesWithout(t, "gemini-3.6-flash"))
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(partial.MissingDated, ","); got != "gemini-3.6-flash" {
		t.Errorf("MissingDated = %q, want gemini-3.6-flash", got)
	}
	if _, ok := partial.Cost("gemini-3.6-flash", million, million, j2); ok {
		t.Error("a key the file lacks was priced")
	}
	for _, key := range []string{"gemini-3.7-flash", "gemini/gemini-3.6-flash"} {
		if got := costAt(t, partial, key, j2); !nearly(got, 9.0) {
			t.Errorf("%s at %s = %v, want 9.0", key, j2, got)
		}
	}
}

func TestLoadTableSortsMissingDatedKeys(t *testing.T) {
	tbl, err := LoadTable(pricesWithout(t, "vertex_ai/gemini-3.8-flash", "gemini-3.6-flash", "gemini/gemini-3.7-flash"))
	if err != nil {
		t.Fatal(err)
	}
	want := "gemini-3.6-flash,gemini/gemini-3.7-flash,vertex_ai/gemini-3.8-flash"
	if got := strings.Join(tbl.MissingDated, ","); got != want {
		t.Errorf("MissingDated = %q, want %q", got, want)
	}
}

// An operator's file that adds an above-128k tier to a dated key cannot
// override the dated rate: a dated hit skips the tier.
func TestDatedRateSkipsTheTier(t *testing.T) {
	var entries map[string]json.RawMessage
	if err := json.Unmarshal(embeddedPrices, &entries); err != nil {
		t.Fatal(err)
	}
	entries["gemini-3.7-flash"] = json.RawMessage(`{"litellm_provider": "vertex_ai-language-models", "mode": "chat",
		"input_cost_per_token": 7.5e-7, "output_cost_per_token": 3.75e-6,
		"input_cost_per_token_above_128k_tokens": 5e-6, "output_cost_per_token_above_128k_tokens": 5e-5}`)
	data, err := json.Marshal(entries)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "tiered.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	tbl, err := LoadTable(path)
	if err != nil {
		t.Fatal(err)
	}
	// 200k in, 1M out at the dated 1.5/7.5 is 0.3 + 7.5; the tier would give 1 + 50.
	got, ok := tbl.Cost("gemini-3.7-flash", 200_000, million, j2)
	if !ok || !nearly(got, 7.8) {
		t.Errorf("Cost = %v, %v; want 7.8 (the dated rate, no tier)", got, ok)
	}
	// Before the date there is no dated row in force, so the tier applies.
	before, _ := tbl.Cost("gemini-3.7-flash", 200_000, million, time.Date(2025, 6, 1, 0, 0, 0, 0, time.UTC))
	if !nearly(before, 200_000*5e-6+million*5e-5) {
		t.Errorf("Cost before the first row = %v, want the tiered price", before)
	}
}

// ParseSchedule refuses every fault the gateway's parsePriceSchedule does
// (TestParsePriceScheduleRejects in agentcc-gateway/internal/modeldb).
func TestParseScheduleRejects(t *testing.T) {
	row := func(from string, input float64) map[string]any {
		return map[string]any{
			"from":                          from,
			"input_cost_per_token":          input,
			"output_cost_per_token":         input * 5,
			"cache_read_input_token_cost":   input / 10,
			"input_cost_per_token_batches":  input / 2,
			"output_cost_per_token_batches": input * 2.5,
		}
	}
	without := func(field string) map[string]any {
		r := row("2027-01-01", 1.5e-6)
		delete(r, field)
		return r
	}
	with := func(field string, v any) map[string]any {
		r := row("2027-01-01", 1.5e-6)
		r[field] = v
		return r
	}
	text := func(key string, rows ...map[string]any) string {
		data, err := json.Marshal(map[string]any{key: rows})
		if err != nil {
			t.Fatal(err)
		}
		return string(data)
	}
	valid := text("k", row("2026-01-01", 7.5e-7), row("2027-01-01", 1.5e-6))
	if _, err := parseSchedule([]byte(valid)); err != nil {
		t.Fatalf("a valid schedule was refused: %v", err)
	}

	for _, tc := range []struct {
		name, text, want string
	}{
		{"not an object", `[]`, "price schedule must be a JSON object of model keys: "},
		{"empty object", `{}`, "price schedule lists no models"},
		{"empty key", text("", row("2027-01-01", 1.5e-6)), "price schedule has an empty model key"},
		{"no rows", `{"k": []}`, `price schedule entry "k" has no rows`},
		{"null rows", `{"k": null}`, `price schedule entry "k" has no rows`},
		{"missing price", text("k", without("cache_read_input_token_cost")), `price schedule entry "k" row 0 is missing cache_read_input_token_cost`},
		{"extra field", text("k", with("note", "x")), `price schedule must be a JSON object of model keys: json: unknown field "note"`},
		{"zero price", text("k", with("input_cost_per_token", 0)), `price schedule entry "k" row 0: input_cost_per_token must be a positive price`},
		{"negative price", text("k", with("input_cost_per_token", -1e-6)), `price schedule entry "k" row 0: input_cost_per_token must be a positive price`},
		{"short date", text("k", with("from", "2027-1-1")), `price schedule entry "k" row 0: from "2027-1-1" is not a YYYY-MM-DD date`},
		{"date with a time", text("k", with("from", "2027-01-01T00:00:00Z")), `price schedule entry "k" row 0: from "2027-01-01T00:00:00Z" is not a YYYY-MM-DD date`},
		{"same date twice", text("k", row("2027-01-01", 1.5e-6), row("2027-01-01", 3e-6)), `price schedule entry "k" rows must start on strictly later dates`},
		{"descending dates", text("k", row("2027-01-01", 1.5e-6), row("2026-01-01", 7.5e-7)), `price schedule entry "k" rows must start on strictly later dates`},
		{"trailing data", valid + ` {}`, "price schedule has data after its JSON object"},
	} {
		_, err := parseSchedule([]byte(tc.text))
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("%s: error %v, want one containing %q", tc.name, err, tc.want)
		}
	}
}

func TestMustParseSchedulePanicsOnABadFile(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Error("mustParseSchedule accepted a bad file")
		}
	}()
	mustParseSchedule([]byte(`{}`))
}
