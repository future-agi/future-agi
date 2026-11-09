package redisstate

import (
	"context"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/redis/go-redis/v9"
)

// The catalogue fence lives in ONE separate Redis slot. Never put that key in
// an org's financial Lua transaction (which would fail in Redis Cluster).
//
// Protocol: reserve provisionally in the org slot, then confirm at the global
// fence. A sealed fence rejects and rolls back the provisional attempt. The
// successful confirmation is the admission linearization point. Settlement
// seals the global fence BEFORE committing the org spend; an interrupted writer
// leaves a conservative quarantine, never an admission window. Begin checks the
// fence again so older unstarted holds cannot become new calls after review was
// requested. Calls already admitted before the seal may finish and settle.
// No expiring distributed locks or process-local caches are used. Only an
// explicit operator review may remove a fence; reconciliation never clears it.
func (s *ManagedCreditStore) catalogueFenceKey() string {
	return s.prefix + "mc:{catalogue}:quarantine"
}
func (s *ManagedCreditStore) checkCatalogueFence(ctx context.Context, model string) error {
	var blocked bool
	err := s.run(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		var e error
		blocked, e = r.HExists(ctx, s.catalogueFenceKey(), model).Result()
		return e
	})
	if err != nil {
		return err
	}
	if blocked {
		return billing.ErrOwnership
	}
	return nil
}
func (s *ManagedCreditStore) sealCatalogue(ctx context.Context, org, request string, listings []string) error {
	if len(listings) == 0 {
		return nil
	}
	fields := map[string]any{}
	for _, id := range listings {
		fields[id] = org + "/" + request
	}
	return s.run(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		return r.HSet(ctx, s.catalogueFenceKey(), fields).Err()
	})
}

var cancelProvisional = redis.NewScript(`
local raw=redis.call('GET',KEYS[1]);if not raw then return 0 end
local o=cjson.decode(raw);local a=o.attempts[ARGV[2]]
if o.terminal or o.finalizing or not a or a.dispatched then return 0 end
local held=tonumber(redis.call('HGET',KEYS[2],ARGV[1]) or '0')
if held >= a.micros then
 redis.call('HINCRBY',KEYS[2],ARGV[1],-a.micros)
 if redis.call('EXISTS',KEYS[3])==1 then redis.call('HINCRBY',KEYS[3],'held',-a.micros) end
 if held==a.micros then redis.call('HDEL',KEYS[2],ARGV[1]);redis.call('ZREM',KEYS[4],ARGV[1]) end
end
o.attempts[ARGV[2]]=nil;redis.call('SET',KEYS[1],cjson.encode(o));return 1
`)

func (s *ManagedCreditStore) cancelProvisional(ctx context.Context, org, request, attempt string) {
	k := s.ownedKeys(org, request)
	_ = s.run(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		return cancelProvisional.Run(ctx, r, []string{k[5], k[1], k[0], k[2]}, request, attempt).Err()
	})
}
func (s *ManagedCreditStore) checkBeginFence(ctx context.Context, org, request, attempt string) error {
	var raw string
	err := s.run(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		var e error
		raw, e = r.Get(ctx, s.ownedKeys(org, request)[5]).Result()
		return e
	})
	if err != nil {
		return err
	}
	var o billing.OwnedRequest
	if json.Unmarshal([]byte(raw), &o) != nil {
		return billing.ErrOwnership
	}
	a, ok := o.Attempts[attempt]
	if !ok {
		return billing.ErrOwnership
	}
	if a.Dispatched {
		return nil
	} // already admitted; retry cannot create a new call
	return s.checkCatalogueFence(ctx, a.Model)
}
