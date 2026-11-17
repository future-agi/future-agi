package logspool

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Defaults for the spool's bounds.
const (
	DefaultMaxBytes = 64 << 20
	// Slack kept after a batch's budget period ends, and the hard outer bound
	// on a batch's age (the usage consumer's lateness window).
	PeriodSlack = 72 * time.Hour
	MaxAge      = 35 * 24 * time.Hour
	LeaseTTL    = 30 * time.Second
)

// Drop reasons, the logspool_dropped_batches label.
const (
	ReasonSize    = "size"
	ReasonAge     = "age"
	ReasonMAC     = "mac"
	ReasonRefused = "refused"
)

// ErrTooLarge is why a batch larger than the whole spool is not spooled.
var ErrTooLarge = errors.New("logspool: batch is larger than the spool")

// Store is the spool in the gateway's Redis. Every key is the operator's
// prefix plus a fixed name: no organisation, key or request id is ever in a
// key name. Writes, removals and evictions are single Lua scripts.
type Store struct {
	client   *redisstate.Client
	prefix   string
	period   string
	maxBytes int64
	now      func() time.Time
}

// NewStore returns the spool under prefix (the gateway's Redis key prefix)
// for budgets of period (the seed period: daily, weekly, monthly or total).
func NewStore(client *redisstate.Client, prefix, period string, maxBytes int64) *Store {
	if maxBytes <= 0 {
		maxBytes = DefaultMaxBytes
	}
	if period == "" {
		period = "monthly"
	}
	return &Store{client: client, prefix: prefix + "logspool:v1:", period: period, maxBytes: maxBytes, now: time.Now}
}

func (s *Store) key(name string) string { return s.prefix + name }

// keys of the spool: batches by id, ids by spool time, ids delivered but not
// yet removed, each batch's spend and expiry, the byte count, the lease.
func (s *Store) bKey() string     { return s.key("b") }
func (s *Store) tKey() string     { return s.key("t") }
func (s *Store) dKey() string     { return s.key("d") }
func (s *Store) sKey() string     { return s.key("s") }
func (s *Store) bytesKey() string { return s.key("bytes") }
func (s *Store) leaseKey() string { return s.key("lease") }
func (s *Store) tombPrefix() string {
	return s.key("tomb:")
}

// meta is what a batch's eviction needs, worked out when it is spooled: its
// expiry and the tombstone lines its spend becomes.
type meta struct {
	ExpireAt int64      `json:"x"`
	Tomb     []tombLine `json:"t"`
}

type tombLine struct {
	Suffix   string  `json:"k"` // period start (unix seconds), or "total"
	Field    string  `json:"f"`
	Cost     float64 `json:"c"`
	ExpireMs int64   `json:"e"` // unix ms
}

const fieldSep = "\x1f"

func tombField(l SpendLine) string {
	return strings.Join([]string{l.Org, l.KeyID, l.UserID, l.Model}, fieldSep)
}

// window is the budget period a time falls in under the store's period, and
// how long spend in it is kept: the period's end plus PeriodSlack, at most
// MaxAge after t.
func (s *Store) window(t time.Time) (suffix string, start time.Time, keepUntil time.Time) {
	start = PeriodStart(s.period, t)
	hard := t.Add(MaxAge)
	if s.period == "total" {
		return "total", start, hard
	}
	end := PeriodEnd(s.period, start).Add(PeriodSlack)
	if end.After(hard) {
		end = hard
	}
	return strconv.FormatInt(start.Unix(), 10), start, end
}

func (s *Store) metaOf(b *Batch) meta {
	m := meta{Tomb: []tombLine{}} // [] not null: the scripts walk it
	for _, l := range b.Spend {
		started := l.StartedAt
		if started.IsZero() {
			started = b.SpooledAt
		}
		suffix, _, until := s.window(started)
		if m.ExpireAt == 0 || until.Unix() > m.ExpireAt {
			m.ExpireAt = until.Unix()
		}
		if l.Cost > 0 && l.Org != "" {
			m.Tomb = append(m.Tomb, tombLine{Suffix: suffix, Field: tombField(l), Cost: l.Cost, ExpireMs: until.UnixMilli()})
		}
	}
	if m.ExpireAt == 0 {
		m.ExpireAt = b.SpooledAt.Add(MaxAge).Unix()
	}
	return m
}

// evictLua removes one batch, folding its spend into the tombstones first
// unless it was delivered or is dropped without spend (keepSpend 0).
// Shared by the put, evict and remove scripts.
const evictLua = `
local function evict(id, keepSpend)
  local j = redis.call('HGET', KEYS[1], id)
  if not j then return 0 end
  if keepSpend == 1 and redis.call('SISMEMBER', KEYS[3], id) == 0 then
    local m = redis.call('HGET', KEYS[4], id)
    if m then
      local meta = cjson.decode(m)
      for _, l in ipairs(meta['t'] or {}) do
        local k = ARGV[1] .. l['k']
        redis.call('HINCRBYFLOAT', k, l['f'], l['c'])
        local ttl = redis.call('PTTL', k)
        local want = l['e'] - tonumber(ARGV[2])
        if want > 0 and (ttl < 0 or ttl < want) then redis.call('PEXPIRE', k, want) end
      end
    end
  end
  redis.call('HDEL', KEYS[1], id)
  redis.call('ZREM', KEYS[2], id)
  redis.call('SREM', KEYS[3], id)
  redis.call('HDEL', KEYS[4], id)
  redis.call('DECRBY', KEYS[5], string.len(j))
  return 1
end
`

// KEYS: b, t, d, s, bytes. ARGV: tomb prefix, now ms, id, batch, meta, score, max bytes.
var putScript = redis.NewScript(evictLua + `
local id = ARGV[3]
redis.call('HSET', KEYS[1], id, ARGV[4])
redis.call('HSET', KEYS[4], id, ARGV[5])
redis.call('ZADD', KEYS[2], ARGV[6], id)
local total = redis.call('INCRBY', KEYS[5], string.len(ARGV[4]))
local evicted = 0
local max = tonumber(ARGV[7])
while total > max do
  local oldest = redis.call('ZRANGE', KEYS[2], 0, 0)[1]
  if not oldest or oldest == id then break end
  evicted = evicted + evict(oldest, 1)
  total = tonumber(redis.call('GET', KEYS[5]) or '0')
end
return evicted
`)

// KEYS: b, t, d, s, bytes, lease. ARGV: tomb prefix, now ms, id, token, mode.
// mode "deliver": mark delivered, then remove if the lease is held.
// mode "remove": remove without spend (delivered or refused) under the lease.
// mode "evict": remove keeping its spend as tombstones, under the lease.
// mode "drop": remove without spend (a failed MAC) under the lease.
var removeScript = redis.NewScript(evictLua + `
local id, mode = ARGV[3], ARGV[5]
if mode == 'deliver' and redis.call('HEXISTS', KEYS[1], id) == 1 then redis.call('SADD', KEYS[3], id) end
if redis.call('GET', KEYS[6]) ~= ARGV[4] then return -1 end
local keep = 0
if mode == 'evict' then keep = 1 end
local n = evict(id, keep)
if n == 0 then redis.call('SREM', KEYS[3], id) end
return n
`)

// KEYS: b, bytes, lease. ARGV: id, token, batch.
var updateScript = redis.NewScript(`
if redis.call('GET', KEYS[3]) ~= ARGV[2] then return -1 end
local old = redis.call('HGET', KEYS[1], ARGV[1])
if not old then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[3])
redis.call('INCRBY', KEYS[2], string.len(ARGV[3]) - string.len(old))
return 1
`)

var renewScript = redis.NewScript(`
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('PEXPIRE', KEYS[1], ARGV[2]) end
return 0
`)

var releaseScript = redis.NewScript(`
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
`)

func (s *Store) do(ctx context.Context, fn func(rdb redis.UniversalClient) error) error {
	return s.client.Do(fn)
}

// Put spools a sealed batch. Older batches are evicted, oldest first, until
// the spool is within its size; their spend stays as tombstones. It returns
// how many were evicted.
func (s *Store) Put(ctx context.Context, b *Batch) (int, error) {
	raw, err := json.Marshal(b)
	if err != nil {
		return 0, err
	}
	if int64(len(raw)) > s.maxBytes {
		return 0, ErrTooLarge
	}
	m, err := json.Marshal(s.metaOf(b))
	if err != nil {
		return 0, err
	}
	var evicted int
	err = s.do(ctx, func(rdb redis.UniversalClient) error {
		n, err := putScript.Run(ctx, rdb, []string{s.bKey(), s.tKey(), s.dKey(), s.sKey(), s.bytesKey()},
			s.tombPrefix(), s.now().UnixMilli(), b.ID, raw, m, b.SpooledAt.UnixMilli(), s.maxBytes).Int()
		evicted = n
		return err
	})
	return evicted, err
}

// Lease is a held drain lease: a random token, renewed while held and
// released by compare-and-delete, so one replica never releases another's.
type Lease struct {
	store *Store
	token string
}

// Acquire takes the drain lease, or returns nil when another replica holds it.
func (s *Store) Acquire(ctx context.Context) (*Lease, error) {
	token, err := randomID()
	if err != nil {
		return nil, err
	}
	var ok bool
	err = s.do(ctx, func(rdb redis.UniversalClient) error {
		var err error
		ok, err = rdb.SetNX(ctx, s.leaseKey(), token, LeaseTTL).Result()
		return err
	})
	if err != nil || !ok {
		return nil, err
	}
	return &Lease{store: s, token: token}, nil
}

// Renew extends the lease; false when it was lost.
func (l *Lease) Renew(ctx context.Context) bool {
	var n int
	_ = l.store.do(ctx, func(rdb redis.UniversalClient) error {
		var err error
		n, err = renewScript.Run(ctx, rdb, []string{l.store.leaseKey()}, l.token, LeaseTTL.Milliseconds()).Int()
		return err
	})
	return n == 1
}

// Release gives the lease up if it is still this holder's.
func (l *Lease) Release(ctx context.Context) {
	_ = l.store.do(ctx, func(rdb redis.UniversalClient) error {
		return releaseScript.Run(ctx, rdb, []string{l.store.leaseKey()}, l.token).Err()
	})
}

// ErrLeaseLost is why a removal under a lease that is no longer held did
// nothing.
var ErrLeaseLost = errors.New("logspool: drain lease lost")

func (l *Lease) remove(ctx context.Context, id, mode string) (bool, error) {
	s := l.store
	var n int
	err := s.do(ctx, func(rdb redis.UniversalClient) error {
		var err error
		n, err = removeScript.Run(ctx, rdb,
			[]string{s.bKey(), s.tKey(), s.dKey(), s.sKey(), s.bytesKey(), s.leaseKey()},
			s.tombPrefix(), s.now().UnixMilli(), id, l.token, mode).Int()
		return err
	})
	if err != nil {
		return false, err
	}
	if n < 0 {
		return false, ErrLeaseLost
	}
	return n == 1, nil
}

// Delivered marks the batch delivered, in the same script that then removes
// it. A batch marked delivered is never counted as spend again, even when the
// removal could not run (the lease was lost); a later drain removes it.
func (l *Lease) Delivered(ctx context.Context, id string) error {
	_, err := l.remove(ctx, id, "deliver")
	return err
}

// Remove removes a batch without keeping its spend: delivered, or refused
// for good by the webhook.
func (l *Lease) Remove(ctx context.Context, id string) error {
	_, err := l.remove(ctx, id, "remove")
	return err
}

// Evict removes a batch that has to go undelivered, keeping its spend as
// tombstones until its period ends.
func (l *Lease) Evict(ctx context.Context, id string) error {
	_, err := l.remove(ctx, id, "evict")
	return err
}

// Drop removes a batch whose MAC failed: it is never sent or counted.
func (l *Lease) Drop(ctx context.Context, id string) error {
	_, err := l.remove(ctx, id, "drop")
	return err
}

// Update rewrites a batch (its attempts, resealed) under the lease.
func (l *Lease) Update(ctx context.Context, b *Batch) error {
	s := l.store
	raw, err := json.Marshal(b)
	if err != nil {
		return err
	}
	var n int
	err = s.do(ctx, func(rdb redis.UniversalClient) error {
		var err error
		n, err = updateScript.Run(ctx, rdb, []string{s.bKey(), s.bytesKey(), s.leaseKey()}, b.ID, l.token, raw).Int()
		return err
	})
	if err == nil && n < 0 {
		return ErrLeaseLost
	}
	return err
}

// Stored is a spooled batch as read back, with whether it was delivered and
// when it expires.
type Stored struct {
	Batch     *Batch
	Delivered bool
	ExpireAt  time.Time
	// Corrupt is set when the stored bytes do not parse; such a batch is
	// dropped like one whose MAC fails.
	Corrupt bool
	ID      string
}

// Oldest returns up to n batches, oldest first.
func (s *Store) Oldest(ctx context.Context, n int) ([]Stored, error) {
	var out []Stored
	err := s.do(ctx, func(rdb redis.UniversalClient) error {
		ids, err := rdb.ZRange(ctx, s.tKey(), 0, int64(n-1)).Result()
		if err != nil || len(ids) == 0 {
			return err
		}
		out, err = s.read(ctx, rdb, ids)
		return err
	})
	return out, err
}

// All returns every spooled batch.
func (s *Store) All(ctx context.Context) ([]Stored, error) {
	var out []Stored
	err := s.do(ctx, func(rdb redis.UniversalClient) error {
		ids, err := rdb.ZRange(ctx, s.tKey(), 0, -1).Result()
		if err != nil || len(ids) == 0 {
			return err
		}
		out, err = s.read(ctx, rdb, ids)
		return err
	})
	return out, err
}

func (s *Store) read(ctx context.Context, rdb redis.UniversalClient, ids []string) ([]Stored, error) {
	raws, err := rdb.HMGet(ctx, s.bKey(), ids...).Result()
	if err != nil {
		return nil, err
	}
	metas, err := rdb.HMGet(ctx, s.sKey(), ids...).Result()
	if err != nil {
		return nil, err
	}
	delivered, err := rdb.SMembers(ctx, s.dKey()).Result()
	if err != nil {
		return nil, err
	}
	done := make(map[string]bool, len(delivered))
	for _, id := range delivered {
		done[id] = true
	}
	out := make([]Stored, 0, len(ids))
	for i, id := range ids {
		raw, ok := raws[i].(string)
		if !ok {
			continue // removed meanwhile
		}
		st := Stored{ID: id, Delivered: done[id]}
		var b Batch
		if err := json.Unmarshal([]byte(raw), &b); err != nil || b.ID != id {
			st.Corrupt = true
		} else {
			st.Batch = &b
		}
		if m, ok := metas[i].(string); ok {
			var mm meta
			if json.Unmarshal([]byte(m), &mm) == nil && mm.ExpireAt > 0 {
				st.ExpireAt = time.Unix(mm.ExpireAt, 0)
			}
		}
		out = append(out, st)
	}
	return out, nil
}

// Tombstones returns the spend kept from dropped batches for the budget
// period that starts at start: field (org, key id, user id, model) to cost.
func (s *Store) Tombstones(ctx context.Context, start time.Time) (map[string]float64, error) {
	suffix := "total"
	if s.period != "total" {
		suffix = strconv.FormatInt(start.Unix(), 10)
	}
	out := map[string]float64{}
	err := s.do(ctx, func(rdb redis.UniversalClient) error {
		vals, err := rdb.HGetAll(ctx, s.tombPrefix()+suffix).Result()
		if err != nil {
			return err
		}
		for f, v := range vals {
			c, err := strconv.ParseFloat(v, 64)
			if err != nil {
				return fmt.Errorf("logspool: tombstone %q: %w", f, err)
			}
			out[f] = c
		}
		return nil
	})
	return out, err
}

// Stats are the spool's gauges.
type Stats struct {
	Pending int64
	Bytes   int64
	Oldest  time.Time
}

// Stats reads the spool's size and the age of its oldest batch.
func (s *Store) Stats(ctx context.Context) (Stats, error) {
	var st Stats
	err := s.do(ctx, func(rdb redis.UniversalClient) error {
		n, err := rdb.ZCard(ctx, s.tKey()).Result()
		if err != nil {
			return err
		}
		st.Pending = n
		if b, err := rdb.Get(ctx, s.bytesKey()).Int64(); err == nil {
			st.Bytes = b
		} else if !errors.Is(err, redis.Nil) {
			return err
		}
		oldest, err := rdb.ZRangeWithScores(ctx, s.tKey(), 0, 0).Result()
		if err != nil {
			return err
		}
		if len(oldest) == 1 {
			st.Oldest = time.UnixMilli(int64(oldest[0].Score))
		}
		return nil
	})
	return st, err
}

// Period is the budget period the spool keeps spend for.
func (s *Store) Period() string { return s.period }
