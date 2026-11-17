package models

// MIXED-HOP-GUARD-1 round 3: a request's attempt record keeps the attempt
// that served it and every attempt that may be charged, however many it
// sent. This file uses only APIs that exist at 4e38b7961b (the round-2 head),
// where its reds fail: past MaxAttempts AddAttempt dropped every new
// attempt, the one that served included. The reds are
// TestAttemptRecordKeepsTheServingAttempt (past MaxAttempts-1 failures) and
// TestAttemptRecordKeepsChargedFailures/half_charged; the all-charged row
// and TestAttemptRecordNeverReclaimsAKeptSlot are controls that hold there
// too.

import (
	"fmt"
	"strconv"
	"testing"
)

// failedAttempt is an attempt that was sent and failed. charged gives it the
// usage an upstream reported before it failed, which the cost plugin bills.
func failedAttempt(model string, charged bool) Attempt {
	a := Attempt{Kind: AttemptModelFallback, Provider: "openrouter", Model: model, CredentialType: CredentialManaged,
		Outcome: OutcomeError, ErrorClass: ErrClassUpstream5xx, ErrorOrigin: OriginUpstream}
	if charged {
		cost := 0.0001
		a.PromptTokens = 10
		a.Usage = &Usage{PromptTokens: 10, Cost: &cost}
	}
	return a
}

// assertContiguous fails unless rc's record is at most MaxAttempts attempts
// indexed 1 to n, each with the AttemptID "<request_id>.<index>", the shape
// log-entry-v2 holds attempts[] to.
func assertContiguous(t *testing.T, rc *RequestContext) []Attempt {
	t.Helper()
	snap := rc.AttemptsSnapshot()
	if len(snap) > MaxAttempts || rc.AttemptCount() != len(snap) {
		t.Fatalf("recorded %d attempts (count %d); want at most %d", len(snap), rc.AttemptCount(), MaxAttempts)
	}
	for i, a := range snap {
		if a.Index != i+1 || a.AttemptID != rc.RequestID+"."+strconv.Itoa(i+1) {
			t.Fatalf("attempt %d has index %d and id %q; want %d and %q", i, a.Index, a.AttemptID, i+1, rc.RequestID+"."+strconv.Itoa(i+1))
		}
	}
	return snap
}

// An org model chain of failed calls then the model that serves: whatever
// the number of failures, the serving attempt is recorded as the record's
// last, the oldest failures (which carry no charge) give up their slots, and
// the record stays MaxAttempts long, indexed 1 to MaxAttempts.
func TestAttemptRecordKeepsTheServingAttempt(t *testing.T) {
	for _, failed := range []int{MaxAttempts - 1, MaxAttempts, MaxAttempts + 4, 3 * MaxAttempts} {
		t.Run(fmt.Sprintf("failed=%d", failed), func(t *testing.T) {
			rc := AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "req-serve"
			for i := range failed {
				rc.AddAttempt(failedAttempt(fmt.Sprintf("failed-%d", i), false))
			}
			served := rc.AddAttempt(Attempt{Kind: AttemptModelFallback, Provider: "openrouter", Model: "serving",
				CredentialType: CredentialManaged, Outcome: OutcomeSuccess})
			want := min(failed+1, MaxAttempts)
			if served.Index != want || served.AttemptID != AttemptID("req-serve", want) {
				t.Fatalf("the serving attempt after %d failures got index %d id %q; want %d", failed, served.Index, served.AttemptID, want)
			}
			snap := assertContiguous(t, rc)
			if len(snap) != want || snap[len(snap)-1].Model != "serving" || snap[len(snap)-1].Outcome != OutcomeSuccess {
				t.Fatalf("record %d long, last %+v; want %d with the serving attempt last", len(snap), snap[len(snap)-1], want)
			}
			// The failures kept are the most recent, in the order sent.
			for i := 0; i < len(snap)-1; i++ {
				if wantModel := fmt.Sprintf("failed-%d", failed-(len(snap)-1)+i); snap[i].Model != wantModel {
					t.Fatalf("attempt %d is %s; want %s, the most recent failures in order", i+1, snap[i].Model, wantModel)
				}
			}
			if !rc.UpdateAttempt(served.AttemptID, func(a *Attempt) { a.Billable = true }) || !rc.AttemptsSnapshot()[want-1].Billable {
				t.Fatal("the serving attempt could not be updated by its id")
			}
		})
	}
}

// Failures that carry a charge (usage the upstream reported before it
// failed) keep their slots: only failures that carry none, and skipped
// entries, give theirs to a new attempt that was sent. With every slot
// holding a charged failure, no new attempt is recorded and none is dropped
// (the gateway then never sends one: CanRecordAttempt).
func TestAttemptRecordKeepsChargedFailures(t *testing.T) {
	t.Run("half charged", func(t *testing.T) {
		// Twenty failures, the odd ones charged, then the serving attempt:
		// 21 sent. The five oldest chargeless failures give up their slots;
		// all ten charged failures and the serving attempt are kept, in the
		// order they were sent.
		rc := AcquireRequestContext()
		defer rc.Release()
		rc.RequestID = "req-half"
		for i := range 20 {
			rc.AddAttempt(failedAttempt(fmt.Sprintf("f-%d", i), i%2 == 1))
		}
		rc.AddAttempt(Attempt{Kind: AttemptModelFallback, Provider: "openrouter", Model: "serving", Outcome: OutcomeSuccess})
		snap := assertContiguous(t, rc)
		want := []string{"f-1", "f-3", "f-5", "f-7", "f-9"}
		for i := 10; i < 20; i++ {
			want = append(want, fmt.Sprintf("f-%d", i))
		}
		want = append(want, "serving")
		if len(snap) != len(want) {
			t.Fatalf("recorded %d attempts; want %d", len(snap), len(want))
		}
		for i, a := range snap {
			if a.Model != want[i] {
				t.Fatalf("attempt %d is %s; want %s (record %v)", i+1, a.Model, want[i], attemptModels(snap))
			}
		}
	})

	t.Run("all charged", func(t *testing.T) {
		rc := AcquireRequestContext()
		defer rc.Release()
		rc.RequestID = "req-all"
		for i := range MaxAttempts {
			if a := rc.AddAttempt(failedAttempt(fmt.Sprintf("c-%d", i), true)); a.Index != i+1 {
				t.Fatalf("charged failure %d got index %d", i, a.Index)
			}
		}
		over := rc.AddAttempt(Attempt{Kind: AttemptModelFallback, Provider: "openrouter", Model: "serving", Outcome: OutcomeSuccess})
		if over.Index != 0 || over.AttemptID != "" {
			t.Fatalf("an attempt past a record of charged failures got index %d id %q; want none", over.Index, over.AttemptID)
		}
		snap := assertContiguous(t, rc)
		for i, a := range snap {
			if a.Model != fmt.Sprintf("c-%d", i) || a.Usage == nil {
				t.Fatalf("attempt %d is %+v; want charged failure c-%d kept", i+1, a, i)
			}
		}
	})
}

// A success, an attempt whose outcome is unknown or that was discarded, and
// any attempt with a token count or cost never give up their slot; a new
// skipped entry never takes one from an attempt that was sent.
func TestAttemptRecordNeverReclaimsAKeptSlot(t *testing.T) {
	keep := []Attempt{
		{Outcome: OutcomeSuccess},
		{Outcome: ""},
		{Outcome: OutcomeDiscarded},
		{Outcome: OutcomeError, CompletionTokens: 3},
		{Outcome: OutcomeTimeout, CachedTokens: 3},
		{Outcome: OutcomeCancelled, BilledCostMicros: 1},
		{Outcome: OutcomeError, UpstreamCostMicros: 1},
		{Outcome: OutcomeError, Billable: true},
		{Outcome: OutcomeCircuitOpen, Usage: &Usage{}},
	}
	for i, k := range keep {
		t.Run(fmt.Sprintf("%d/%s", i, k.Outcome), func(t *testing.T) {
			rc := AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "req-keep"
			for range MaxAttempts {
				rc.AddAttempt(k)
			}
			if a := rc.AddAttempt(failedAttempt("new", false)); a.Index != 0 {
				t.Fatalf("a new attempt took the slot of %+v", k)
			}
			for _, a := range rc.AttemptsSnapshot() {
				if a.Model == "new" {
					t.Fatalf("a new attempt took the slot of %+v", k)
				}
			}
		})
	}
	rc := AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-skip"
	for i := range MaxAttempts {
		rc.AddAttempt(failedAttempt(fmt.Sprintf("f-%d", i), false))
	}
	if a := rc.AddAttempt(Attempt{Outcome: OutcomeSkipped, Model: "skip"}); a.Index != 0 || hasModel(rc.AttemptsSnapshot(), "skip") {
		t.Fatal("a skipped entry took a sent attempt's slot")
	}
}

func attemptModels(attempts []Attempt) []string {
	out := make([]string, len(attempts))
	for i, a := range attempts {
		out[i] = a.Model
	}
	return out
}

func hasModel(attempts []Attempt, model string) bool {
	for _, a := range attempts {
		if a.Model == model {
			return true
		}
	}
	return false
}
