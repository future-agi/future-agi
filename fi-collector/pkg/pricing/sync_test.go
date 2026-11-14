package pricing

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// tableJSON is n models fetched-0..n-1 that cost price USD per token.
func tableJSON(n int, price float64) string {
	out := "{"
	for i := 0; i < n; i++ {
		if i > 0 {
			out += ","
		}
		out += fmt.Sprintf(`"fetched-%d":{"litellm_provider":"openai","input_cost_per_token":%g,"output_cost_per_token":%g}`, i, price, price)
	}
	return out + "}"
}

// withEntry adds one raw entry to a JSON object body.
func withEntry(body, key, entry string) string {
	return body[:len(body)-1] + fmt.Sprintf(`,%q:%s}`, key, entry)
}

type fakeCatalogue struct {
	*httptest.Server
	hits atomic.Int32
	body atomic.Value // string
	code atomic.Int32
}

func newFakeCatalogue(t *testing.T, body string) *fakeCatalogue {
	t.Helper()
	f := &fakeCatalogue{}
	f.body.Store(body)
	f.code.Store(http.StatusOK)
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.hits.Add(1)
		w.WriteHeader(int(f.code.Load()))
		fmt.Fprint(w, f.body.Load().(string))
	}))
	t.Cleanup(f.Close)
	return f
}

const gpt4oPrice = 1.25e-6 // 1000 in + 1000 out costs 0.0025 at 1.25e-6

// startingTable has n "base-i" models at 1e-6 and gpt-4o at gpt4oPrice, the
// way the embedded table has models a fetch may not.
func startingTable(t *testing.T, n int) *Table {
	t.Helper()
	body := fmt.Sprintf(`{"gpt-4o":{"litellm_provider":"openai","input_cost_per_token":%g,"output_cost_per_token":%g}}`, gpt4oPrice, gpt4oPrice)
	for i := 0; i < n; i++ {
		body = withEntry(body, fmt.Sprintf("base-%d", i), `{"litellm_provider":"openai","input_cost_per_token":1e-6,"output_cost_per_token":1e-6}`)
	}
	tbl, err := parseTable([]byte(body), false)
	if err != nil {
		t.Fatal(err)
	}
	return tbl
}

func newTestSyncer(t *testing.T, url string, baseEntries int) (*Syncer, *Pricer) {
	t.Helper()
	base := startingTable(t, baseEntries)
	p := New(base, nil)
	return NewSyncer(p, base, url, time.Hour, slog.Default()), p
}

func priced(p *Pricer, model string) bool {
	_, ok := p.TokenCost(context.Background(), "", model, 10, 10)
	return ok
}

func cost(p *Pricer, model string) float64 {
	c, _ := p.TokenCost(context.Background(), "", model, 1000, 1000)
	return c
}

func TestSync_GoodTableOverlaysStarting(t *testing.T) {
	f := newFakeCatalogue(t, tableJSON(60, 1e-6))
	s, p := newTestSyncer(t, f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if !priced(p, "fetched-7") {
		t.Error("fetched model not priced after a good sync")
	}
	if !priced(p, "gpt-4o") || !priced(p, "base-5") {
		t.Error("a starting model the fetch lacks was dropped; an older snapshot must not remove models")
	}
}

func TestSync_FetchedWinsOnMatchingKey(t *testing.T) {
	f := newFakeCatalogue(t, withEntry(tableJSON(60, 1e-6), "gpt-4o",
		`{"litellm_provider":"openai","input_cost_per_token":5e-6,"output_cost_per_token":5e-6}`))
	s, p := newTestSyncer(t, f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if got := cost(p, "gpt-4o"); got != 0.01 {
		t.Errorf("gpt-4o cost = %v, want 0.01 (fetched price)", got)
	}
}

// A fetched entry replaces the starting entry's provider too. Without it the
// prefix fallback would price "openai/gpt-4o" at the fetched rate under a
// provider the fetched entry never stated.
func TestSync_FetchedEntryReplacesProvider(t *testing.T) {
	cases := []struct {
		name       string
		entry      string
		wantOpenAI bool
		wantAzure  bool
	}{
		{"no provider", `{"input_cost_per_token":5e-6,"output_cost_per_token":5e-6}`, false, false},
		{"other provider", `{"litellm_provider":"azure","input_cost_per_token":5e-6,"output_cost_per_token":5e-6}`, false, true},
		{"same provider", `{"litellm_provider":"openai","input_cost_per_token":5e-6,"output_cost_per_token":5e-6}`, true, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeCatalogue(t, withEntry(tableJSON(60, 1e-6), "gpt-4o", tc.entry))
			s, p := newTestSyncer(t, f.URL, 100)
			if !priced(p, "openai/gpt-4o") {
				t.Fatal("starting table does not price openai/gpt-4o")
			}

			s.syncOnce(context.Background(), time.Second)

			if got := priced(p, "openai/gpt-4o"); got != tc.wantOpenAI {
				t.Errorf("openai/gpt-4o priced = %v, want %v", got, tc.wantOpenAI)
			}
			if got := priced(p, "azure/gpt-4o"); got != tc.wantAzure {
				t.Errorf("azure/gpt-4o priced = %v, want %v", got, tc.wantAzure)
			}
			if got := cost(p, "gpt-4o"); got != 0.01 {
				t.Errorf("gpt-4o cost = %v, want the fetched 0.01", got)
			}
		})
	}
}

func TestSync_RejectedFetchKeepsTableInUse(t *testing.T) {
	cases := []struct {
		name string
		body string
		code int
	}{
		{"not json", "<html>nope</html>", http.StatusOK},
		{"fewer than 50 entries", tableJSON(49, 1e-6), http.StatusOK},
		{"under half of the starting table", tableJSON(60, 1e-6), http.StatusOK},
		{"http error", tableJSON(200, 1e-6), http.StatusInternalServerError},
		{"wrong schema: objects with no price fields", wrongSchema(500), http.StatusOK},
		{"wrong schema: negative prices", negativePrices(500), http.StatusOK},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeCatalogue(t, tableJSON(150, 2e-6))
			s, p := newTestSyncer(t, f.URL, 150)
			s.syncOnce(context.Background(), time.Second)
			if !priced(p, "fetched-149") {
				t.Fatal("precondition: first sync did not load")
			}
			before, beforeFetched := cost(p, "gpt-4o"), cost(p, "fetched-149")

			f.body.Store(tc.body)
			f.code.Store(int32(tc.code))
			s.syncOnce(context.Background(), time.Second)

			if got := cost(p, "fetched-149"); got != beforeFetched {
				t.Errorf("fetched-149 cost changed to %v from %v: the last good table was dropped", got, beforeFetched)
			}
			if cost(p, "gpt-4o") != before || before == 0 {
				t.Errorf("gpt-4o cost changed to %v from %v", cost(p, "gpt-4o"), before)
			}
		})
	}
}

// 500 existing model keys with objects that state no price: enough entries to
// pass a count, none usable. The good table must stay and gpt-4o keep its price.
func TestSync_WrongSchemaDoesNotZeroPrices(t *testing.T) {
	body := wrongSchema(500)
	body = withEntry(body, "gpt-4o", `{"description":"wrong schema: no price fields"}`)
	f := newFakeCatalogue(t, body)
	s, p := newTestSyncer(t, f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if got := cost(p, "gpt-4o"); got != 0.0025 {
		t.Errorf("gpt-4o cost = %v, want 0.0025", got)
	}
}

// Every key of the embedded table served as an object with no price fields:
// as many entries as the good table, none usable. It must be rejected and
// gpt-4o keep its embedded price.
func TestSync_WrongSchemaForEveryEmbeddedKeyKeepsTable(t *testing.T) {
	base, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	wrong := make(map[string]json.RawMessage, base.Len())
	for key := range base.prices {
		wrong[key] = json.RawMessage(`{"description":"wrong schema: no price fields"}`)
	}
	body, err := json.Marshal(wrong)
	if err != nil {
		t.Fatal(err)
	}
	f := newFakeCatalogue(t, string(body))
	p := New(base, nil)
	before, ok := p.TokenCost(context.Background(), "", "gpt-4o", 1000, 1000)
	if !ok || math.Abs(before-0.0125) > 1e-12 {
		t.Fatalf("precondition: gpt-4o = (%v, %v), want (0.0125, true)", before, ok)
	}

	NewSyncer(p, base, f.URL, time.Hour, slog.Default()).syncOnce(context.Background(), 5*time.Second)

	if p.table.Load() != base {
		t.Error("the wrong-schema download replaced the table in use")
	}
	if c, ok := p.TokenCost(context.Background(), "", "gpt-4o", 1000, 1000); !ok || c != before {
		t.Errorf("gpt-4o = (%v, %v), want (%v, true)", c, ok, before)
	}
}

// The embedded table, served as a download, clears the floors it is checked
// against: the default catalogue URL serves the same file.
func TestSync_EmbeddedTableIsAccepted(t *testing.T) {
	base, err := LoadTable("")
	if err != nil {
		t.Fatal(err)
	}
	f := newFakeCatalogue(t, string(embeddedPrices))
	p := New(base, nil)

	NewSyncer(p, base, f.URL, time.Hour, slog.Default()).syncOnce(context.Background(), 5*time.Second)

	if got := p.table.Load(); got == base || got.Len() != base.Len() {
		t.Errorf("table = %d entries (replaced: %v), want the %d embedded entries loaded", got.Len(), got != base, base.Len())
	}
}

// A partly wrong download adds its usable entries; the unpriced objects are
// dropped and cannot overwrite a priced starting model.
func TestSync_PartiallyMalformedKeepsUsableEntries(t *testing.T) {
	body := tableJSON(60, 1e-6)
	body = withEntry(body, "gpt-4o", `{"description":"no price fields"}`)
	body = withEntry(body, "bad-type", `{"input_cost_per_token":"cheap"}`)
	f := newFakeCatalogue(t, body)
	s, p := newTestSyncer(t, f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if !priced(p, "fetched-7") {
		t.Error("usable fetched entries were not loaded")
	}
	if got := cost(p, "gpt-4o"); got != 0.0025 {
		t.Errorf("gpt-4o cost = %v, want the starting 0.0025", got)
	}
	if priced(p, "bad-type") {
		t.Error("an entry with a non-numeric price was loaded")
	}
}

// One bad supplied rate drops the whole entry, whichever rate it is: the
// starting price of gpt-4o stays and the entry does not count as usable.
func TestSync_NegativeRateDropsTheEntry(t *testing.T) {
	cases := map[string]string{
		"negative input, non-negative output": `{"mode":"chat","input_cost_per_token":-0.00001,"output_cost_per_token":0.000001}`,
		"non-negative input, negative output": `{"mode":"chat","input_cost_per_token":0.000001,"output_cost_per_token":-0.00001}`,
		"negative long-context input tier":    `{"input_cost_per_token":1e-6,"output_cost_per_token":1e-6,"input_cost_per_token_above_128k_tokens":-0.00001}`,
		"negative long-context output tier":   `{"input_cost_per_token":1e-6,"output_cost_per_token":1e-6,"output_cost_per_token_above_128k_tokens":-0.00001}`,
	}
	for name, entry := range cases {
		t.Run(name, func(t *testing.T) {
			f := newFakeCatalogue(t, withEntry(tableJSON(60, 1e-6), "gpt-4o", entry))
			s, p := newTestSyncer(t, f.URL, 100)

			s.syncOnce(context.Background(), time.Second)

			if !priced(p, "fetched-7") {
				t.Fatal("the good entries were not loaded")
			}
			if got := cost(p, "gpt-4o"); got != 0.0025 {
				t.Errorf("gpt-4o cost = %v, want the starting 0.0025", got)
			}
			if c, _ := p.TokenCost(context.Background(), "", "gpt-4o", 129000, 129000); c != 129000*2*gpt4oPrice {
				t.Errorf("gpt-4o long-context cost = %v, want %v", c, 129000*2*gpt4oPrice)
			}
		})
	}
}

// A download whose every entry has a bad rate counts no usable entry, so it is
// rejected and the table in use stays.
func TestSync_AllNegativeCatalogueRejected(t *testing.T) {
	f := newFakeCatalogue(t, tableJSON(150, 2e-6))
	s, p := newTestSyncer(t, f.URL, 150)
	s.syncOnce(context.Background(), time.Second)
	f.body.Store(mixedSign(500))

	s.syncOnce(context.Background(), time.Second)

	if got := cost(p, "fetched-7"); got != 0.004 {
		t.Errorf("fetched-7 cost = %v, want the last good 0.004", got)
	}
}

func mixedSign(n int) string {
	out := "{"
	for i := 0; i < n; i++ {
		if i > 0 {
			out += ","
		}
		out += fmt.Sprintf(`"fetched-%d":{"input_cost_per_token":-0.00001,"output_cost_per_token":0.000001}`, i)
	}
	return out + "}"
}

// An optional rate that is absent is fine; only a supplied bad one drops an entry.
func TestSync_AbsentOptionalRatesAccepted(t *testing.T) {
	f := newFakeCatalogue(t, withEntry(tableJSON(60, 1e-6), "input-only", `{"input_cost_per_token":1e-6}`))
	s, p := newTestSyncer(t, f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if c, ok := p.TokenCost(context.Background(), "", "input-only", 1000, 1000); !ok || c != 0.001 {
		t.Errorf("input-only = (%v, %v), want (0.001, true)", c, ok)
	}
}

func TestSync_ExplicitFreePricingAccepted(t *testing.T) {
	body := withEntry(tableJSON(60, 1e-6), "free-model",
		`{"litellm_provider":"openai","input_cost_per_token":0,"output_cost_per_token":0}`)
	f := newFakeCatalogue(t, body)
	s, p := newTestSyncer(t, f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if c, ok := p.TokenCost(context.Background(), "", "free-model", 1000, 1000); !ok || c != 0 {
		t.Errorf("free-model = (%v, %v), want (0, true)", c, ok)
	}
}

func TestSync_UnreachableKeepsStartingTable(t *testing.T) {
	f := newFakeCatalogue(t, "")
	f.Close()
	s, p := newTestSyncer(t, f.URL, 100)

	s.syncOnce(context.Background(), time.Second)

	if !priced(p, "gpt-4o") {
		t.Error("starting table lost after an unreachable fetch")
	}
}

func TestNewSyncer_EmptyURLTurnsSyncOff(t *testing.T) {
	f := newFakeCatalogue(t, tableJSON(60, 1e-6))
	base := startingTable(t, 100)
	if s := NewSyncer(New(base, nil), base, "", time.Hour, slog.Default()); s != nil {
		t.Fatal("an empty URL must give no syncer")
	}
	if f.hits.Load() != 0 {
		t.Errorf("fetched %d times with the sync off", f.hits.Load())
	}
}

func TestSync_RunFetchesAtStartup(t *testing.T) {
	f := newFakeCatalogue(t, tableJSON(60, 1e-6))
	s, p := newTestSyncer(t, f.URL, 100)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { s.Run(ctx); close(done) }()

	deadline := time.Now().Add(2 * time.Second)
	for !priced(p, "fetched-7") && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	<-done

	if !priced(p, "fetched-7") {
		t.Error("Run did not fetch at startup")
	}
}

// Run fetches again on every interval: a price changed after startup reaches
// the pricer without a restart.
func TestSync_RunPublishesAScheduledRefresh(t *testing.T) {
	f := newFakeCatalogue(t, tableJSON(60, 1e-6))
	s, p := newTestSyncer(t, f.URL, 100)
	s.interval = 20 * time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { s.Run(ctx); close(done) }()
	defer func() { cancel(); <-done }()

	waitFor := func(what string, want float64) {
		t.Helper()
		deadline := time.Now().Add(2 * time.Second)
		for math.Abs(cost(p, "fetched-7")-want) > 1e-12 {
			if time.Now().After(deadline) {
				t.Fatalf("timed out waiting for %s: fetched-7 cost = %v, want %v", what, cost(p, "fetched-7"), want)
			}
			time.Sleep(5 * time.Millisecond)
		}
	}
	waitFor("the startup fetch", 0.002)
	f.body.Store(tableJSON(60, 3e-6))
	waitFor("a scheduled refresh to publish the new price", 0.006)
	if hits := f.hits.Load(); hits < 2 {
		t.Errorf("catalogue fetched %d times, want a startup fetch and a scheduled one", hits)
	}
}

func TestNewSyncer_Interval(t *testing.T) {
	base := startingTable(t, 1)
	p := New(base, nil)
	for _, tc := range []struct{ in, want time.Duration }{
		{0, DefaultSyncInterval},
		{time.Minute, MinSyncInterval},
		{6 * time.Hour, 6 * time.Hour},
	} {
		if got := NewSyncer(p, base, "http://x", tc.in, slog.Default()).interval; got != tc.want {
			t.Errorf("interval(%v) = %v, want %v", tc.in, got, tc.want)
		}
	}
}

func wrongSchema(n int) string {
	out := "{"
	for i := 0; i < n; i++ {
		if i > 0 {
			out += ","
		}
		out += fmt.Sprintf(`"fetched-%d":{"description":"wrong schema: no price fields"}`, i)
	}
	return out + "}"
}

func negativePrices(n int) string {
	out := "{"
	for i := 0; i < n; i++ {
		if i > 0 {
			out += ","
		}
		out += fmt.Sprintf(`"fetched-%d":{"input_cost_per_token":-1,"output_cost_per_token":-1}`, i)
	}
	return out + "}"
}

// A catalogue URL can carry a password or a token in its query string; the
// logs must carry neither, on a good fetch or a failed one.
func TestSync_CredentialsNeverReachLogs(t *testing.T) {
	credentialURL := func(serverURL string) string {
		return "http://bob:hunter2@" + strings.TrimPrefix(serverURL, "http://") + "/catalogue.json?token=s3cret"
	}
	cases := []struct {
		name   string
		closed bool
	}{
		{"good fetch", false},
		{"unreachable fetch", true}, // the client error carries the URL
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeCatalogue(t, tableJSON(60, 1e-6))
			if tc.closed {
				f.Close()
			}
			var logs bytes.Buffer
			base := startingTable(t, 100)
			s := NewSyncer(New(base, nil), base, credentialURL(f.URL), time.Hour, slog.New(slog.NewTextHandler(&logs, nil)))

			s.syncOnce(context.Background(), time.Second)

			if logs.Len() == 0 {
				t.Fatal("precondition: the sync logged nothing")
			}
			for _, secret := range []string{"hunter2", "s3cret"} {
				if strings.Contains(logs.String(), secret) {
					t.Errorf("log carries the credential %q: %s", secret, logs.String())
				}
			}
		})
	}
}

// "At least half" of an odd count rounds up: 61 of 121 usable, not 60.
func TestSync_HalfOfStartingRoundsUp(t *testing.T) {
	f := newFakeCatalogue(t, tableJSON(60, 1e-6))
	s, p := newTestSyncer(t, f.URL, 120) // gpt-4o + 120 base = 121 usable

	s.syncOnce(context.Background(), time.Second)

	if priced(p, "fetched-7") {
		t.Error("a fetch of 60 against 121 starting entries was accepted")
	}
}
