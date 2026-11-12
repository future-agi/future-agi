package redisstate

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/redis/go-redis/v9"
)

type ownedR4SealFault struct {
	key  string
	fail atomic.Bool
}

func (h *ownedR4SealFault) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *ownedR4SealFault) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *ownedR4SealFault) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if cmd.Name() == "hset" && len(cmd.Args()) > 1 && fmt.Sprint(cmd.Args()[1]) == h.key && h.fail.Load() {
			return errors.New("synthetic catalogue seal unavailable")
		}
		return next(ctx, cmd)
	}
}

func TestOwnedR4PriorAnomalySealFailureClosesReserveAndBegin(t *testing.T) {
	for _, begin := range []bool{false, true} {
		t.Run(fmt.Sprintf("begin=%t", begin), func(t *testing.T) {
			s, rdb, _ := managedTestStore(t)
			mustGrant(t, s, "org-owned", 10000, 1, 0)
			req, model := "seal-before-hop", "google/gemini-3.5-flash"
			if _, err := s.ReserveInclusive(bg, "org-owned", req, req+".1", model, 100, time.Hour, billing.ReserveNew); err != nil {
				t.Fatal(err)
			}
			if err := s.BeginInclusive(bg, "org-owned", req, req+".1"); err != nil {
				t.Fatal(err)
			}
			if begin {
				if _, err := s.ReserveOwned(bg, "org-owned", req, req+".2", "synthetic/token", 200, time.Hour, billing.ReserveAdd, true, billing.Settlement{}); err != nil {
					t.Fatal(err)
				}
			}
			rec := ownedRecord(req, 1050)
			summary := billing.AttemptSummary{AttemptID: req + ".1", Model: model, Provider: "openrouter", CredentialType: "managed", BilledMicros: 1050, UpstreamMicros: 1000, Outcome: "error"}
			rec.ManagedJournal = map[string]billing.OwnedAttempt{req + ".1": {Model: model, Micros: 100, Dispatched: true, Summary: &summary, Evidence: &rec.Dimensional.InclusiveAttempts[0]}}
			fault := &ownedR4SealFault{key: s.catalogueFenceKey()}
			fault.fail.Store(true)
			rdb.AddHook(fault)
			var err error
			if begin {
				err = s.BeginInclusiveWithRecord(bg, "org-owned", req, req+".2", rec)
			} else {
				_, err = s.ReserveOwned(bg, "org-owned", req, req+".2", "synthetic/token", 200, time.Hour, billing.ReserveAdd, true, rec)
			}
			if err == nil {
				t.Error("next hop admitted despite unavailable global anomaly seal")
			}
			raw, e := rdb.Get(bg, s.ownedKeys("org-owned", req)[5]).Bytes()
			if e != nil {
				t.Fatal(e)
			}
			var o billing.OwnedRequest
			if e = json.Unmarshal(raw, &o); e != nil {
				t.Fatal(e)
			}
			if a := o.Attempts[req+".1"]; a.Summary == nil || a.Summary.BilledMicros != 1050 {
				t.Errorf("known prior charge not durable on seal failure: %+v", a)
			}
			if a, ok := o.Attempts[req+".2"]; (ok && !begin) || a.Dispatched {
				t.Errorf("next hop advanced on seal failure: %+v", a)
			}
			rows, _ := rdb.XRange(bg, s.keys("org-owned").outbox, "-", "+").Result()
			if len(rows) != 0 {
				t.Fatal("journal emitted a second financial path")
			}
			// Losing the caller's response snapshot must not lose the durable
			// anomaly or let another hop bypass the still-failing global seal.
			empty := billing.Settlement{OrgID: "org-owned", RequestID: req}
			if begin {
				err = s.BeginInclusiveWithRecord(bg, "org-owned", req, req+".2", empty)
			} else {
				_, err = s.ReserveOwned(bg, "org-owned", req, req+".2", "synthetic/token", 200, time.Hour, billing.ReserveAdd, true, empty)
			}
			if err == nil {
				t.Error("empty snapshot bypassed durable anomaly seal")
			}
			fault.fail.Store(false)
			if begin {
				err = s.BeginInclusiveWithRecord(bg, "org-owned", req, req+".2", rec)
			} else {
				_, err = s.ReserveOwned(bg, "org-owned", req, req+".2", "synthetic/token", 200, time.Hour, billing.ReserveAdd, true, rec)
			}
			if err != nil {
				t.Fatal(err)
			}
			if blocked, e := s.InclusiveQuarantined(bg, "independent-org", model); e != nil || !blocked {
				t.Fatalf("cross-org fence missing: %t %v", blocked, e)
			}
		})
	}
}
