package redisstate

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// MCPApprovalStore uses opaque records and compare-and-swap transitions. Keys
// and their org index share mapr:{org}'s Redis cluster slot. No fallback state.
type MCPApprovalStore struct{ client *Client }

func NewMCPApprovalStore(client *Client) *MCPApprovalStore { return &MCPApprovalStore{client: client} }
func (s *MCPApprovalStore) do(ctx context.Context, fn func(context.Context, redis.UniversalClient) error) error {
	if s == nil || s.client == nil {
		return errors.New("approval Redis unavailable")
	}
	ctx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
	defer cancel()
	return s.client.Do(func(r redis.UniversalClient) error { return fn(ctx, r) })
}

var approvalCreate = redis.NewScript(`
local t = redis.call('TIME')
local now = tonumber(t[1])*1000 + math.floor(tonumber(t[2])/1000)
if not redis.call('SET', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[2]) then return 0 end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
redis.call('ZADD', KEYS[2], now+tonumber(ARGV[2]), KEYS[1])
if redis.call('PTTL', KEYS[2]) < tonumber(ARGV[2]) then redis.call('PEXPIRE', KEYS[2], ARGV[2]) end
return 1
`)

func (s *MCPApprovalStore) Create(ctx context.Context, key, index string, data []byte, ttl time.Duration) error {
	if ttl <= 0 {
		return errors.New("invalid approval TTL")
	}
	return s.do(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		n, err := approvalCreate.Run(ctx, r, []string{key, index}, data, ttl.Milliseconds()).Int()
		if err == nil && n != 1 {
			return errors.New("approval already exists")
		}
		return err
	})
}
func (s *MCPApprovalStore) Load(ctx context.Context, key string) ([]byte, bool, error) {
	var data []byte
	err := s.do(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		var err error
		data, err = r.Get(ctx, key).Bytes()
		if errors.Is(err, redis.Nil) {
			return nil
		}
		return err
	})
	return data, data != nil, err
}

var approvalCAS = redis.NewScript(`
local t = redis.call('TIME')
local now = tonumber(t[1])*1000 + math.floor(tonumber(t[2])/1000)
if now >= tonumber(ARGV[3]) then return 0 end
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'XX', 'KEEPTTL')
return 1
`)

func (s *MCPApprovalStore) CompareSwap(ctx context.Context, key string, old, next []byte, until time.Time) (bool, error) {
	var changed bool
	err := s.do(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		n, err := approvalCAS.Run(ctx, r, []string{key}, old, next, until.UnixMilli()).Int()
		changed = n == 1
		return err
	})
	return changed, err
}

// A keyset cursor uses the immutable retention score and Redis key, not an
// offset (which skips rows when TTL cleanup removes earlier entries). Binary
// search also handles score ties and a cursor whose own record has expired.
// Each call reads at most limit records plus O(log N) index probes. This is a
// live traversal, not a snapshot: later inserts ahead of it appear next sweep.
var approvalListPage = redis.NewScript(`
local t = redis.call('TIME')
local now = tonumber(t[1])*1000 + math.floor(tonumber(t[2])/1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local count = redis.call('ZCARD', KEYS[1])
local start = 0
if ARGV[1] ~= '' then
 local score = tonumber(ARGV[2])
 local lo, hi = 0, count
 while lo < hi do
  local mid = math.floor((lo+hi)/2)
  local row = redis.call('ZREVRANGE', KEYS[1], mid, mid, 'WITHSCORES')
  local s = tonumber(row[2])
  if s > score or (s == score and row[1] >= ARGV[1]) then lo = mid+1 else hi = mid end
 end
 start = lo
end
local limit = tonumber(ARGV[3])
local ids = redis.call('ZREVRANGE', KEYS[1], start, start+limit-1, 'WITHSCORES')
local out = {'', ''}
if start + #ids/2 < count and #ids > 0 then
 out[1], out[2] = ids[#ids-1], ids[#ids]
end
for i=1,#ids,2 do
 local data = redis.call('GET', ids[i])
 if data then table.insert(out, data) else redis.call('ZREM', KEYS[1], ids[i]) end
end
return out
`)

var ErrApprovalCursor = errors.New("invalid approval page cursor or limit")

type approvalCursor struct {
	Index string `json:"index"`
	Key   string `json:"key"`
	Score int64  `json:"score"`
}

func (s *MCPApprovalStore) ListPage(ctx context.Context, index, cursor string, limit int) ([][]byte, string, error) {
	if limit < 1 || limit > 1000 || len(cursor) > 4096 {
		return nil, "", ErrApprovalCursor
	}
	var position approvalCursor
	if cursor != "" {
		raw, err := base64.RawURLEncoding.DecodeString(cursor)
		if err != nil || json.Unmarshal(raw, &position) != nil || position.Index != index ||
			position.Score <= 0 || position.Score > 9007199254740991 || position.Key == index ||
			!strings.HasPrefix(position.Key, strings.TrimSuffix(index, "index")) {
			return nil, "", ErrApprovalCursor
		}
	}
	var out [][]byte
	var next string
	err := s.do(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		rows, err := approvalListPage.Run(ctx, r, []string{index}, position.Key, position.Score, limit).StringSlice()
		if err != nil {
			return err
		}
		if len(rows) < 2 {
			return errors.New("invalid approval page response")
		}
		if rows[0] != "" {
			score, err := strconv.ParseInt(rows[1], 10, 64)
			if err != nil {
				return err
			}
			raw, err := json.Marshal(approvalCursor{Index: index, Key: rows[0], Score: score})
			if err != nil {
				return err
			}
			next = base64.RawURLEncoding.EncodeToString(raw)
		}
		for _, row := range rows[2:] {
			out = append(out, []byte(row))
		}
		return nil
	})
	return out, next, err
}

// Legacy callers still receive the first bounded page. Exhaustive consumers
// must follow ListPage's continuation, including pages empty after filtering.
func (s *MCPApprovalStore) List(ctx context.Context, index string) ([][]byte, error) {
	rows, _, err := s.ListPage(ctx, index, "", 1000)
	return rows, err
}
