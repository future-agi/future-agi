package models

// MIXED-HOP-GUARD-1 round 3, the parts that need its new APIs: an attempt in
// flight is never dropped nor renumbered, so its finish finds it by its id;
// and CanRecordAttempt says whether one more attempt that is sent would be
// recorded, which the gateway asks before it holds or sends one.

import (
	"fmt"
	"testing"
)

func TestCanRecordAttempt(t *testing.T) {
	fill := func(rc *RequestContext, n int, charged bool) {
		for i := range n {
			rc.AddAttempt(failedAttempt(fmt.Sprintf("f-%d", i), charged))
		}
	}
	cases := []struct {
		name  string
		build func(rc *RequestContext)
		want  bool
	}{
		{"empty", func(*RequestContext) {}, true},
		{"one free slot", func(rc *RequestContext) { fill(rc, MaxAttempts-1, true) }, true},
		{"full of chargeless failures", func(rc *RequestContext) { fill(rc, MaxAttempts, false) }, true},
		{"full of charged failures", func(rc *RequestContext) { fill(rc, MaxAttempts, true) }, false},
		{"full, one skipped entry", func(rc *RequestContext) {
			fill(rc, MaxAttempts-1, true)
			rc.AddAttempt(Attempt{Outcome: OutcomeSkipped})
		}, true},
		{"full, the only chargeless failure before one in flight", func(rc *RequestContext) {
			rc.AddAttempt(failedAttempt("chargeless", false))
			fill(rc, MaxAttempts-2, true)
			rc.AddAttempt(Attempt{Outcome: OutcomeError, InFlight: true})
		}, false},
		{"full, the last in flight", func(rc *RequestContext) {
			fill(rc, MaxAttempts-1, false)
			rc.AddAttempt(Attempt{Outcome: OutcomeError, InFlight: true})
		}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			rc := AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "req-can"
			c.build(rc)
			if got := rc.CanRecordAttempt(); got != c.want {
				t.Fatalf("CanRecordAttempt() = %v; want %v", got, c.want)
			}
			// AddAttempt agrees with it, and an attempt it refuses changes nothing.
			before := rc.AttemptsSnapshot()
			a := rc.AddAttempt(failedAttempt("new", false))
			if (a.AttemptID != "") != c.want {
				t.Fatalf("AddAttempt recorded %v; CanRecordAttempt said %v", a.AttemptID != "", c.want)
			}
			if !c.want && len(rc.AttemptsSnapshot()) != len(before) {
				t.Fatal("a refused attempt changed the record")
			}
		})
	}
}

// A slot is reclaimed only after the last attempt in flight: the one in
// flight keeps its index and id, and its finish still updates it.
func TestAttemptInFlightIsNeverRenumbered(t *testing.T) {
	rc := AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-flight"
	var inFlight Attempt
	for i := range MaxAttempts {
		a := failedAttempt(fmt.Sprintf("f-%d", i), false)
		if i == 7 {
			a.InFlight = true
		}
		got := rc.AddAttempt(a)
		if i == 7 {
			inFlight = got
		}
	}
	rc.AddAttempt(failedAttempt("new", false))
	snap := assertContiguous(t, rc)
	if snap[7].Model != "f-7" || snap[7].AttemptID != inFlight.AttemptID {
		t.Fatalf("attempt 8 is %s %q; want the attempt in flight, f-7, at its id %q", snap[7].Model, snap[7].AttemptID, inFlight.AttemptID)
	}
	if hasModel(snap, "f-8") || !hasModel(snap, "f-0") || snap[len(snap)-1].Model != "new" {
		t.Fatalf("record %v; want f-8, the oldest chargeless failure after the one in flight, dropped", attemptModels(snap))
	}
	if !rc.UpdateAttempt(inFlight.AttemptID, func(a *Attempt) { a.InFlight, a.Outcome = false, OutcomeSuccess }) {
		t.Fatal("the attempt in flight could not be finished by its id")
	}
	if got := rc.AttemptsSnapshot()[7]; got.Model != "f-7" || got.Outcome != OutcomeSuccess {
		t.Fatalf("finishing the attempt in flight updated %+v", got)
	}
}
