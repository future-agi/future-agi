package redisstate

import (
	"context"
	"encoding/json"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/redis/go-redis/v9"
)

// Persist before crossing the global catalogue slot. If the seal fails, the
// next hop fails closed and recovery still owns the known prior charge. No
// reservation, dispatch marker, debit or outbox row is created by this step.
var ownedJournalScript = redis.NewScript(`
local raw = redis.call('GET',KEYS[6])
if ARGV[5] == '0' then return raw or '' end
local o = raw and cjson.decode(raw) or {attempts={},dispatched=false}
if o.terminal or o.finalizing or redis.call('EXISTS',KEYS[4]) == 1 then return redis.error_reply('terminal request') end
if redis.call('HEXISTS',KEYS[2],ARGV[1]) == 0 then return redis.error_reply('missing owned hold') end
local heldBefore = tonumber(redis.call('HGET',KEYS[2],ARGV[1]))
local recordJSON = ARGV[2]
` + mergeOwnedJournal + `
redis.call('SET',KEYS[6],cjson.encode(o))
if o.dispatched then redis.call('ZREM',KEYS[3],ARGV[1]); redis.call('ZADD',KEYS[8],ARGV[3],ARGV[1]) end
for _, model in ipairs(cjson.decode(ARGV[4])) do redis.call('HSET',KEYS[7],model,ARGV[1]) end
return cjson.encode(o)
`)

func (s *ManagedCreditStore) SealOwnedJournal(ctx context.Context, org, request string, rec billing.Settlement) error {
	updating := len(rec.ManagedJournal) > 0
	if validOrg(org) != nil || request == "" || (updating && (rec.OrgID != org || rec.RequestID != request)) {
		return billing.ErrOwnership
	}
	q := billing.OwnedQuarantines(rec)
	if q == nil {
		q = []string{}
	}
	data, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	models, err := json.Marshal(q)
	if err != nil {
		return err
	}
	var raw string
	if err = s.run(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		var e error
		raw, e = ownedJournalScript.Run(ctx, r, s.ownedKeys(org, request), request, string(data), s.now().Add(24*time.Hour).UnixMilli(), string(models), updating).Text()
		return e
	}); err != nil {
		return err
	}
	if raw == "" {
		return nil
	}
	var owned billing.OwnedRequest
	if err = json.Unmarshal([]byte(raw), &owned); err != nil {
		return err
	}
	// Seal from durable evidence, not only this caller's snapshot. A retry
	// without the previous response cannot bypass a failed global seal.
	saved := owned.Record
	saved.ManagedJournal = owned.Attempts
	return s.sealCatalogue(ctx, org, request, billing.OwnedQuarantines(saved))
}
