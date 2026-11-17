package redisstate

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// SpendGuardNewOrgPeriod is how long after an org's first managed request
// its new-org daily cap applies.
const SpendGuardNewOrgPeriod = 7 * 24 * time.Hour

// spendGuardAlertTTL is how long one anomaly alert silences the next.
const spendGuardAlertTTL = time.Hour

// SpendGuardStore keeps the managed spend guard's state in Redis:
// per-minute spend buckets per org (sg:{<org>}:m:<epoch_min>) and per key
// (sg:{<org>}:k:<key>:m:<epoch_min>), a per-UTC-day bucket for the new-org
// cap (sg:{<org>}:d:<epoch_day>), when the org's first managed request was
// checked (sg:{<org>}:first, kept forever), one hold record per admitted
// request (sg:{<org>}:h:<hold_id>), the hourly alert mark
// (sg:{<org>}:alerted) and the auto-pause (mc:{<org>}:paused). Every one
// shares the org's hash tag.
// The EE budget enforcer's pause:<org>:managed_spend is read as EE writes
// it, without a hash tag, so it is read on its own before the script.
//
// A bucket counts what each admitted request holds (its worst-case estimate)
// until the request settles, then what it was billed; so the windows count
// in-flight spend at its worst case. Production keys have no prefix; tests
// isolate theirs with one.
type SpendGuardStore struct {
	client *Client
	prefix string
}

// NewSpendGuardStore returns the store over client. A nil client is never
// available: every operation returns billing.ErrUnavailable.
func NewSpendGuardStore(client *Client, prefix string) *SpendGuardStore {
	return &SpendGuardStore{client: client, prefix: prefix}
}

// SpendGuardLimits are the caps an admission is checked against, in micros;
// 0 is no cap.
type SpendGuardLimits struct {
	MinuteMicros int64
	HourMicros   int64
	// NewOrgDailyMicros caps each UTC day's spend for SpendGuardNewOrgPeriod
	// after the org's first managed request.
	NewOrgDailyMicros int64
}

// SpendGuardAdmission is one request to admit: its org, its key (empty for a
// caller without one, which has only the org's windows), the caller's hold
// id, its worst-case estimate and the time.
type SpendGuardAdmission struct {
	Org, KeyID, HoldID string
	EstimateMicros     int64
	Limits             SpendGuardLimits
	Now                time.Time
}

// SpendGuardOutcome is what an admission decided.
type SpendGuardOutcome string

const (
	SpendGuardAdmitted     SpendGuardOutcome = "ok"
	SpendGuardOverCap      SpendGuardOutcome = "velocity"
	SpendGuardNewOrgCap    SpendGuardOutcome = "new_org_cap"
	SpendGuardBudgetPaused SpendGuardOutcome = "paused_budget" // pause:<org>:managed_spend
	SpendGuardAutoPaused   SpendGuardOutcome = "paused_auto"   // mc:{<org>}:paused
)

// Scopes and windows a cap refusal names.
const (
	SpendGuardScopeOrg = "org"
	SpendGuardScopeKey = "key"

	SpendGuardWindowMinute = "minute"
	SpendGuardWindowHour   = "hour"
	SpendGuardWindowDay    = "day"
)

// SpendGuardVerdict is an admission's decision. A cap refusal names the
// scope and window that tripped, what that window counted before this
// request, its cap, and when the oldest counted bucket leaves the window.
type SpendGuardVerdict struct {
	Outcome       SpendGuardOutcome
	Scope         string
	Window        string
	CountedMicros int64
	CapMicros     int64
	ResetsAt      time.Time
}

// SpendGuardSums are the org's spend windows after a settlement, for anomaly
// detection: the last 5 minutes, the 60 minutes before them, and the last
// hour.
type SpendGuardSums struct {
	Last5MinMicros   int64
	Prior60MinMicros int64
	LastHourMicros   int64
}

var errNoHoldID = errors.New("redisstate: spend guard hold id is empty")

func (s *SpendGuardStore) base(org string) string { return s.prefix + "sg:{" + org + "}:" }
func (s *SpendGuardStore) pausedKey(org string) string {
	return s.prefix + "mc:{" + org + "}:paused"
}
func (s *SpendGuardStore) budgetPauseKey(org string) string {
	return s.prefix + "pause:" + org + ":managed_spend"
}

// run executes fn through the client's circuit breaker on its own timeout.
// Any failure is billing.ErrUnavailable: managed billing fails closed.
func (s *SpendGuardStore) run(ctx context.Context, fn func(ctx context.Context, rdb redis.UniversalClient) error) error {
	if s.client == nil || !s.client.Available() {
		return billing.ErrUnavailable
	}
	ctx, cancel := context.WithTimeout(ctx, managedOpTimeout)
	defer cancel()
	if err := s.client.Do(func(rdb redis.UniversalClient) error { return fn(ctx, rdb) }); err != nil {
		return fmt.Errorf("%w: %v", billing.ErrUnavailable, err)
	}
	return nil
}

// Admit checks a request against, in order, the new-org daily cap, the EE
// budget pause, the auto-pause, and the key's then the org's minute and hour
// windows. A window refuses when what it counts plus the estimate exceeds its
// cap. An admitted request adds its estimate to its minute buckets and to the
// day bucket, and leaves a hold record that Settle and Void correct from. A
// hold id already admitted is admitted again without adding anything.
func (s *SpendGuardStore) Admit(ctx context.Context, a SpendGuardAdmission) (SpendGuardVerdict, error) {
	if err := validOrg(a.Org); err != nil {
		return SpendGuardVerdict{}, err
	}
	if a.HoldID == "" {
		return SpendGuardVerdict{}, errNoHoldID
	}
	base := s.base(a.Org)
	var reply []any
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		budgetPaused, err := rdb.Exists(ctx, s.budgetPauseKey(a.Org)).Result()
		if err != nil {
			return err
		}
		reply, err = spendGuardAdmitScript.Run(ctx, rdb, []string{base + "first", s.pausedKey(a.Org), base + "h:" + a.HoldID},
			base, a.KeyID, max(a.EstimateMicros, 0), a.Now.UnixMilli(),
			max(a.Limits.MinuteMicros, 0), max(a.Limits.HourMicros, 0), max(a.Limits.NewOrgDailyMicros, 0),
			SpendGuardNewOrgPeriod.Milliseconds(), budgetPaused).Slice()
		return err
	})
	if err != nil {
		return SpendGuardVerdict{}, err
	}
	return parseVerdict(reply)
}

// parseVerdict reads the admission script's reply: {outcome} or, for a cap,
// {outcome, scope, window, counted, cap, resets_at_ms}.
func parseVerdict(reply []any) (SpendGuardVerdict, error) {
	if len(reply) == 0 {
		return SpendGuardVerdict{}, fmt.Errorf("%w: empty spend guard reply", billing.ErrUnavailable)
	}
	outcome, _ := reply[0].(string)
	v := SpendGuardVerdict{Outcome: SpendGuardOutcome(outcome)}
	if len(reply) == 6 {
		v.Scope, _ = reply[1].(string)
		v.Window, _ = reply[2].(string)
		v.CountedMicros, v.CapMicros = asInt(reply[3]), asInt(reply[4])
		v.ResetsAt = time.UnixMilli(asInt(reply[5])).UTC()
	}
	return v, nil
}

// Settle replaces the hold's estimate with billedMicros in the buckets the
// estimate went into, once per hold id, and returns the org's windows at
// now. applied is false when the hold id was never admitted or was already
// settled or voided.
func (s *SpendGuardStore) Settle(ctx context.Context, org, holdID string, billedMicros int64, now time.Time) (sums SpendGuardSums, applied bool, err error) {
	reply, err := s.correct(ctx, org, holdID, max(billedMicros, 0), now)
	if err != nil || len(reply) < 4 {
		return SpendGuardSums{}, false, err
	}
	return SpendGuardSums{Last5MinMicros: asInt(reply[1]), Prior60MinMicros: asInt(reply[2]), LastHourMicros: asInt(reply[3])}, true, nil
}

// Void takes the hold's estimate out of the buckets it went into, once per
// hold id. applied is false when the hold id was never admitted or was
// already settled or voided.
func (s *SpendGuardStore) Void(ctx context.Context, org, holdID string) (applied bool, err error) {
	reply, err := s.correct(ctx, org, holdID, -1, time.Time{})
	return err == nil && len(reply) > 0 && asInt(reply[0]) == 1, err
}

// correct runs the correction script: billed -1 is a void.
func (s *SpendGuardStore) correct(ctx context.Context, org, holdID string, billed int64, now time.Time) ([]any, error) {
	if err := validOrg(org); err != nil {
		return nil, err
	}
	if holdID == "" {
		return nil, errNoHoldID
	}
	base := s.base(org)
	var reply []any
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		reply, err = spendGuardCorrectScript.Run(ctx, rdb, []string{base + "h:" + holdID}, base, billed, now.UnixMilli()).Slice()
		return err
	})
	if err != nil {
		return nil, err
	}
	if len(reply) == 0 || asInt(reply[0]) != 1 {
		return nil, nil
	}
	return reply, nil
}

// MarkAnomaly records that the org's spend looked anomalous and reports
// whether this is the first mark in the hour: only the first raises an
// alert, on whichever replica wins it. With pause, the first mark also sets
// the org's auto-pause.
func (s *SpendGuardStore) MarkAnomaly(ctx context.Context, org string, pause bool) (first bool, err error) {
	if err := validOrg(org); err != nil {
		return false, err
	}
	pauseArg := 0
	if pause {
		pauseArg = 1
	}
	err = s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		n, err := spendGuardMarkScript.Run(ctx, rdb, []string{s.base(org) + "alerted", s.pausedKey(org)},
			int64(spendGuardAlertTTL/time.Second), pauseArg).Int64()
		first = n == 1
		return err
	})
	return first, err
}

// Unpause clears the org's auto-pause and reports whether one was set. It
// never touches the EE budget pause, which only EE lifts.
func (s *SpendGuardStore) Unpause(ctx context.Context, org string) (cleared bool, err error) {
	if err := validOrg(org); err != nil {
		return false, err
	}
	err = s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		n, err := rdb.Del(ctx, s.pausedKey(org)).Result()
		cleared = n > 0
		return err
	})
	return cleared, err
}

// spendGuardLua is shared by the scripts. Bucket and record keys are built
// from the base sg:{<org>}: passed in ARGV[1], so they share the declared
// keys' hash slot. Expiry is relative to the caller's now: a bucket lives
// until two hours after its minute, a day bucket until the end of the next
// day, a hold record two hours.
const spendGuardLua = `
local MIN, DAY = 60000, 86400000
local function int(v) return string.format('%d', v) end
local function bucketsSum(names)
  local s = 0
  for _, v in ipairs(redis.call('MGET', unpack(names))) do s = s + (tonumber(v) or 0) end
  return s
end
local function minuteNames(prefix, from, to)
  local names = {}
  for b = from, to do names[#names + 1] = prefix .. int(b) end
  return names
end
`

// spendGuardAdmitScript is Admit.
//
// KEYS: first, auto-pause, hold record.
// ARGV: base, key id, estimate, now (ms), minute cap, hour cap, new-org daily
// cap, new-org period (ms), EE budget pause (0 or 1).
var spendGuardAdmitScript = redis.NewScript(spendGuardLua + `
local base, key = ARGV[1], ARGV[2]
local est, now = tonumber(ARGV[3]), tonumber(ARGV[4])
local minCap, hourCap, newCap, newPeriod = tonumber(ARGV[5]), tonumber(ARGV[6]), tonumber(ARGV[7]), tonumber(ARGV[8])
local m, d = math.floor(now / MIN), math.floor(now / DAY)
if redis.call('EXISTS', KEYS[3]) == 1 then return {'ok'} end
local dayKey = base .. 'd:' .. int(d)
redis.call('SET', KEYS[1], int(now), 'NX')
local first = tonumber(redis.call('GET', KEYS[1]))
if newCap > 0 and now - first < newPeriod then
  local spent = tonumber(redis.call('GET', dayKey)) or 0
  if spent + est > newCap then return {'new_org_cap', 'org', 'day', int(spent), int(newCap), int((d + 1) * DAY)} end
end
if ARGV[9] ~= '0' then return {'paused_budget'} end
if redis.call('EXISTS', KEYS[2]) == 1 then return {'paused_auto'} end
-- window: the n minute buckets ending at m, and when the oldest non-zero
-- one leaves the window (bucket i, minute m-n+i, leaves at minute m+i).
local function window(prefix, n)
  local vals = redis.call('MGET', unpack(minuteNames(prefix, m - n + 1, m)))
  local sum, resets = 0, nil
  for i, v in ipairs(vals) do
    local x = tonumber(v) or 0
    if x > 0 and not resets then resets = (m + i) * MIN end
    sum = sum + x
  end
  return sum, resets or (m + 1) * MIN
end
local scopes = {}
if key ~= '' then scopes[#scopes + 1] = {'key', base .. 'k:' .. key .. ':m:'} end
scopes[#scopes + 1] = {'org', base .. 'm:'}
for _, sc in ipairs(scopes) do
  for _, w in ipairs({{'minute', 1, minCap}, {'hour', 60, hourCap}}) do
    if w[3] > 0 then
      local sum, resets = window(sc[2], w[2])
      if sum + est > w[3] then return {'velocity', sc[1], w[1], int(sum), int(w[3]), int(resets)} end
    end
  end
end
local function add(k, expireAt)
  redis.call('INCRBY', k, int(est))
  redis.call('PEXPIRE', k, int(expireAt - now))
end
add(base .. 'm:' .. int(m), (m + 120) * MIN)
if key ~= '' then add(base .. 'k:' .. key .. ':m:' .. int(m), (m + 120) * MIN) end
add(dayKey, (d + 2) * DAY)
redis.call('HSET', KEYS[3], 'm', int(m), 'd', int(d), 'k', key, 'e', int(est))
redis.call('PEXPIRE', KEYS[3], 120 * MIN)
return {'ok'}
`)

// spendGuardCorrectScript is Settle and Void: once per hold record, add
// billed - estimate (a void, billed -1, adds -estimate) to the buckets the
// estimate went into, while they still exist. A settle returns the org's
// windows at now.
//
// KEYS: hold record.
// ARGV: base, billed (-1 for a void), now (ms).
// Reply: {0} when there is no hold record; {1} for a void; {1, last 5
// minutes, the 60 minutes before them, last hour} for a settle.
var spendGuardCorrectScript = redis.NewScript(spendGuardLua + `
local base, billed = ARGV[1], tonumber(ARGV[2])
local h = redis.call('HMGET', KEYS[1], 'm', 'd', 'k', 'e')
if not h[1] then return {0} end
redis.call('DEL', KEYS[1])
local est = tonumber(h[4]) or 0
local delta = -est
if billed >= 0 then delta = billed - est end
local function fix(k)
  if delta ~= 0 and redis.call('EXISTS', k) == 1 then redis.call('INCRBY', k, int(delta)) end
end
fix(base .. 'm:' .. h[1])
if h[3] and h[3] ~= '' then fix(base .. 'k:' .. h[3] .. ':m:' .. h[1]) end
fix(base .. 'd:' .. h[2])
if billed < 0 then return {1} end
local m = math.floor(tonumber(ARGV[3]) / MIN)
local prefix = base .. 'm:'
return {1, int(bucketsSum(minuteNames(prefix, m - 4, m))), int(bucketsSum(minuteNames(prefix, m - 64, m - 5))),
  int(bucketsSum(minuteNames(prefix, m - 59, m)))}
`)

// spendGuardMarkScript is MarkAnomaly.
//
// KEYS: alert mark, auto-pause. ARGV: mark TTL (s), pause (0 or 1).
var spendGuardMarkScript = redis.NewScript(`
if not redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1]) then return 0 end
if ARGV[2] == '1' then redis.call('SET', KEYS[2], '1') end
return 1
`)
