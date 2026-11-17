package redisstate

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/redis/go-redis/v9"
)

func (s *ManagedCreditStore) ownedKeys(org, request string) []string {
	k := s.keys(org)
	base := s.prefix + "mc:{" + org + "}:"
	return []string{k.acct, k.holds, k.holdexp, k.settled(request), k.outbox, base + "owned:" + request, base + "quarantine", base + "recovery", base + "pending"}
}

func (s *ManagedCreditStore) ReserveInclusive(ctx context.Context, org, request, attempt, model string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	return s.ReserveOwned(ctx, org, request, attempt, model, micros, ttl, mode, false, billing.Settlement{})
}

// ReserveOwned uses the same durable protocol for either kind of managed hop.
// Import and reservation are atomic: a crash between reserve and Begin cannot
// make the earlier managed attempts expire or disappear from recovery.
func (s *ManagedCreditStore) ReserveOwned(ctx context.Context, org, request, attempt, model string, micros int64, ttl time.Duration, _ billing.ReserveMode, tokenPriced bool, rec billing.Settlement) (billing.ReserveResult, error) {
	if validOrg(org) != nil || request == "" || attempt == "" || model == "" || micros < 0 || micros > 1<<50 || ttl <= 0 {
		return billing.ReserveResult{}, billing.ErrOwnership
	}
	if err := s.SealOwnedJournal(ctx, org, request, rec); err != nil {
		return billing.ReserveResult{}, err
	}
	if err := s.checkCatalogueFence(ctx, model); err != nil {
		return billing.ReserveResult{}, err
	}
	data, err := json.Marshal(rec)
	if err != nil {
		return billing.ReserveResult{}, err
	}
	var reply []any
	now := s.now()
	err = s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		reply, err = ownedReserveScript.Run(ctx, rdb, s.ownedKeys(org, request), request, attempt, model, micros, now.UnixMilli(), now.Add(ttl).UnixMilli(), s.overdraftMicros, tokenPriced, string(data), now.Add(24*time.Hour).UnixMilli()).Slice()
		return err
	})
	if err != nil {
		return billing.ReserveResult{}, err
	}
	status, acct, err := parseState(reply)
	if err == nil && billing.ReserveStatus(status) == billing.ReserveOK {
		if fenceErr := s.checkCatalogueFence(ctx, model); fenceErr != nil {
			s.cancelProvisional(ctx, org, request, attempt)
			return billing.ReserveResult{}, fenceErr
		}
	}
	return billing.ReserveResult{Status: billing.ReserveStatus(status), Account: acct}, err
}

func (s *ManagedCreditStore) BeginInclusive(ctx context.Context, org, request, attempt string) error {
	return s.BeginInclusiveWithRecord(ctx, org, request, attempt, billing.Settlement{OrgID: org, RequestID: request})
}
func (s *ManagedCreditStore) BeginInclusiveWithRecord(ctx context.Context, org, request, attempt string, rec billing.Settlement) error {
	if validOrg(org) != nil || rec.OrgID != org || rec.RequestID != request {
		return billing.ErrOwnership
	}
	if err := s.SealOwnedJournal(ctx, org, request, rec); err != nil {
		return err
	}
	if err := s.checkBeginFence(ctx, org, request, attempt); err != nil {
		return err
	}
	data, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	k := s.ownedKeys(org, request)
	return s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		// Recovery is deliberately later than the synchronous request's maximum
		// lifetime. Every new dispatch renews it; terminal settlement removes it.
		return ownedBeginScript.Run(ctx, rdb, []string{k[5], k[1], k[2], k[7], k[6]}, request, attempt, s.now().UnixMilli(), string(data), s.now().Add(24*time.Hour).UnixMilli()).Err()
	})
}

func (s *ManagedCreditStore) finishOwned(ctx context.Context, org, request string, rec billing.Settlement, expected string) (billing.SettleResult, bool, error) {
	data, err := json.Marshal(rec)
	if err != nil {
		return billing.SettleResult{}, true, err
	}
	q := billing.OwnedQuarantines(rec)
	if q == nil {
		q = []string{}
	}
	quarantines, _ := json.Marshal(q)
	args := []any{request, rec.BilledMicros, billing.OwnedFingerprint(rec), rec.Kind, string(data), string(quarantines), expected, org}
	// A global seal may fail independently of the org's financial shard. Make
	// the complete settlement durable first, so losing this caller's response
	// can never turn a known final charge back into a smaller admission hold.
	var prepared string
	err = s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		prepared, err = ownedPrepareFinishScript.Run(ctx, rdb, s.ownedKeys(org, request), args...).Text()
		return err
	})
	if err != nil {
		return billing.SettleResult{}, true, err
	}
	if prepared == "" {
		return billing.SettleResult{}, false, nil
	}
	args[6] = prepared // preserve recovery CAS across the prepare write
	if err := s.sealCatalogue(ctx, org, request, q); err != nil {
		return billing.SettleResult{}, true, err
	}
	for _, f := range billing.OwnedFinancial(rec) {
		args = append(args, f)
	}
	var reply []any
	err = s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		reply, err = ownedFinishScript.Run(ctx, rdb, s.ownedKeys(org, request), args...).Slice()
		return err
	})
	if err != nil {
		return billing.SettleResult{}, true, err
	}
	if len(reply) == 1 && reply[0] == "legacy" {
		return billing.SettleResult{}, false, nil
	}
	status, acct, err := parseState(reply)
	if err != nil {
		return billing.SettleResult{}, true, err
	}
	// Discovery is a hint only: workers also SCAN the per-org recovery/pending
	// indexes. The BL drain independently discovers all database accounts.
	_ = s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return rdb.SAdd(ctx, s.outboxOrgsKey(), org).Err()
	})
	return billing.SettleResult{Applied: status != "duplicate", AccountMissing: status == "ok_missing", Account: acct}, true, nil
}

// RecoverInclusive compares the entire ownership journal in the terminal Lua
// transaction. A concurrent reservation/begin/settlement invalidates the read;
// recovery never overwrites a live writer's newer evidence.
func (s *ManagedCreditStore) RecoverInclusive(ctx context.Context, org, request string) (billing.SettleResult, error) {
	k := s.ownedKeys(org, request)
	var raw string
	var due float64
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		raw, err = rdb.Get(ctx, k[5]).Result()
		if err != nil {
			return err
		}
		due, err = rdb.ZScore(ctx, k[7], request).Result()
		return err
	})
	if err != nil {
		return billing.SettleResult{}, err
	}
	if due > float64(s.now().UnixMilli()) {
		return billing.SettleResult{}, fmt.Errorf("recovery not due")
	}
	var o billing.OwnedRequest
	if err = json.Unmarshal([]byte(raw), &o); err != nil {
		return billing.SettleResult{}, err
	}
	if !o.Dispatched || o.Terminal != "" {
		return billing.SettleResult{}, billing.ErrOwnership
	}
	res, _, err := s.finishOwned(ctx, org, request, o.RecoveryRecord(org, request, s.now()), raw)
	return res, err
}

func (s *ManagedCreditStore) InclusiveQuarantined(ctx context.Context, org, model string) (bool, error) {
	if err := validOrg(org); err != nil {
		return false, err
	}
	var found bool
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		found, err = rdb.HExists(ctx, s.catalogueFenceKey(), model).Result()
		if err == nil && !found {
			found, err = rdb.HExists(ctx, s.ownedKeys(org, "")[6], model).Result()
		}
		return err
	})
	return found, err
}
