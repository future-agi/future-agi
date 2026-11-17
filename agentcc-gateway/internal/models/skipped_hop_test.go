package models

import (
	"fmt"
	"testing"
)

// Skipped hops are counted without bound, kept in full up to
// MaxSkippedHops, and never take an attempt slot: a request that refused
// more candidates than MaxAttempts still records every attempt it made.
func TestSkippedHopsAreBoundedAndApartFromAttempts(t *testing.T) {
	rc := AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-skips"
	rc.AddAttempt(Attempt{Kind: AttemptPrimary, Provider: "openrouter", Model: "m", Outcome: OutcomeError})
	const refused = 3 * MaxAttempts
	for i := range refused {
		n := rc.AddSkippedHop(SkippedHop{Kind: AttemptModelFallback, Reason: ReasonPolicy, Provider: "operator",
			Model: fmt.Sprintf("own-%d", i), CredentialType: CredentialBYOK})
		if n != i+1 {
			t.Fatalf("AddSkippedHop returned %d after %d refusals", n, i+1)
		}
	}
	served := rc.AddAttempt(Attempt{Kind: AttemptModelFallback, Provider: "openrouter", Model: "m2", Outcome: OutcomeSuccess})
	if served.AttemptID == "" || rc.AttemptCount() != 2 {
		t.Fatalf("the serving attempt after %d skipped hops got id %q, %d attempts recorded; want it recorded as attempt 2",
			refused, served.AttemptID, rc.AttemptCount())
	}
	for _, a := range rc.AttemptsSnapshot() {
		if a.Outcome == OutcomeSkipped {
			t.Fatalf("attempts %+v hold a skipped hop", rc.AttemptsSnapshot())
		}
	}
	count, kept := rc.SkippedHops()
	if count != refused || len(kept) != MaxSkippedHops || rc.SkippedHopCount() != refused {
		t.Fatalf("SkippedHops = %d, %d kept (count %d); want %d counted and the first %d kept",
			count, len(kept), rc.SkippedHopCount(), refused, MaxSkippedHops)
	}
	for i, s := range kept {
		want := SkippedHop{Kind: AttemptModelFallback, Reason: ReasonPolicy, Provider: "operator",
			Model: fmt.Sprintf("own-%d", i), CredentialType: CredentialBYOK}
		if s != want {
			t.Fatalf("kept[%d] = %+v; want %+v (the first refusals, in order)", i, s, want)
		}
	}
	// The copy is the caller's.
	kept[0].Model = "changed"
	if _, again := rc.SkippedHops(); again[0].Model != "own-0" {
		t.Fatal("SkippedHops returned the request's own slice")
	}
}

func TestReleaseClearsSkippedHops(t *testing.T) {
	rc := AcquireRequestContext()
	rc.AddSkippedHop(SkippedHop{Kind: AttemptAutoFallback, Reason: ReasonPolicy, Provider: "openrouter", Model: "m", CredentialType: CredentialManaged})
	rc.AddSkippedHop(SkippedHop{Kind: AttemptFailover, Reason: ReasonPolicy, Provider: "operator", Model: "m", CredentialType: CredentialBYOK})
	rc.Release()
	if n, kept := rc.SkippedHops(); n != 0 || kept != nil {
		t.Fatalf("Release left %d skipped hops %+v", n, kept)
	}
	// The backing array is kept for the next request, zeroed.
	if all := rc.skippedHops[:cap(rc.skippedHops)]; len(all) > 0 && all[0] != (SkippedHop{}) {
		t.Fatalf("Release kept the previous request's skipped hop %+v", all[0])
	}
	if n, _ := rc.SkippedHops(); n != 0 {
		t.Fatal("the count outlived the request")
	}
}

func TestSkippedHopsEmpty(t *testing.T) {
	rc := AcquireRequestContext()
	defer rc.Release()
	if n, kept := rc.SkippedHops(); n != 0 || kept != nil || rc.SkippedHopCount() != 0 {
		t.Fatalf("a fresh request has %d skipped hops %+v", n, kept)
	}
}
