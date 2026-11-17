package redisstate

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/redis/go-redis/v9"
)

func (s *ManagedCreditStore) correctionKey(org, req string) string {
	return s.prefix + "mc:{" + org + "}:correction:" + req
}
func (s *ManagedCreditStore) Prepared(ctx context.Context, org, req string) (*inclusive.Correction, error) {
	if validOrg(org) != nil {
		return nil, billing.ErrOwnership
	}
	var raw string
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		raw, err = rdb.Get(ctx, s.correctionKey(org, req)).Result()
		if err == redis.Nil {
			return nil
		}
		return err
	})
	if err != nil || raw == "" {
		return nil, err
	}
	var c inclusive.Correction
	err = json.Unmarshal([]byte(raw), &c)
	return &c, err
}

var prepareCorrection = redis.NewScript(`
local prior=redis.call('GET',KEYS[1])
if prior then return prior end
local raw=redis.call('GET',KEYS[2]);if not raw then return redis.error_reply('missing spend') end
local o=cjson.decode(raw);local c=cjson.decode(ARGV[1])
if not o.terminal or not o.record_json then return redis.error_reply('missing terminal spend') end
local record=cjson.decode(o.record_json)
if record.Kind ~= 'spend' or record.BilledMicros ~= c.SpentMicros then return redis.error_reply('spend conflict') end
redis.call('SET',KEYS[1],ARGV[1])
for _,model in ipairs(c.Anomalies or {}) do redis.call('HSET',KEYS[3],model,c.RequestID) end
return ARGV[1]
`)

func (s *ManagedCreditStore) Prepare(ctx context.Context, c inclusive.Correction) (inclusive.Correction, error) {
	if validOrg(c.OrgID) != nil || c.RequestID == "" || c.SpentMicros < 0 || c.ActualMicros < 0 || c.RefundMicros != max(int64(0), c.SpentMicros-c.ActualMicros) || c.ShortfallMicros != max(int64(0), c.ActualMicros-c.SpentMicros) {
		return c, billing.ErrOwnership
	}
	if c.Anomalies == nil {
		c.Anomalies = []string{}
	}
	if err := s.sealCatalogue(ctx, c.OrgID, c.RequestID, c.Anomalies); err != nil {
		return c, err
	}
	data, _ := json.Marshal(c)
	k := s.ownedKeys(c.OrgID, c.RequestID)
	var raw string
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		raw, err = prepareCorrection.Run(ctx, rdb, []string{s.correctionKey(c.OrgID, c.RequestID), k[5], k[6]}, string(data)).Text()
		return err
	})
	if err != nil {
		return c, err
	}
	var saved inclusive.Correction
	if err = json.Unmarshal([]byte(raw), &saved); err != nil {
		return c, err
	}
	if saved.SpentMicros != c.SpentMicros {
		return c, billing.ErrOwnership
	}
	return saved, nil
}

var finishCorrection = redis.NewScript(`
if redis.call('GET',KEYS[1]) ~= ARGV[1] then return redis.error_reply('correction conflict') end
redis.call('SREM',KEYS[2],ARGV[2]);redis.call('SET',KEYS[3],'1');return 1
`)

func (s *ManagedCreditStore) Finish(ctx context.Context, c inclusive.Correction) error {
	if validOrg(c.OrgID) != nil {
		return billing.ErrOwnership
	}
	if c.Anomalies == nil {
		c.Anomalies = []string{}
	}
	data, _ := json.Marshal(c)
	key := s.correctionKey(c.OrgID, c.RequestID)
	return s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return finishCorrection.Run(ctx, rdb, []string{key, s.ownedKeys(c.OrgID, c.RequestID)[8], key + ":done"}, string(data), c.RequestID).Err()
	})
}

// ReconcileOrg runs bounded recovery and pending work. Multiple replicas may
// call it: terminal CAS and the existing refund idempotency fence both steps.
// A failed/missing generation remains in pending without an expiry waiver.
func (s *ManagedCreditStore) ReconcileOrg(ctx context.Context, org string, client inclusive.GenerationClient, refund inclusive.Refund) error {
	if validOrg(org) != nil || client == nil || refund == nil {
		return billing.ErrOwnership
	}
	k := s.ownedKeys(org, "")
	var ids []string
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		ids, err = rdb.ZRangeByScore(ctx, k[7], &redis.ZRangeBy{Min: "-inf", Max: fmt.Sprint(s.now().UnixMilli()), Count: 100}).Result()
		return err
	})
	if err != nil {
		return err
	}
	var first error
	for _, id := range ids {
		if _, err := s.RecoverInclusive(ctx, org, id); err != nil && first == nil {
			first = err
		}
	}
	var cursor uint64
	err = s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		cursor, err = rdb.Get(ctx, k[8]+":cursor").Uint64()
		if err != nil && err != redis.Nil {
			return err
		}
		ids, cursor, err = rdb.SScan(ctx, k[8], cursor, "*", 100).Result()
		if err != nil {
			return err
		}
		return rdb.Set(ctx, k[8]+":cursor", cursor, 0).Err()
	})
	if err != nil {
		return err
	}
	for _, id := range ids {
		var raw string
		err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
			var err error
			raw, err = rdb.Get(ctx, s.ownedKeys(org, id)[5]).Result()
			return err
		})
		var o billing.OwnedRequest
		if err == nil {
			err = json.Unmarshal([]byte(raw), &o)
		}
		if err == nil {
			if scoped, ok := client.(interface{ Accepts(string, string) bool }); ok && !scoped.Accepts(o.Record.Provider, o.Record.UpstreamKeyID) {
				err = fmt.Errorf("generation credential identity unavailable")
			} else {
				_, err = inclusive.Reconcile(ctx, o.Record, client, s, refund)
			}
		}
		if err != nil {
			code := "generation_or_refund_unavailable"
			if strings.Contains(err.Error(), "credential") {
				code = "original_credential_unavailable_or_not_retained"
			} else if strings.Contains(err.Error(), "snapshot") {
				code = "served_model_outside_snapshot_review_required"
			} else if strings.Contains(err.Error(), "identity") {
				code = "generation_identity_missing_or_conflicting"
			}
			savedErr := s.run(ctx, func(ctx context.Context, r redis.UniversalClient) error {
				return r.HSet(ctx, k[8]+":errors", id, code).Err()
			})
			if first == nil {
				if savedErr != nil {
					first = savedErr
				} else {
					first = err
				}
			}
		} else {
			_ = s.run(ctx, func(ctx context.Context, r redis.UniversalClient) error { return r.HDel(ctx, k[8]+":errors", id).Err() })
		}
	}
	return first
}
