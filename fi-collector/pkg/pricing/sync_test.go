package pricing

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

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

func startingPricer(t *testing.T) *Pricer {
	t.Helper()
	tbl, err := LoadTable("testdata/prices_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	return New(tbl, nil)
}

func priced(p *Pricer, model string) bool {
	_, ok := p.TokenCost(context.Background(), "", model, 10, 10)
	return ok
}

func TestSync_GoodTableReplacesStarting(t *testing.T) {
	f := newFakeCatalogue(t, tableJSON(60, 1e-6))
	p := startingPricer(t)
	s := NewSyncer(p, 100, f.URL, time.Hour, slog.Default())

	s.syncOnce(context.Background(), time.Second)

	if !priced(p, "fetched-7") {
		t.Error("fetched model not priced after a good sync")
	}
	if priced(p, "gpt-4o") {
		t.Error("starting table survived; the fetched table should replace it")
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
		{"under half of baseline", tableJSON(60, 1e-6), http.StatusOK},
		{"http error", tableJSON(200, 1e-6), http.StatusInternalServerError},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeCatalogue(t, tableJSON(150, 1e-6))
			p := startingPricer(t)
			s := NewSyncer(p, 150, f.URL, time.Hour, slog.Default())
			s.syncOnce(context.Background(), time.Second)
			if !priced(p, "fetched-149") {
				t.Fatal("precondition: first sync did not load")
			}

			f.body.Store(tc.body)
			f.code.Store(int32(tc.code))
			s.syncOnce(context.Background(), time.Second)

			if !priced(p, "fetched-149") {
				t.Error("last good table was dropped")
			}
		})
	}
}

func TestSync_UnreachableKeepsStartingTable(t *testing.T) {
	f := newFakeCatalogue(t, "")
	f.Close()
	p := startingPricer(t)
	s := NewSyncer(p, 100, f.URL, time.Hour, slog.Default())

	s.syncOnce(context.Background(), time.Second)

	if !priced(p, "gpt-4o") {
		t.Error("starting table lost after an unreachable fetch")
	}
}

func TestNewSyncer_EmptyURLTurnsSyncOff(t *testing.T) {
	f := newFakeCatalogue(t, tableJSON(60, 1e-6))
	if s := NewSyncer(startingPricer(t), 100, "", time.Hour, slog.Default()); s != nil {
		t.Fatal("an empty URL must give no syncer")
	}
	if f.hits.Load() != 0 {
		t.Errorf("fetched %d times with the sync off", f.hits.Load())
	}
}

func TestSync_RunFetchesAtStartup(t *testing.T) {
	f := newFakeCatalogue(t, tableJSON(60, 1e-6))
	p := startingPricer(t)
	s := NewSyncer(p, 100, f.URL, time.Hour, slog.Default())
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

func TestNewSyncer_Interval(t *testing.T) {
	p := startingPricer(t)
	for _, tc := range []struct{ in, want time.Duration }{
		{0, DefaultSyncInterval},
		{time.Minute, MinSyncInterval},
		{6 * time.Hour, 6 * time.Hour},
	} {
		if got := NewSyncer(p, 1, "http://x", tc.in, slog.Default()).interval; got != tc.want {
			t.Errorf("interval(%v) = %v, want %v", tc.in, got, tc.want)
		}
	}
}
