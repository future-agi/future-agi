package redisstate

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// Cancelling a provisional managed attempt that holds 0 micros removes the
// attempt and leaves every hold where it was. The cancel must not send Redis a
// "-0" increment, which Redis before 7.4 refuses; the script would abort and
// the attempt would never be cancelled.
func TestCancelProvisionalZeroMicrosAttempt(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	const org, req = "org-owned", "zreq"
	mustGrant(t, s, org, 10000, 1, 0)
	if r, e := s.ReserveInclusive(bg, org, req, req+".1", "google/gemini-3.5-flash", 500, time.Hour, billing.ReserveNew); e != nil || r.Status != billing.ReserveOK {
		t.Fatalf("first attempt: %+v %v", r, e)
	}
	if r, e := s.ReserveInclusive(bg, org, req, req+".2", "google/gemini-3.5-flash", 0, time.Hour, billing.ReserveAdd); e != nil || r.Status != billing.ReserveOK {
		t.Fatalf("zero-micros attempt: %+v %v", r, e)
	}
	k := s.ownedKeys(org, req)

	s.cancelProvisional(bg, org, req, req+".2")

	var o billing.OwnedRequest
	if err := json.Unmarshal([]byte(rdb.Get(bg, k[5]).Val()), &o); err != nil {
		t.Fatal(err)
	}
	if _, ok := o.Attempts[req+".2"]; ok {
		t.Fatal("the zero-micros attempt was not cancelled")
	}
	if _, ok := o.Attempts[req+".1"]; !ok {
		t.Fatal("the cancel removed the other attempt")
	}
	if got := rdb.HGet(bg, k[1], req).Val(); got != "500" {
		t.Fatalf("the request's hold = %q, want 500 unchanged", got)
	}
	if got := rdb.HGet(bg, k[0], "held").Val(); got != "500" {
		t.Fatalf("the account's held = %q, want 500 unchanged", got)
	}
}
