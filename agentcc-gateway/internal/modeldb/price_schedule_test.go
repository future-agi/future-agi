package modeldb

import (
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"
)

// The last instant of 2026 and the first of 2027, both UTC.
var (
	t26 = time.Date(2026, 12, 31, 23, 59, 59, 999999999, time.UTC)
	t27 = time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC)
)

// datedKeys are the big-table keys of Gemini 3.6/3.7/3.8 Flash that carry dated rows.
var datedKeys = []string{
	"gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
	"gemini/gemini-3.6-flash", "gemini/gemini-3.7-flash", "gemini/gemini-3.8-flash",
	"vertex_ai/gemini-3.6-flash", "vertex_ai/gemini-3.7-flash", "vertex_ai/gemini-3.8-flash",
}

// Per-token rates of the two rows: input, output, cached, batch input, batch output.
var (
	rates2026 = [5]float64{7.5e-7, 3.75e-6, 7.5e-8, 3.75e-7, 1.875e-6}
	rates2027 = [5]float64{1.5e-6, 7.5e-6, 1.5e-7, 7.5e-7, 3.75e-6}
)

// 1M tokens in and 100k out cost 1.125 at the 2026 rates and 2.25 at the 2027 ones.
const (
	cost2026 = 1.125
	cost2027 = 2.25
)

func near(a, b float64) bool { return math.Abs(a-b) <= 1e-12 }

func fiveRates(p PricingInfo) [5]float64 {
	return [5]float64{p.InputPerToken, p.OutputPerToken, p.CachedInputPerToken, p.BatchInputPerToken, p.BatchOutputPerToken}
}

func assertRates(t *testing.T, what string, p PricingInfo, want [5]float64) {
	t.Helper()
	got := fiveRates(p)
	for i := range got {
		if !near(got[i], want[i]) {
			t.Errorf("%s: rates %v, want %v", what, got, want)
			return
		}
	}
}

func assertCost(t *testing.T, db *ModelDB, model string, opts CostOptions, want float64) {
	t.Helper()
	got, ok := db.CalculateCost(model, 1_000_000, 100_000, opts)
	if !ok || !near(got, want) {
		t.Errorf("CalculateCost(%q, at %s, table price %v) = %v, %v; want %v", model, opts.At.Format(time.RFC3339Nano), opts.TablePrice, got, ok, want)
	}
}

// rowFields is a valid row with every price derived from input; tests edit it.
func rowFields(from string, input float64) map[string]any {
	return map[string]any{
		"from":                          from,
		"input_cost_per_token":          input,
		"output_cost_per_token":         input * 5,
		"cache_read_input_token_cost":   input / 10,
		"input_cost_per_token_batches":  input / 2,
		"output_cost_per_token_batches": input * 2.5,
	}
}

// scheduleText is a schedule file holding key with rows.
func scheduleText(t *testing.T, key string, rows ...map[string]any) string {
	t.Helper()
	data, err := json.Marshal(map[string]any{key: rows})
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func mustParse(t *testing.T, text string) map[string][]scheduleRow {
	t.Helper()
	schedule, err := parsePriceSchedule([]byte(text))
	if err != nil {
		t.Fatalf("parsePriceSchedule: %v", err)
	}
	return schedule
}

// syntheticDB is a ModelDB of one model "m" priced table, with the rows of text.
func syntheticDB(t *testing.T, table PricingInfo, text string) *ModelDB {
	t.Helper()
	bundled := map[string]*ModelInfo{"m": {ID: "m", Mode: ModeChat, Pricing: table}}
	schedule := mustParse(t, text)
	if err := checkPriceSchedule(bundled, schedule); err != nil {
		t.Fatalf("checkPriceSchedule: %v", err)
	}
	addScheduleForTest(t, schedule)
	return New(bundled, nil)
}

// addScheduleForTest adds schedule's keys to the embedded schedule until the
// test ends. Production never changes it; no test in this package runs in
// parallel.
func addScheduleForTest(t *testing.T, schedule map[string][]scheduleRow) {
	t.Helper()
	saved := priceSchedule
	merged := make(map[string][]scheduleRow, len(saved)+len(schedule))
	for key, rows := range saved {
		merged[key] = rows
	}
	for key, rows := range schedule {
		merged[key] = rows
	}
	priceSchedule = merged
	t.Cleanup(func() { priceSchedule = saved })
}

func mustGet(t *testing.T, db *ModelDB, id string) *ModelInfo {
	t.Helper()
	m, ok := db.Get(id)
	if !ok {
		t.Fatalf("the table does not list %q", id)
	}
	return m
}

func TestDatedPriceSwitchesAtMidnightUTC(t *testing.T) {
	db := New(BundledModels, nil)
	for _, key := range datedKeys {
		assertCost(t, db, key, CostOptions{At: t26}, cost2026)
		assertCost(t, db, key, CostOptions{At: t27}, cost2027)
		m := mustGet(t, db, key)
		assertRates(t, key+" on 31 Dec", m.PricingAt(t26), rates2026)
		assertRates(t, key+" on 1 Jan", m.PricingAt(t27), rates2027)
	}

	// The same instant written in another zone takes the same row.
	m := mustGet(t, db, "gemini-3.7-flash")
	for _, tc := range []struct {
		at   string
		want [5]float64
	}{
		{"2027-01-01T05:30:00+05:30", rates2027},
		{"2026-12-31T23:00:00-05:00", rates2027}, // 04:00 UTC on 1 Jan
		{"2026-12-31T23:59:59+00:00", rates2026},
	} {
		at, err := time.Parse(time.RFC3339, tc.at)
		if err != nil {
			t.Fatal(err)
		}
		assertRates(t, tc.at, m.PricingAt(at), tc.want)
	}
}

func TestBeforeTheFirstRowTheTablesPriceApplies(t *testing.T) {
	db := syntheticDB(t,
		PricingInfo{InputPerToken: 1e-6, OutputPerToken: 2e-6, InputPerImage: 0.04},
		scheduleText(t, "m", rowFields("2026-01-01", 3e-6), rowFields("2027-01-01", 5e-6)))
	m := mustGet(t, db, "m")
	for _, tc := range []struct {
		at        time.Time
		wantInput float64
	}{
		{time.Date(2025, 12, 31, 23, 59, 59, 0, time.UTC), 1e-6},
		{time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), 3e-6},
		{t27, 5e-6},
	} {
		p := m.PricingAt(tc.at)
		if !near(p.InputPerToken, tc.wantInput) {
			t.Errorf("PricingAt(%s).InputPerToken = %g, want %g", tc.at.Format(time.RFC3339), p.InputPerToken, tc.wantInput)
		}
		if p.InputPerImage != 0.04 {
			t.Errorf("PricingAt(%s).InputPerImage = %g, want the table's 0.04", tc.at.Format(time.RFC3339), p.InputPerImage)
		}
	}
}

func TestZeroUsageTimeMeansNow(t *testing.T) {
	db := syntheticDB(t, PricingInfo{InputPerToken: 1e-6, OutputPerToken: 1e-6},
		scheduleText(t, "m", rowFields("2000-01-01", 9e-6)))
	got, ok := db.CalculateCost("m", 1_000_000, 0, CostOptions{})
	if !ok || !near(got, 9.0) {
		t.Fatalf("CalculateCost with no usage time = %v, %v; want 9 (the row in force now, not the table's 1)", got, ok)
	}
}

func TestGetPricingQuotesTheRowInForceNow(t *testing.T) {
	db := syntheticDB(t, PricingInfo{InputPerToken: 1e-6, OutputPerToken: 1e-6},
		scheduleText(t, "m", rowFields("2000-01-01", 9e-6)))
	p, ok := db.GetPricing("m")
	if !ok || !near(p.InputPerToken, 9e-6) {
		t.Fatalf("GetPricing(m) = %+v, %v; want input 9e-6", p, ok)
	}
}

func TestOtherNamesOfTheseModelsGetTheDatedPrice(t *testing.T) {
	db := New(BundledModels, nil)
	for _, name := range []string{
		"google/gemini-3.7-flash", "models/gemini-3.7-flash", "publishers/google/models/gemini-3.7-flash",
		"vertex_ai_global/gemini-3.7-flash", "google/gemini-3.6-flash", "google/gemini-3.8-flash",
	} {
		assertCost(t, db, name, CostOptions{At: t27}, cost2027)
	}
	// Resellers' own entries and other models of the family carry no dated
	// rows: on both sides of midnight they cost what their own big-table entry
	// says, whatever litellm.json holds for them.
	for _, name := range []string{
		"openrouter/google/gemini-3.7-flash", "perplexity/google/gemini-3.7-flash",
		"deepinfra/google/gemini-3.7-flash", "aihubmix/gemini-3.7-flash",
		"github_copilot/gemini-3.8-flash", "gemini-3.8-flash-cyber",
	} {
		m := mustGet(t, db, name)
		if m.ID != name {
			t.Fatalf("%q resolves to the entry %q, not its own", name, m.ID)
		}
		want := tableCost(m)
		assertCost(t, db, name, CostOptions{At: t26}, want)
		assertCost(t, db, name, CostOptions{At: t27}, want)
		assertRates(t, name+" on 31 Dec", m.PricingAt(t26), fiveRates(m.Pricing))
		assertRates(t, name+" on 1 Jan", m.PricingAt(t27), fiveRates(m.Pricing))
	}
}

// tableCost is what 1M tokens in and 100k out cost at an entry's own
// big-table price, read from the table rather than written down, so a
// refresh that moves that price does not change what the test expects.
func tableCost(m *ModelInfo) float64 {
	p := m.Pricing
	return p.Calculate(1_000_000, 100_000, CostOptions{})
}

func TestPriceOverrideWinsFieldByField(t *testing.T) {
	db := New(BundledModels, map[string]ModelOverride{
		"gemini-3.7-flash": {Pricing: &PricingOverride{InputPerToken: ptr(2e-6)}},
	})
	m := mustGet(t, db, "gemini-3.7-flash")
	if p := m.PricingAt(t26); !near(p.InputPerToken, 2e-6) || !near(p.OutputPerToken, 3.75e-6) {
		t.Errorf("31 Dec: input %g output %g, want 2e-6 and 3.75e-6", p.InputPerToken, p.OutputPerToken)
	}
	if p := m.PricingAt(t27); !near(p.InputPerToken, 2e-6) || !near(p.OutputPerToken, 7.5e-6) || !near(p.CachedInputPerToken, 1.5e-7) {
		t.Errorf("1 Jan: input %g output %g cached %g, want 2e-6, 7.5e-6, 1.5e-7", p.InputPerToken, p.OutputPerToken, p.CachedInputPerToken)
	}
}

func TestCachedOnlyOverrideKeepsTheDatedInputAndOutput(t *testing.T) {
	db := New(BundledModels, map[string]ModelOverride{
		"gemini-3.7-flash": {Pricing: &PricingOverride{CachedInputPerToken: ptr(1e-8)}},
	})
	p := mustGet(t, db, "gemini-3.7-flash").PricingAt(t27)
	if !near(p.InputPerToken, 1.5e-6) || !near(p.OutputPerToken, 7.5e-6) || !near(p.CachedInputPerToken, 1e-8) {
		t.Errorf("1 Jan: input %g output %g cached %g, want 1.5e-6, 7.5e-6, 1e-8", p.InputPerToken, p.OutputPerToken, p.CachedInputPerToken)
	}
	assertCost(t, db, "gemini-3.7-flash", CostOptions{At: t27}, cost2027)
}

func TestCapabilitiesOnlyOverrideKeepsTheDatedPrice(t *testing.T) {
	db := New(BundledModels, map[string]ModelOverride{
		"vertex_ai/gemini-3.7-flash": {
			Capabilities:    &CapOverride{FunctionCalling: ptr(false)},
			MaxOutputTokens: ptr(1000),
		},
	})
	if m := mustGet(t, db, "vertex_ai/gemini-3.7-flash"); m.MaxOutputTokens != 1000 || m.Capabilities.FunctionCalling {
		t.Fatalf("the override was not applied: max output %d, function calling %v", m.MaxOutputTokens, m.Capabilities.FunctionCalling)
	}
	assertCost(t, db, "vertex_ai/gemini-3.7-flash", CostOptions{At: t27}, cost2027)
}

func TestOverriddenRowsAreNotShared(t *testing.T) {
	a := New(BundledModels, map[string]ModelOverride{
		"gemini-3.7-flash": {Pricing: &PricingOverride{InputPerToken: ptr(2e-6)}},
	})
	if got := mustGet(t, a, "gemini-3.7-flash").PricingAt(t27).InputPerToken; !near(got, 2e-6) {
		t.Fatalf("overridden table: 1 Jan input %g, want 2e-6", got)
	}
	b := New(BundledModels, nil)
	if got := mustGet(t, b, "gemini-3.7-flash").PricingAt(t27).InputPerToken; !near(got, 1.5e-6) {
		t.Errorf("a table built after an overridden one: 1 Jan input %g, want 1.5e-6", got)
	}
	if got := BundledModels["gemini-3.7-flash"].PricingAt(t27).InputPerToken; !near(got, 1.5e-6) {
		t.Errorf("bundled entry on 1 Jan: input %g, want 1.5e-6", got)
	}
	if got := *priceSchedule["gemini-3.7-flash"][1].InputCostPerToken; !near(got, 1.5e-6) {
		t.Errorf("the schedule's 2027 row: input %g, want 1.5e-6", got)
	}
}

// bigTableDrift lists the dated keys whose big-table price equals neither
// their first row nor, once its date has passed, their last row.
func bigTableDrift(table map[string]*ModelInfo, schedule map[string][]scheduleRow, now time.Time) []string {
	keys := make([]string, 0, len(schedule))
	for key := range schedule {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	var drift []string
	for _, key := range keys {
		m, ok := table[key]
		if !ok {
			drift = append(drift, fmt.Sprintf("%s: litellm.json does not list this key, which price_schedule.json dates. Restore the key or remove it from both copies of price_schedule.json.", key))
			continue
		}
		rows := schedule[key]
		first, last := rows[0], rows[len(rows)-1]
		got := fiveRates(m.Pricing)
		if got == rowRates(first) || (!now.Before(last.start) && got == rowRates(last)) {
			continue
		}
		drift = append(drift, fmt.Sprintf("%s: litellm.json equals neither the dated file's first row nor, from %s, its last row (input %g, output %g, cached %g, batch %g / %g). If a refresh moved this price before its date, fix the refresh: the dated row keeps billing the old price until then. If the price changed again, update price_schedule.json in both copies.",
			key, last.From, got[0], got[1], got[2], got[3], got[4]))
	}
	return drift
}

func rowRates(r scheduleRow) [5]float64 {
	return [5]float64{*r.InputCostPerToken, *r.OutputCostPerToken, *r.CacheReadInputTokenCost, *r.InputCostPerTokenBatches, *r.OutputCostPerTokenBatches}
}

func rowPricing(r scheduleRow) PricingInfo {
	return PricingInfo{
		InputPerToken:       *r.InputCostPerToken,
		OutputPerToken:      *r.OutputCostPerToken,
		CachedInputPerToken: *r.CacheReadInputTokenCost,
		BatchInputPerToken:  *r.InputCostPerTokenBatches,
		BatchOutputPerToken: *r.OutputCostPerTokenBatches,
	}
}

func TestBigTableAgreesWithTheDatedFile(t *testing.T) {
	schedule, err := parsePriceSchedule(priceScheduleData)
	if err != nil {
		t.Fatalf("parsePriceSchedule: %v", err)
	}
	for _, msg := range bigTableDrift(BundledModels, schedule, time.Now()) {
		t.Error(msg)
	}
}

func TestBigTableCheckAcceptsTheLastRowOnlyFromItsDate(t *testing.T) {
	schedule := mustParse(t, scheduleText(t, "m", rowFields("2026-01-01", 7.5e-7), rowFields("2027-01-01", 1.5e-6)))
	a, b := rowPricing(schedule["m"][0]), rowPricing(schedule["m"][1])
	neither := a
	neither.InputPerToken = 2e-6
	december := time.Date(2026, 12, 15, 12, 0, 0, 0, time.UTC)
	tableOf := func(p PricingInfo) map[string]*ModelInfo { return map[string]*ModelInfo{"m": {ID: "m", Pricing: p}} }

	for _, tc := range []struct {
		name  string
		table map[string]*ModelInfo
		now   time.Time
		want  int
	}{
		{"first row in December", tableOf(a), december, 0},
		{"first row in January, not yet caught up", tableOf(a), t27, 0},
		{"last row in December, moved early", tableOf(b), december, 1},
		{"last row on 31 Dec, moved early", tableOf(b), t26, 1},
		{"last row in January, caught up", tableOf(b), t27, 0},
		{"another price", tableOf(neither), t27, 1},
		{"key missing", map[string]*ModelInfo{}, t27, 1},
	} {
		if got := bigTableDrift(tc.table, schedule, tc.now); len(got) != tc.want {
			t.Errorf("%s: %d messages %q, want %d", tc.name, len(got), got, tc.want)
		}
	}
}

func TestDatedKeysAreEveryGoogleKeyOfTheseModels(t *testing.T) {
	family := regexp.MustCompile(`^(vertex_ai/|gemini/)?gemini-3\.[678]-flash(-.+)?$`)
	otherModels := map[string]bool{
		"gemini-3.8-flash-cyber": true, "vertex_ai/gemini-3.8-flash-cyber": true,
		"gemini/gemini-3.8-flash-tts": true, "gemini/gemini-3.8-flash-lite-tts": true,
		"vertex_ai/gemini-3.8-flash-tts": true, "vertex_ai/gemini-3.8-flash-lite-tts": true,
	}
	schedule, err := parsePriceSchedule(priceScheduleData)
	if err != nil {
		t.Fatalf("parsePriceSchedule: %v", err)
	}
	var missing, extra []string
	for key := range BundledModels {
		if family.MatchString(key) && !otherModels[key] {
			if _, ok := schedule[key]; !ok {
				missing = append(missing, key)
			}
		}
	}
	for key := range schedule {
		if !family.MatchString(key) || otherModels[key] {
			extra = append(extra, key)
		}
	}
	sort.Strings(missing)
	sort.Strings(extra)
	if len(missing) > 0 {
		t.Errorf("litellm.json keys of these models with no dated rows (add them to both copies of price_schedule.json, or to the other-model list here): %v", missing)
	}
	if len(extra) > 0 {
		t.Errorf("price_schedule.json keys that are not Google keys of these models: %v", extra)
	}
}

func TestParsePriceScheduleRejects(t *testing.T) {
	without := func(field string) map[string]any {
		r := rowFields("2027-01-01", 1.5e-6)
		delete(r, field)
		return r
	}
	with := func(field string, v any) map[string]any {
		r := rowFields("2027-01-01", 1.5e-6)
		r[field] = v
		return r
	}
	valid := scheduleText(t, "k", rowFields("2026-01-01", 7.5e-7), rowFields("2027-01-01", 1.5e-6))
	if _, err := parsePriceSchedule([]byte(valid)); err != nil {
		t.Fatalf("a valid schedule was refused: %v", err)
	}

	for _, tc := range []struct {
		name, text, want string
	}{
		{"not an object", `[]`, "price schedule must be a JSON object of model keys: "},
		{"empty object", `{}`, "price schedule lists no models"},
		{"empty key", scheduleText(t, "", rowFields("2027-01-01", 1.5e-6)), "price schedule has an empty model key"},
		{"no rows", `{"k": []}`, `price schedule entry "k" has no rows`},
		{"null rows", `{"k": null}`, `price schedule entry "k" has no rows`},
		{"missing price", scheduleText(t, "k", without("cache_read_input_token_cost")), `price schedule entry "k" row 0 is missing cache_read_input_token_cost`},
		{"extra field", scheduleText(t, "k", with("note", "x")), `price schedule must be a JSON object of model keys: json: unknown field "note"`},
		{"zero price", scheduleText(t, "k", with("input_cost_per_token", 0)), `price schedule entry "k" row 0: input_cost_per_token must be a positive price`},
		{"negative price", scheduleText(t, "k", with("input_cost_per_token", -1e-6)), `price schedule entry "k" row 0: input_cost_per_token must be a positive price`},
		{"short date", scheduleText(t, "k", with("from", "2027-1-1")), `price schedule entry "k" row 0: from "2027-1-1" is not a YYYY-MM-DD date`},
		{"date with a time", scheduleText(t, "k", with("from", "2027-01-01T00:00:00Z")), `price schedule entry "k" row 0: from "2027-01-01T00:00:00Z" is not a YYYY-MM-DD date`},
		{"same date twice", scheduleText(t, "k", rowFields("2027-01-01", 1.5e-6), rowFields("2027-01-01", 3e-6)), `price schedule entry "k" rows must start on strictly later dates`},
		{"descending dates", scheduleText(t, "k", rowFields("2027-01-01", 1.5e-6), rowFields("2026-01-01", 7.5e-7)), `price schedule entry "k" rows must start on strictly later dates`},
		{"trailing data", valid + ` {}`, "price schedule has data after its JSON object"},
	} {
		_, err := parsePriceSchedule([]byte(tc.text))
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("%s: error %v, want one containing %q", tc.name, err, tc.want)
		}
	}
}

func TestAttachPriceScheduleRejectsAKeyTheTableLacks(t *testing.T) {
	schedule := mustParse(t, scheduleText(t, "vertex_ai/gemini-3.7-flash-001", rowFields("2027-01-01", 1.5e-6)))
	err := checkPriceSchedule(map[string]*ModelInfo{}, schedule)
	want := `price schedule names "vertex_ai/gemini-3.7-flash-001", which the bundled price table does not list`
	if err == nil || err.Error() != want {
		t.Fatalf("error %v, want %q", err, want)
	}
}

func TestTablePriceSkipsTheDatedRow(t *testing.T) {
	// A table price and a row that differ whatever litellm.json holds:
	// 1M in and 100k out cost 1.1 at the table's price and 13.5 at the row's.
	synthetic := syntheticDB(t, PricingInfo{InputPerToken: 1e-6, OutputPerToken: 1e-6},
		scheduleText(t, "m", rowFields("2000-01-01", 9e-6)))
	assertCost(t, synthetic, "m", CostOptions{At: t27, TablePrice: true}, 1.1)
	assertCost(t, synthetic, "m", CostOptions{At: t27}, 13.5)

	// The bundled entry: its own table price on both sides of midnight.
	db := New(BundledModels, nil)
	want := tableCost(mustGet(t, db, "gemini-3.7-flash"))
	assertCost(t, db, "gemini-3.7-flash", CostOptions{At: t26, TablePrice: true}, want)
	assertCost(t, db, "gemini-3.7-flash", CostOptions{At: t27, TablePrice: true}, want)
	assertCost(t, db, "gemini-3.7-flash", CostOptions{At: t27}, cost2027)
}

// Every row starts at 00:00:00 UTC of its date, read in UTC whatever the
// zone of the machine parsing it.
func TestScheduleRowsStartAtMidnightUTC(t *testing.T) {
	schedule, err := parsePriceSchedule(priceScheduleData)
	if err != nil {
		t.Fatalf("parsePriceSchedule: %v", err)
	}
	for key, rows := range schedule {
		for i, row := range rows {
			if row.start.Location() != time.UTC {
				t.Errorf("%s row %d: from %q was read in the zone %q, want UTC", key, i, row.From, row.start.Location())
			}
			if h, m, s := row.start.Clock(); h != 0 || m != 0 || s != 0 || row.start.Nanosecond() != 0 || row.start.Format(time.DateOnly) != row.From {
				t.Errorf("%s row %d: from %q starts at %s, want midnight of that date", key, i, row.From, row.start.Format(time.RFC3339Nano))
			}
		}
	}
	rows := schedule["gemini-3.7-flash"]
	if len(rows) != 2 || !rows[0].start.Equal(time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)) || !rows[1].start.Equal(t27) {
		t.Errorf("gemini-3.7-flash rows start at %v, want 2026-01-01 and 2027-01-01 at 00:00:00 UTC", rows)
	}
}

// A day into 2027, and the last millisecond of 2026, both UTC.
var (
	jan2       = time.Date(2027, 1, 2, 0, 0, 0, 0, time.UTC)
	lastMs2026 = time.Date(2026, 12, 31, 23, 59, 59, 999_000_000, time.UTC)
)

// A table built with New from entries that never went through the bundled
// loader still bills the dated rows: they are found by the entry's key, not
// carried on the entry.
func TestDatedPriceNeedsNoLoadedEntries(t *testing.T) {
	table := PricingInfo{
		InputPerToken: 7.5e-7, OutputPerToken: 3.75e-6, CachedInputPerToken: 7.5e-8,
		BatchInputPerToken: 3.75e-7, BatchOutputPerToken: 1.875e-6,
	}
	db := New(map[string]*ModelInfo{
		"gemini-3.7-flash":           {Provider: "gemini", Mode: ModeChat, Pricing: table},
		"vertex_ai/gemini-3.7-flash": {Provider: "vertex_ai", Mode: ModeChat, Pricing: table},
	}, nil)
	for _, key := range []string{"gemini-3.7-flash", "vertex_ai/gemini-3.7-flash"} {
		m := mustGet(t, db, key)
		assertRates(t, key+" on 2 Jan", m.PricingAt(jan2), rates2027)
		assertRates(t, key+" on 31 Dec", m.PricingAt(lastMs2026), rates2026)
		assertCost(t, db, key, CostOptions{At: jan2}, cost2027)
		assertCost(t, db, key, CostOptions{At: lastMs2026}, cost2026)
	}
	// Another name for the model is priced by the entry it resolves to.
	assertCost(t, db, "google/gemini-3.7-flash", CostOptions{At: jan2}, cost2027)
}

// freshEntry parses key's litellm.json entry again on its own, as a catalogue
// sync would, giving an entry that init never saw.
func freshEntry(t *testing.T, key string) *ModelInfo {
	t.Helper()
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(liteLLMData, &raw); err != nil {
		t.Fatal(err)
	}
	entry, ok := raw[key]
	if !ok {
		t.Fatalf("litellm.json does not list %q", key)
	}
	snippet, err := json.Marshal(map[string]json.RawMessage{key: entry})
	if err != nil {
		t.Fatal(err)
	}
	fresh, err := parseLiteLLM(snippet)
	if err != nil {
		t.Fatalf("parseLiteLLM: %v", err)
	}
	m, ok := fresh[key]
	if !ok {
		t.Fatalf("parseLiteLLM skipped %q", key)
	}
	if m == BundledModels[key] {
		t.Fatalf("the fresh %q entry is the bundled one", key)
	}
	return m
}

// swappedTable is the bundled table with key's entry replaced by a fresh parse.
func swappedTable(t *testing.T, key string) map[string]*ModelInfo {
	t.Helper()
	table := make(map[string]*ModelInfo, len(BundledModels))
	for id, m := range BundledModels {
		table[id] = m
	}
	table[key] = freshEntry(t, key)
	return table
}

func TestDatedPriceSurvivesAnEntrySwap(t *testing.T) {
	for _, key := range []string{"gemini-3.7-flash", "vertex_ai/gemini-3.8-flash"} {
		db := New(swappedTable(t, key), nil)
		assertRates(t, key+" on 2 Jan after a swap", mustGet(t, db, key).PricingAt(jan2), rates2027)
		assertCost(t, db, key, CostOptions{At: jan2}, cost2027)
		assertCost(t, db, key, CostOptions{At: lastMs2026}, cost2026)
	}
}

func TestPriceOverrideSurvivesAnEntrySwap(t *testing.T) {
	const key = "gemini-3.7-flash"
	db := New(swappedTable(t, key), map[string]ModelOverride{
		key: {Pricing: &PricingOverride{CachedInputPerToken: ptr(1e-8)}},
	})
	p := mustGet(t, db, key).PricingAt(jan2)
	if !near(p.InputPerToken, 1.5e-6) || !near(p.OutputPerToken, 7.5e-6) || !near(p.CachedInputPerToken, 1e-8) {
		t.Errorf("2 Jan after a swap: input %g output %g cached %g, want 1.5e-6, 7.5e-6, 1e-8", p.InputPerToken, p.OutputPerToken, p.CachedInputPerToken)
	}
	// The 2026 row, not the big table, sets the price before midnight, so this
	// holds whatever litellm.json says for the key.
	p = mustGet(t, db, key).PricingAt(lastMs2026)
	if !near(p.InputPerToken, 7.5e-7) || !near(p.OutputPerToken, 3.75e-6) || !near(p.CachedInputPerToken, 1e-8) {
		t.Errorf("31 Dec after a swap: input %g output %g cached %g, want 7.5e-7, 3.75e-6, 1e-8", p.InputPerToken, p.OutputPerToken, p.CachedInputPerToken)
	}
}
