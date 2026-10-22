package redisstate

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// OutboxConsumerGroup is the drain's consumer group on every org's outbox
// stream. The gateway never creates it.
const OutboxConsumerGroup = "django-settlement"

// managedOpTimeout bounds one hot-path operation. It runs on its own
// context, so a hold is still released after the request's has ended.
const managedOpTimeout = 500 * time.Millisecond

// ManagedCreditStore is the managed-credit hot path in Redis: RESERVE,
// SETTLE, VOID and GRANT as Lua scripts over an org's account
// hash, holds, hold expiries, settled markers and settlement outbox, every
// key sharing the org's hash tag {<org>}. It implements billing.Ledger and is
// exported for callers outside the request pipeline, which pass their own
// hold id and hold TTL. Production keys have no prefix: the settlement drain reads
// mc:{<org>}:outbox, mc:outbox_orgs and mc:needs_push as named.
type ManagedCreditStore struct {
	client          *Client
	prefix          string
	overdraftMicros int64
	now             func() time.Time
}

// NewManagedCreditStore returns the store over client. prefix is "" in a
// gateway; tests isolate their keys with one. A nil client is never
// available, so every operation returns billing.ErrUnavailable.
func NewManagedCreditStore(client *Client, prefix string) *ManagedCreditStore {
	return &ManagedCreditStore{client: client, prefix: prefix, now: time.Now}
}

// SetMaxOverdraftMicros lets RESERVE take a balance down to -micros
// (managed.max_overdraft_usd). Call it before use.
func (s *ManagedCreditStore) SetMaxOverdraftMicros(micros int64) { s.overdraftMicros = max(micros, 0) }

// Kind is billing.KindRedis.
func (s *ManagedCreditStore) Kind() string { return billing.KindRedis }

// managedKeys are one org's keys.
type managedKeys struct {
	acct, holds, holdexp, outbox, settledPrefix string
}

func (k managedKeys) settled(holdID string) string { return k.settledPrefix + holdID }

func (s *ManagedCreditStore) keys(org string) managedKeys {
	base := s.prefix + "mc:{" + org + "}:"
	return managedKeys{acct: base + "acct", holds: base + "holds", holdexp: base + "holdexp",
		outbox: base + "outbox", settledPrefix: base + "settled:"}
}

func (s *ManagedCreditStore) outboxOrgsKey() string { return s.prefix + "mc:outbox_orgs" }
func (s *ManagedCreditStore) needsPushKey() string  { return s.prefix + "mc:needs_push" }

// errBadOrg refuses an org id that would break the hash tag.
var errBadOrg = errors.New("redisstate: org id is empty or contains a brace")

func validOrg(org string) error {
	if org == "" || strings.ContainsAny(org, "{}") {
		return errBadOrg
	}
	return nil
}

// run executes fn through the client's circuit breaker on its own timeout.
// Any failure is billing.ErrUnavailable, so managed billing fails closed.
func (s *ManagedCreditStore) run(ctx context.Context, fn func(ctx context.Context, rdb redis.UniversalClient) error) error {
	if s.client == nil || !s.client.Available() {
		return billing.ErrUnavailable
	}
	ctx, cancel := context.WithTimeout(ctx, managedOpTimeout)
	defer cancel()
	err := s.client.Do(func(rdb redis.UniversalClient) error { return fn(ctx, rdb) })
	if err != nil {
		return fmt.Errorf("%w: %v", billing.ErrUnavailable, err)
	}
	return nil
}

// Reserve implements billing.Ledger: RESERVE (mode new or add).
func (s *ManagedCreditStore) Reserve(ctx context.Context, org, holdID string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	if err := validOrg(org); err != nil {
		return billing.ReserveResult{}, err
	}
	k := s.keys(org)
	now := s.now()
	var reply []any
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		reply, err = reserveScript.Run(ctx, rdb, []string{k.acct, k.holds, k.holdexp},
			holdID, max(micros, 0), now.Add(ttl).UnixMilli(), now.UnixMilli(), string(mode),
			s.overdraftMicros, billing.SweepLimit).Slice()
		return err
	})
	if err != nil {
		return billing.ReserveResult{}, err
	}
	status, acct, err := parseState(reply)
	if err != nil {
		return billing.ReserveResult{}, err
	}
	return billing.ReserveResult{Status: billing.ReserveStatus(status), Account: acct}, nil
}

// Settle implements billing.Ledger: SETTLE, writing rec as a spend record.
func (s *ManagedCreditStore) Settle(ctx context.Context, org, holdID string, rec billing.Settlement) (billing.SettleResult, error) {
	rec.Kind = billing.KindSpend
	return s.finish(ctx, org, holdID, rec, rec.BilledMicros)
}

// Void implements billing.Ledger: VOID, writing rec as a void record.
func (s *ManagedCreditStore) Void(ctx context.Context, org, holdID string, rec billing.Settlement) (billing.SettleResult, error) {
	rec.Kind = billing.KindVoid
	rec.BilledMicros, rec.UpstreamMicros, rec.MarkupMicros, rec.LandedMicros = 0, 0, 0, 0
	return s.finish(ctx, org, holdID, rec, 0)
}

// finish runs the settle script for a spend or a void, then adds the org to
// the outbox discovery set. That SADD is a hint, outside the org's slot and
// not atomic with the script: a failure is logged, never returned.
func (s *ManagedCreditStore) finish(ctx context.Context, org, holdID string, rec billing.Settlement, spend int64) (billing.SettleResult, error) {
	if err := validOrg(org); err != nil {
		return billing.SettleResult{}, err
	}
	k := s.keys(org)
	fields := rec.Fields()
	args := make([]any, 0, 3+len(fields))
	args = append(args, holdID, max(spend, 0), int64(billing.SettledMarkerTTL/time.Second))
	for _, f := range fields {
		args = append(args, f)
	}
	var reply []any
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		reply, err = settleScript.Run(ctx, rdb, []string{k.acct, k.holds, k.holdexp, k.settled(holdID), k.outbox}, args...).Slice()
		return err
	})
	if err != nil {
		return billing.SettleResult{}, err
	}
	status, acct, err := parseState(reply)
	if err != nil {
		return billing.SettleResult{}, err
	}
	res := billing.SettleResult{Account: acct}
	switch status {
	case "duplicate":
		return res, nil
	case "ok_missing":
		res.AccountMissing = true
	}
	res.Applied = true
	if err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return rdb.SAdd(ctx, s.outboxOrgsKey(), org).Err()
	}); err != nil {
		slog.Warn("managed credits: outbox discovery hint not written", "org_id", org, "error", err)
	}
	return res, nil
}

// Grant implements billing.Ledger: GRANT.
func (s *ManagedCreditStore) Grant(ctx context.Context, org string, push billing.GrantPush) (billing.GrantResult, error) {
	if err := validOrg(org); err != nil {
		return billing.GrantResult{}, err
	}
	var reply []any
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		k := s.keys(org)
		reply, err = grantScript.Run(ctx, rdb, []string{k.acct, k.holds},
			max(push.GrantedTotalMicros, 0), max(push.Version, 0), max(push.SettledSpendFloorMicros, 0)).Slice()
		return err
	})
	if err != nil {
		return billing.GrantResult{}, err
	}
	status, acct, err := parseState(reply)
	if err != nil {
		return billing.GrantResult{}, err
	}
	return billing.GrantResult{Applied: status == "applied", Account: acct}, nil
}

// Account implements billing.Ledger.
func (s *ManagedCreditStore) Account(ctx context.Context, org string) (billing.Account, error) {
	if err := validOrg(org); err != nil {
		return billing.Account{}, err
	}
	var vals []any
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		vals, err = rdb.HMGet(ctx, s.keys(org).acct, "granted", "granted_version", "spent", "held").Result()
		return err
	})
	if err != nil {
		return billing.Account{}, err
	}
	if vals[0] == nil {
		return billing.Account{}, nil
	}
	return billing.Account{Exists: true, GrantedMicros: asInt(vals[0]), GrantedVersion: asInt(vals[1]),
		SpentMicros: asInt(vals[2]), HeldMicros: asInt(vals[3])}, nil
}

// OutboxPending implements billing.Ledger: the drain group's pending plus
// lag, or the stream's length before the group exists.
func (s *ManagedCreditStore) OutboxPending(ctx context.Context, org string) (int64, error) {
	if err := validOrg(org); err != nil {
		return 0, err
	}
	outbox := s.keys(org).outbox
	var n int64
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		length, err := rdb.XLen(ctx, outbox).Result()
		if err != nil || length == 0 {
			n = length
			return err
		}
		groups, err := rdb.XInfoGroups(ctx, outbox).Result()
		if err != nil {
			return err
		}
		n = length
		for _, g := range groups {
			if g.Name == OutboxConsumerGroup && g.Lag >= 0 {
				n = g.Pending + g.Lag
			}
		}
		return nil
	})
	return n, err
}

// MarkNeedsPush implements billing.Ledger.
func (s *ManagedCreditStore) MarkNeedsPush(ctx context.Context, org string) error {
	if err := validOrg(org); err != nil {
		return err
	}
	return s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return rdb.SAdd(ctx, s.needsPushKey(), org).Err()
	})
}

// parseState reads a script's reply: {status, granted, granted_version,
// spent, held, exists}.
func parseState(reply []any) (string, billing.Account, error) {
	if len(reply) != 6 {
		return "", billing.Account{}, fmt.Errorf("%w: unexpected script reply %v", billing.ErrUnavailable, reply)
	}
	status, _ := reply[0].(string)
	return status, billing.Account{
		Exists:        asInt(reply[5]) == 1,
		GrantedMicros: asInt(reply[1]), GrantedVersion: asInt(reply[2]),
		SpentMicros: asInt(reply[3]), HeldMicros: asInt(reply[4]),
	}, nil
}

func asInt(v any) int64 {
	switch x := v.(type) {
	case int64:
		return x
	case string:
		n, _ := strconv.ParseInt(x, 10, 64)
		return n
	}
	return 0
}

// luaState is the reply every script ends with: {status, granted,
// granted_version, spent, held, exists}. luaInt renders a Lua number as an
// integer string; tostring would write large values in exponent form.
const luaState = `
local function luaInt(v) return string.format('%d', v) end
local function state(status)
  local a = redis.call('HMGET', KEYS[1], 'granted', 'granted_version', 'spent', 'held')
  local exists = 0
  if a[1] then exists = 1 end
  return {status, tonumber(a[1]) or 0, tonumber(a[2]) or 0, tonumber(a[3]) or 0, tonumber(a[4]) or 0, exists}
end
local function release(id)
  redis.call('ZREM', KEYS[3], id)
  local amt = tonumber(redis.call('HGET', KEYS[2], id) or '0')
  if redis.call('HDEL', KEYS[2], id) == 0 then return end
  if amt ~= 0 and redis.call('EXISTS', KEYS[1]) == 1 then
    local held = redis.call('HINCRBY', KEYS[1], 'held', luaInt(-amt))
    if held < 0 then redis.call('HSET', KEYS[1], 'held', 0) end
  end
end
`

// reserveScript is RESERVE: sweep up to ARGV[7] expired holds, then hold
// ARGV[2] micros for hold id ARGV[1] until ARGV[3] (ms), unless the account
// refuses. ARGV[4] is now (ms), ARGV[5] the mode (new or add), ARGV[6] the
// allowed overdraft. A second RESERVE new for a held id adds nothing. The
// decision is billing.decideReserve's.
//
// KEYS: acct, holds, holdexp.
var reserveScript = redis.NewScript(luaState + `
if redis.call('EXISTS', KEYS[1]) == 0 then return state('no_account') end
local expired = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', ARGV[4], 'LIMIT', 0, tonumber(ARGV[7]))
for _, id in ipairs(expired) do release(id) end
local id, micros = ARGV[1], tonumber(ARGV[2])
if ARGV[5] ~= 'add' and redis.call('HEXISTS', KEYS[2], id) == 1 then return state('ok') end
local a = redis.call('HMGET', KEYS[1], 'granted', 'spent', 'held')
local left = (tonumber(a[1]) or 0) - (tonumber(a[2]) or 0) + tonumber(ARGV[6])
local held = tonumber(a[3]) or 0
if left <= 0 then return state('exhausted') end
if left - held >= micros then
  redis.call('HINCRBY', KEYS[2], id, ARGV[2])
  redis.call('ZADD', KEYS[3], ARGV[3], id)
  redis.call('HINCRBY', KEYS[1], 'held', ARGV[2])
  return state('ok')
end
if left >= micros then return state('in_flight') end
return state('insufficient')
`)

// settleScript is SETTLE and VOID, once per hold id: release hold ARGV[1],
// add ARGV[2] micros to spent (overshoot allowed; 0 for a void), XADD the
// record (ARGV[4] on, field value pairs) to the outbox and set the settled
// marker for ARGV[3] seconds. A lost account hash is not recreated: the
// record is still written and the drain's re-push restores the counters.
//
// KEYS: acct, holds, holdexp, settled marker, outbox.
var settleScript = redis.NewScript(luaState + `
if redis.call('EXISTS', KEYS[4]) == 1 then return state('duplicate') end
release(ARGV[1])
local status = 'ok'
if redis.call('EXISTS', KEYS[1]) == 0 then
  status = 'ok_missing'
elseif tonumber(ARGV[2]) > 0 then
  redis.call('HINCRBY', KEYS[1], 'spent', ARGV[2])
end
redis.call('XADD', KEYS[5], '*', unpack(ARGV, 4))
redis.call('SET', KEYS[4], '1', 'EX', ARGV[3])
return state(status)
`)

// grantScript is GRANT: apply granted total ARGV[1] at version ARGV[2] only
// when it is newer than the stored version (or the account is missing, which
// it creates), and always raise spent to the floor ARGV[3]. A recreated
// account (after a Redis loss of the hash) counts the holds still in flight
// as held, so their settlements release exactly what they hold.
//
// KEYS: acct, holds.
var grantScript = redis.NewScript(luaState + `
local status = 'not_applied'
if redis.call('EXISTS', KEYS[1]) == 0 then
  local held = 0
  for _, v in ipairs(redis.call('HVALS', KEYS[2])) do held = held + (tonumber(v) or 0) end
  redis.call('HSET', KEYS[1], 'granted', ARGV[1], 'granted_version', ARGV[2], 'spent', 0, 'held', luaInt(held))
  status = 'applied'
elseif tonumber(ARGV[2]) > tonumber(redis.call('HGET', KEYS[1], 'granted_version') or '-1') then
  redis.call('HSET', KEYS[1], 'granted', ARGV[1], 'granted_version', ARGV[2])
  status = 'applied'
end
if tonumber(ARGV[3]) > tonumber(redis.call('HGET', KEYS[1], 'spent') or '0') then
  redis.call('HSET', KEYS[1], 'spent', ARGV[3])
end
return state(status)
`)
