package ingestbench

import (
	"context"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"
	"unsafe"

	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
)

func finishMeasurementRequest(s *runState, seq int64, n int, end time.Time) {
	s.mu.Lock()
	s.counters.Attempted += int64(n)
	s.counters.Requests++
	s.inFlightSpans += int64(n)
	s.inFlightRequests++
	s.mu.Unlock()
	s.finish(seq, n, 0, 0, exportOutcome{class: outcomeAcknowledged}, 0, 0, end)
}

// Exercise the actual trace and session SQL probe paths, including bound IDs.
// The transport is in-process: this proves dispatch/selection, NOT real storage
// visibility or query latency, and opens no listener or database connection.
func requireCandidateIDProbes(t *testing.T, c Config, start time.Time, cand candidate) {
	t.Helper()
	if !cand.ok {
		t.Fatal("no eligible during-load candidate")
	}
	calls := map[string]int{}
	client := &http.Client{Transport: probeRoundTripFunc(func(r *http.Request) (*http.Response, error) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatal(err)
		}
		q := r.URL.Query()
		if q.Get("param_project") != c.ProjectID {
			t.Fatal("wrong project binding")
		}
		switch {
		case strings.Contains(string(body), "trace_id = {trace:String}"):
			calls["trace-by-id"]++
			if q.Get("param_trace") != c.traceString(cand.seq) {
				t.Fatal("wrong trace binding")
			}
		case strings.Contains(string(body), "trace_session_id = {session:UUID}"):
			calls["session-by-id"]++
			session := cand.seq / (c.SpansPerTrace * c.TracesPerSession)
			want := detid.TraceSessionID(c.ProjectID, sessionName(c.RunID, session)).String()
			if q.Get("param_session") != want {
				t.Fatal("wrong session binding")
			}
		}
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(`{"rows":1,"payload_bytes":32}`))}, nil
	})}
	d := newDirectRunner(clickHouse{cfg: c, client: client}, start)
	d.round(context.Background(), "during-load", cand)
	for _, result := range d.results() {
		if result.Phase == "during-load" && result.Name != "attribute-filter" {
			if calls[result.Name] != 1 || result.Successes != 1 || result.NonEmpty != 1 || result.Skipped != 0 || result.Status != "measured" {
				t.Fatalf("ID probe did not execute: %+v calls=%v", result, calls)
			}
		}
	}
}

// R4: deterministic virtual completion times through finish, at both supplied
// request rates. No sleeps, full-scale load or dependence on wall-clock speed.
func TestProfileRateCandidateRetention(t *testing.T) {
	for _, tc := range []struct {
		name string
		rate int64
		age  time.Duration
	}{{"sustained", 15000, 30 * time.Second}, {"burst", 60000, 10 * time.Second}} {
		t.Run(tc.name, func(t *testing.T) {
			c := testConfig()
			c.Rate, c.BatchSize, c.DirectProbes.MinAge = tc.rate, 500, tc.age
			start := time.Now() // Add/Sub preserve the monotonic clock component.
			s := newRunState(c, start, false)
			requests := 40 * c.Rate / int64(c.BatchSize)
			for r := int64(0); r < requests; r++ {
				elapsed := time.Duration(r*int64(c.BatchSize)) * time.Second / time.Duration(c.Rate)
				at := start.Add(elapsed)
				finishMeasurementRequest(s, r*int64(c.BatchSize), c.BatchSize, at)
				got := s.candidateAt(tc.age, at)
				if got.ok != (elapsed >= tc.age) {
					t.Fatalf("candidate=%+v after %d requests at %s, min age %s", got, r+1, elapsed, tc.age)
				}
				if got.ok {
					ackAt := time.Duration(got.seq) * time.Second / time.Duration(c.Rate)
					if elapsed-ackAt < tc.age {
						t.Fatal("selected a future or too-young acknowledgement")
					}
				}
			}
			requireCandidateIDProbes(t, c, start, s.candidateAt(tc.age, start.Add(40*time.Second)))
			if err := s.counters.CheckIdentity(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestCandidateRetentionVariableRatesIdleResume(t *testing.T) {
	c := testConfig()
	c.BatchSize, c.DirectProbes.MinAge = 1, 10*time.Second
	start := time.Now()
	s := newRunState(c, start, false)
	var seq int64
	// Enough virtual time to wrap many times, including a long idle period
	// and a high-rate resume that must not displace all matured samples.
	for second := 0; second < 200; second++ {
		rate := []int{1, 30, 120, 0, 400, 3, 0, 120}[second/25]
		for r := 0; r < rate; r++ {
			at := start.Add(time.Duration(second)*time.Second + time.Duration(r)*time.Second/time.Duration(rate))
			finishMeasurementRequest(s, seq, 1, at)
			seq++
		}
		at := start.Add(time.Duration(second+1) * time.Second)
		cand := s.candidateAt(c.DirectProbes.MinAge, at)
		if second+1 >= 10 && !cand.ok {
			t.Fatalf("lost eligibility during rate=%d at %ds", rate, second+1)
		}
		if (second+1)%25 == 0 {
			requireCandidateIDProbes(t, c, start, cand)
		}
	}
}

func TestCandidateAgeBoundariesAndOutOfOrderFinishes(t *testing.T) {
	age := 30 * time.Second
	h := newAckCandidates(age)
	h.add(1, 0)
	h.add(2, time.Nanosecond) // Must not refresh the first sample's age.
	if h.selectAt(age, age-time.Nanosecond).ok {
		t.Fatal("selected before minimum age")
	}
	if got := h.selectAt(age, age); !got.ok || got.seq != 1 {
		t.Fatalf("first acknowledgement not eligible exactly at min age: %+v", got)
	}
	later := h.width * ackCandidateBuckets
	h.add(3, later) // Wrap the ring, saving a matured fallback.
	h.add(4, later+time.Nanosecond)
	h.add(5, 2*time.Nanosecond) // A delayed finish must not replace a newer slot.
	if got := h.selectAt(age, later); !got.ok || got.seq == 3 || got.seq == 4 {
		t.Fatalf("idle/resume lost the eligible sample: %+v", got)
	}
	if got := h.selectAt(age, later+age); !got.ok || got.seq != 3 {
		t.Fatalf("new generation did not mature: %+v", got)
	}
	future := newAckCandidates(0)
	future.add(6, time.Second)
	if future.selectAt(0, time.Second-time.Nanosecond).ok || future.selectAt(0, -time.Nanosecond).ok {
		t.Fatal("selected a future acknowledgement")
	}
	if got := future.selectAt(0, time.Second); !got.ok || got.seq != 6 {
		t.Fatal("zero-age sample not immediately eligible")
	}
}

func TestCandidateRetentionMemoryIsRateIndependent(t *testing.T) {
	for _, age := range []time.Duration{0, time.Nanosecond, 10 * time.Second, 30 * time.Second, time.Hour} {
		h := newAckCandidates(age)
		if unsafe.Sizeof(h) > 2048 {
			t.Fatalf("candidate state grew beyond 2KiB: %d", unsafe.Sizeof(h))
		}
		if allocs := testing.AllocsPerRun(10, func() {
			for i := int64(0); i < 10000; i++ {
				h.add(i, time.Duration(i)*time.Microsecond)
			}
			_ = h.selectAt(age, age+time.Second)
		}); allocs != 0 {
			t.Fatalf("candidate updates allocate: %v", allocs)
		}
	}
}

func TestCandidateSelectionConcurrentWithFinish(t *testing.T) {
	c := testConfig()
	c.BatchSize = 1
	start := time.Now().Add(-time.Hour)
	s := newRunState(c, start, false)
	var wg sync.WaitGroup
	for worker := 0; worker < 4; worker++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			for i := int64(worker); i < 4000; i += 4 {
				finishMeasurementRequest(s, i, 1, start.Add(time.Duration(i)*time.Millisecond))
				_ = s.candidate(c.DirectProbes.MinAge)
			}
		}(worker)
	}
	wg.Wait()
	if !s.candidate(c.DirectProbes.MinAge).ok {
		t.Fatal("concurrent finishes lost all mature candidates")
	}
	if err := s.counters.CheckIdentity(); err != nil {
		t.Fatal(err)
	}
}
