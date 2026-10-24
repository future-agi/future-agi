package redisstate

import (
	"context"
	"errors"
	"log/slog"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
)

// AgentSignalStore keeps the agent signals routing learns from in Redis:
// session/model affinity pins (affinity.Backend) and outcome counters, the
// Last record of each generation and each subject's previous generation
// (outcomes.Backend). The routing packages own the key names and the
// fallback; this store adds prefix to every key and does the Redis work.
type AgentSignalStore struct {
	client *Client
	prefix string
}

// NewAgentSignalStore returns the store on client, with prefix ahead of
// every key ("" keeps the contract's key names).
func NewAgentSignalStore(client *Client, prefix string) *AgentSignalStore {
	return &AgentSignalStore{client: client, prefix: prefix}
}

var errNoAgentSignalRedis = errors.New("agent signals: no Redis client")

// agentSignalTimeout bounds each Redis call, as the credit, budget and rate
// limit stores bound theirs.
const agentSignalTimeout = 200 * time.Millisecond

// do runs fn on Redis with a context detached from the caller's: its values,
// none of its deadline or cancellation, and agentSignalTimeout of its own.
// The plugin and /v1/feedback call the store on the request's context, which
// may have ended by then (the gateway timeout fired, or the client went away).
// The call runs on its own bounded context, so it still reaches Redis and an
// ended request context never counts against the circuit breaker every Redis
// store shares. A Redis call slower than the timeout counts, as it does for
// those stores. The caller falls back to memory on any error, so a failure
// other than the open breaker (which logs its own opening) is logged here.
// fn's ctx shadows the caller's on purpose: every Redis call inside it is bounded.
func (s *AgentSignalStore) do(ctx context.Context, fn func(ctx context.Context, rdb redis.UniversalClient) error) error {
	if s == nil || s.client == nil {
		return errNoAgentSignalRedis
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), agentSignalTimeout)
	defer cancel()
	err := s.client.Do(func(rdb redis.UniversalClient) error { return fn(ctx, rdb) })
	if err != nil && !errors.Is(err, ErrCircuitOpen) {
		slog.Warn("agent signals: Redis call failed, using the in-memory fallback", "error", err)
	}
	return err
}

// Hash fields of an affinity pin and a Last record.
const (
	fieldModel        = "model"
	fieldProvider     = "provider"
	fieldRequestID    = "request_id"
	fieldPrefixTokens = "prefix_tokens"
	fieldAgent        = "agent"
	fieldRun          = "run"
	fieldStep         = "step"
	fieldClass        = "class"
	fieldJudged       = "judged"
)

// --- affinity.Backend ---------------------------------------------------------

// GetAffinity reads the pin at key.
func (s *AgentSignalStore) GetAffinity(ctx context.Context, key string) (affinity.Value, bool, error) {
	var h map[string]string
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) (err error) {
		h, err = rdb.HGetAll(ctx, s.prefix+key).Result()
		return err
	})
	if err != nil || len(h) == 0 {
		return affinity.Value{}, false, err
	}
	tokens, _ := strconv.Atoi(h[fieldPrefixTokens])
	return affinity.Value{Model: h[fieldModel], Provider: h[fieldProvider], RequestID: h[fieldRequestID], PrefixTokens: tokens}, true, nil
}

// SetAffinity replaces the pin at key and keeps it for ttl.
func (s *AgentSignalStore) SetAffinity(ctx context.Context, key string, v affinity.Value, ttl time.Duration) error {
	return s.replaceHash(ctx, s.prefix+key, ttl,
		fieldModel, v.Model, fieldProvider, v.Provider, fieldRequestID, v.RequestID, fieldPrefixTokens, strconv.Itoa(v.PrefixTokens))
}

// DelAffinity removes the pin at key.
func (s *AgentSignalStore) DelAffinity(ctx context.Context, key string) error {
	return s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error { return rdb.Del(ctx, s.prefix+key).Err() })
}

// --- outcomes.Backend ---------------------------------------------------------

// addOutcomeScript decays every counter of the hash KEYS[1] from its ts_ms
// to ARGV[1] (half-life ARGV[2] ms), adds the field/increment pairs from
// ARGV[4] on, moves ts_ms forward and keeps the hash for ARGV[3] ms. A
// timestamp ahead of now (another node's clock) is neither decayed nor
// moved back.
var addOutcomeScript = redis.NewScript(`
local now = tonumber(ARGV[1])
local half_life = tonumber(ARGV[2])
local ts = tonumber(redis.call('HGET', KEYS[1], 'ts_ms') or '0')
if ts > 0 and now > ts and half_life > 0 then
  local f = 0.5 ^ ((now - ts) / half_life)
  local all = redis.call('HGETALL', KEYS[1])
  for i = 1, #all, 2 do
    if all[i] ~= 'ts_ms' then
      redis.call('HSET', KEYS[1], all[i], tostring(tonumber(all[i + 1]) * f))
    end
  end
end
for i = 4, #ARGV, 2 do
  redis.call('HINCRBYFLOAT', KEYS[1], ARGV[i], ARGV[i + 1])
end
if now > ts then
  redis.call('HSET', KEYS[1], 'ts_ms', ARGV[1])
end
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return 1
`)

// AddOutcome decays the counters hash at key to nowMs and adds d.
func (s *AgentSignalStore) AddOutcome(ctx context.Context, key string, d outcomes.Delta, nowMs, halfLifeMs int64, ttl time.Duration) error {
	args := make([]any, 0, 3+2*len(d))
	args = append(args, nowMs, halfLifeMs, ttl.Milliseconds())
	for field, v := range d {
		args = append(args, field, strconv.FormatFloat(v, 'g', -1, 64))
	}
	return s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return addOutcomeScript.Run(ctx, rdb, []string{s.prefix + key}, args...).Err()
	})
}

// OutcomeCounters reads the counters hash at key, as last written.
func (s *AgentSignalStore) OutcomeCounters(ctx context.Context, key string) (outcomes.Counters, bool, error) {
	var h map[string]string
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) (err error) {
		h, err = rdb.HGetAll(ctx, s.prefix+key).Result()
		return err
	})
	var c outcomes.Counters
	if err != nil || len(h) == 0 {
		return c, false, err
	}
	for field, raw := range h {
		if v, perr := strconv.ParseFloat(raw, 64); perr == nil {
			c.SetField(field, v)
		}
	}
	return c, true, nil
}

// SetLast replaces the generation's Last record at key and keeps it for ttl.
func (s *AgentSignalStore) SetLast(ctx context.Context, key string, l outcomes.Last, ttl time.Duration) error {
	judged := "0"
	if l.Judged {
		judged = "1"
	}
	return s.replaceHash(ctx, s.prefix+key, ttl, fieldAgent, l.Agent, fieldRun, l.Run, fieldStep, string(l.Step),
		fieldModel, l.Model, fieldClass, string(l.Class), fieldJudged, judged)
}

// GetLast reads the generation's Last record at key.
func (s *AgentSignalStore) GetLast(ctx context.Context, key string) (outcomes.Last, bool, error) {
	var h map[string]string
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) (err error) {
		h, err = rdb.HGetAll(ctx, s.prefix+key).Result()
		return err
	})
	if err != nil || len(h) == 0 {
		return outcomes.Last{}, false, err
	}
	return outcomes.Last{Agent: h[fieldAgent], Run: h[fieldRun], Step: models.Step(h[fieldStep]), Model: h[fieldModel],
		Class: outcomes.Class(h[fieldClass]), Judged: h[fieldJudged] == "1"}, true, nil
}

// claimScript sets judged on the Last record KEYS[1] if it exists: 1 when
// this call set it, 0 when it was already set, -1 when there is no record
// (so a claim never creates a key without a TTL).
var claimScript = redis.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 0 then
  return -1
end
if redis.call('HGET', KEYS[1], 'judged') == '1' then
  return 0
end
redis.call('HSET', KEYS[1], 'judged', '1')
return 1
`)

// ClaimJudgement marks the Last record at key judged.
func (s *AgentSignalStore) ClaimJudgement(ctx context.Context, key string) (claimed, found bool, err error) {
	var res int64
	err = s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) (err error) {
		res, err = claimScript.Run(ctx, rdb, []string{s.prefix + key}).Int64()
		return err
	})
	if err != nil {
		return false, false, err
	}
	return res == 1, res >= 0, nil
}

// SetPrev records requestID at key for ttl.
func (s *AgentSignalStore) SetPrev(ctx context.Context, key, requestID string, ttl time.Duration) error {
	return s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return rdb.Set(ctx, s.prefix+key, requestID, ttl).Err()
	})
}

// GetPrev reads the request id at key. A missing key is a miss, not an
// error, so misses never count against the client's circuit breaker.
func (s *AgentSignalStore) GetPrev(ctx context.Context, key string) (string, bool, error) {
	var id string
	found := false
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		v, err := rdb.Get(ctx, s.prefix+key).Result()
		if errors.Is(err, redis.Nil) {
			return nil
		}
		id, found = v, err == nil
		return err
	})
	return id, found, err
}

// replaceHash writes fieldValues as the whole hash at key with ttl, in one
// transaction, so a reader never sees a half-written or TTL-less hash.
func (s *AgentSignalStore) replaceHash(ctx context.Context, key string, ttl time.Duration, fieldValues ...string) error {
	values := make([]any, len(fieldValues))
	for i, v := range fieldValues {
		values[i] = v
	}
	return s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		_, err := rdb.TxPipelined(ctx, func(p redis.Pipeliner) error {
			p.Del(ctx, key)
			p.HSet(ctx, key, values...)
			p.PExpire(ctx, key, ttl)
			return nil
		})
		return err
	})
}

var (
	_ affinity.Backend = (*AgentSignalStore)(nil)
	_ outcomes.Backend = (*AgentSignalStore)(nil)
)
