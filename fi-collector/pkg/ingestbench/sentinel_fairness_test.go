package ingestbench

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestSentinelPollingRotatesAfterQueryErrors(t *testing.T) {
	c := testConfig()
	s := newRunState(c, time.Now(), false)
	for seq := int64(0); seq <= maxPollIDsPerTick; seq++ {
		s.registerSentinel(seq, s.start, s.start, s.start)
		s.sentinelFinished(seq, true)
	}
	var fail atomic.Bool
	fail.Store(true)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		if fail.Load() {
			http.Error(w, "query failed", http.StatusInternalServerError)
			return
		}
		if strings.Contains(r.URL.Query().Get("param_ids"), "'"+spanID(maxPollIDsPerTick)+"'") {
			fmt.Fprintf(w, "{\"id\":%q}\n", spanID(maxPollIDsPerTick))
		}
	}))
	defer srv.Close()
	c.ClickHouseURL = srv.URL
	ch := clickHouse{cfg: c, client: c.readClient()}
	defer ch.client.CloseIdleConnections()
	s.pollOnce(context.Background(), ch)
	if s.lagErrors != maxPollIDsPerTick/pollChunk || len(s.pending) != maxPollIDsPerTick+1 {
		t.Fatalf("errors lost pending identities: errors=%d pending=%d", s.lagErrors, len(s.pending))
	}
	for _, st := range s.pending {
		if !st.lastMiss.IsZero() {
			t.Fatal("failed query fabricated a last-miss bound")
		}
	}
	fail.Store(false)
	s.pollOnce(context.Background(), ch)
	if s.sentinels[maxPollIDsPerTick].seenAt.IsZero() {
		t.Fatal("query errors prevented rotation to the visible tail")
	}
	if !s.pending[maxPollIDsPerTick-1].lastMiss.IsZero() {
		t.Fatal("an unpolled sentinel got a last-miss timestamp")
	}
	s.pollOnce(context.Background(), ch)
	if s.pending[maxPollIDsPerTick-1].lastMiss.IsZero() {
		t.Fatal("rotation did not revisit earlier failed queries")
	}
}

func TestSentinelPollingDoesNotStarveLaterIdentities(t *testing.T) {
	c := testConfig()
	s := newRunState(c, time.Now(), false)
	// More than two ticks of permanently missing sentinels, plus a visible tail.
	const total = 2*maxPollIDsPerTick + 1
	for seq := int64(0); seq < total; seq++ {
		s.registerSentinel(seq, s.start, s.start, s.start)
		s.sentinelFinished(seq, true)
	}
	var calls atomic.Int64
	chServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		calls.Add(1)
		ids := r.URL.Query().Get("param_ids")
		if strings.Contains(ids, "'"+spanID(total-1)+"'") {
			fmt.Fprintf(w, "{\"id\":%q}\n", spanID(total-1))
		}
	}))
	defer chServer.Close()
	c.ClickHouseURL = chServer.URL
	ch := clickHouse{cfg: c, client: c.readClient()}
	defer ch.client.CloseIdleConnections()
	for tick := 0; tick < 3; tick++ {
		before := calls.Load()
		s.pollOnce(context.Background(), ch)
		if calls.Load()-before > maxPollIDsPerTick/pollChunk {
			t.Fatalf("tick exceeded query bound: %d", calls.Load()-before)
		}
	}
	if s.sentinels[total-1].seenAt.IsZero() || s.seen != 1 {
		t.Fatalf("visible tail starved after three ticks: seen=%d calls=%d", s.seen, calls.Load())
	}
	for seq := int64(0); seq < total-1; seq++ {
		st := s.pending[seq]
		if st == nil || st.lastMiss.IsZero() || !st.seenAt.IsZero() {
			t.Fatalf("missing sentinel %d lost its poll bounds or unseen status", seq)
		}
	}
	before := s.pending[0].lastMiss
	for tick := 0; tick < 2; tick++ {
		s.pollOnce(context.Background(), ch)
	}
	if !s.pending[0].lastMiss.After(before) {
		t.Fatal("poll rotation did not return to earlier missing sentinels")
	}
}
