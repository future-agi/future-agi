package redisstate

import (
	"context"
	"errors"
	"time"

	"github.com/redis/go-redis/v9"
)

// MCPSessionStore keeps MCP legacy sessions in Redis, so any gateway replica
// resumes a session another opened: the session record (mcp.SessionStore,
// plus mcp.SessionRefresher) and the session's server-event log
// (mcp.SessionEventLog), a stream capped at its last events. The mcp package
// names the keys (MC.6: mcps:{<org>}:<hash> and mcpev:{<org>}:<hash>, which
// share a slot through the org hash tag); this store puts prefix ahead of
// each. Every value is opaque to it.
type MCPSessionStore struct {
	client *Client
	prefix string
}

// NewMCPSessionStore returns the store on client, with prefix ahead of every
// key ("" keeps the contract's names).
func NewMCPSessionStore(client *Client, prefix string) *MCPSessionStore {
	return &MCPSessionStore{client: client, prefix: prefix}
}

var errNoMCPSessionRedis = errors.New("mcp sessions: no Redis client")

// mcpSessionTimeout bounds each Redis call.
const mcpSessionTimeout = 500 * time.Millisecond

// do runs fn with a context detached from the caller's cancellation and
// bounded by mcpSessionTimeout: a client that went away must not count as a
// Redis failure against the breaker every store shares.
func (s *MCPSessionStore) do(ctx context.Context, fn func(ctx context.Context, rdb redis.UniversalClient) error) error {
	if s == nil || s.client == nil {
		return errNoMCPSessionRedis
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), mcpSessionTimeout)
	defer cancel()
	return s.client.Do(func(rdb redis.UniversalClient) error { return fn(ctx, rdb) })
}

// Save stores data under id with ttl (no expiry when ttl <= 0).
func (s *MCPSessionStore) Save(ctx context.Context, id string, data []byte, ttl time.Duration) error {
	return s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return rdb.Set(ctx, s.prefix+id, data, max(ttl, 0)).Err()
	})
}

// Load returns the data under id, and false when there is none.
func (s *MCPSessionStore) Load(ctx context.Context, id string) ([]byte, bool, error) {
	var data []byte
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		v, err := rdb.Get(ctx, s.prefix+id).Bytes()
		if errors.Is(err, redis.Nil) {
			return nil
		}
		data = v
		return err
	})
	if err != nil {
		return nil, false, err
	}
	return data, data != nil, nil
}

// Delete removes the data under id.
func (s *MCPSessionStore) Delete(ctx context.Context, id string) error {
	return s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return rdb.Del(ctx, s.prefix+id).Err()
	})
}

// mcpSessionRefreshScript replaces a record that still exists (SET XX) and
// renews its event log's ttl with it; a record already deleted or expired is
// never written back. KEYS: record, event log. ARGV: data, ttl ms.
var mcpSessionRefreshScript = redis.NewScript(`
if redis.call('SET', KEYS[1], ARGV[1], 'XX', 'PX', ARGV[2]) then
  redis.call('PEXPIRE', KEYS[2], ARGV[2])
  return 1
end
return 0
`)

// Refresh replaces the record under id and renews its and the event log's
// ttl, only when the record still exists (mcp.SessionRefresher).
func (s *MCPSessionStore) Refresh(ctx context.Context, id, eventsID string, data []byte, ttl time.Duration) (bool, error) {
	var alive bool
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		n, err := mcpSessionRefreshScript.Run(ctx, rdb, []string{s.prefix + id, s.prefix + eventsID},
			data, ttlMillis(ttl)).Int()
		alive = n == 1
		return err
	})
	return alive, err
}

// mcpSessionAppendScript appends one event to a live session's log, keeping
// the last ARGV[2] events and renewing the log's ttl; it returns the event id,
// or nil when the record is gone. KEYS: record, event log. ARGV: data, max
// length, ttl ms.
var mcpSessionAppendScript = redis.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 0 then
  return false
end
local id = redis.call('XADD', KEYS[2], 'MAXLEN', ARGV[2], '*', 'd', ARGV[1])
redis.call('PEXPIRE', KEYS[2], ARGV[3])
return id
`)

// AppendSessionEvent appends data to the event log under eventsID and
// returns its id; it reports false, appending nothing, when the record under
// id no longer exists (mcp.SessionEventLog).
func (s *MCPSessionStore) AppendSessionEvent(ctx context.Context, id, eventsID string, data []byte, maxLen int, ttl time.Duration) (string, bool, error) {
	var eventID string
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		v, err := mcpSessionAppendScript.Run(ctx, rdb, []string{s.prefix + id, s.prefix + eventsID},
			data, maxLen, ttlMillis(ttl)).Text()
		if errors.Is(err, redis.Nil) {
			return nil
		}
		eventID = v
		return err
	})
	if err != nil {
		return "", false, err
	}
	return eventID, eventID != "", nil
}

// SessionEventsAfter calls each, oldest first, for at most limit events
// logged after afterID (mcp.SessionEventLog).
func (s *MCPSessionStore) SessionEventsAfter(ctx context.Context, eventsID, afterID string, limit int, each func(id string, data []byte)) error {
	var msgs []redis.XMessage
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		msgs, err = rdb.XRangeN(ctx, s.prefix+eventsID, "("+afterID, "+", int64(limit)).Result()
		return err
	})
	if err != nil {
		return err
	}
	for _, m := range msgs {
		if d, ok := m.Values["d"].(string); ok {
			each(m.ID, []byte(d))
		}
	}
	return nil
}

// DeleteSessionEvents removes the event log under eventsID.
func (s *MCPSessionStore) DeleteSessionEvents(ctx context.Context, eventsID string) error {
	return s.Delete(ctx, eventsID)
}

// ttlMillis is ttl in whole milliseconds, at least 1 (PX and PEXPIRE refuse
// 0).
func ttlMillis(ttl time.Duration) int64 {
	return max(ttl.Milliseconds(), 1)
}
