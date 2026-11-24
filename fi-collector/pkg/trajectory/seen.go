package trajectory

import (
	"container/list"
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

// Item is one content-addressed entry destined for ClickHouse `trajectory_items`.
type Item struct {
	ProjectID string
	ScopeID   string
	ItemHash  string // 32-char lowercase hex
	ItemType  string // message | tool_call | tool_result | reasoning | tool_def | raw
	Role      string
	Content   string // verbatim original JSON
}

// Key uniquely identifies an item inside trajectory_items.
type ItemKey struct {
	ProjectID string
	ScopeID   string
	ItemHash  string
}

func (it Item) Key() ItemKey {
	return ItemKey{ProjectID: it.ProjectID, ScopeID: it.ScopeID, ItemHash: it.ItemHash}
}

// ToRow converts the Item to the JSONEachRow map matching schema 040_trajectory_items.sql.
func (it Item) ToRow(now time.Time) map[string]any {
	return map[string]any{
		"project_id":    it.ProjectID,
		"scope_id":      it.ScopeID,
		"item_hash":     it.ItemHash,
		"item_type":     it.ItemType,
		"role":          it.Role,
		"content":       it.Content,
		"first_seen_at": now.UTC().Format("2006-01-02 15:04:05.000000"),
	}
}

// redisSeenLuaSrc atomically adds each hash argument to the scope's Redis set,
// refreshes its TTL, and returns a 1/0 array indicating which hashes were NEW.
// Executed via pipelined EVAL (script is ~170B and compiled/cached by SHA1
// server-side) so fresh or restarted Redis instances never fail with NOSCRIPT.
const redisSeenLuaSrc = `
local key = KEYS[1]
local ttl = tonumber(ARGV[1])
local res = {}
for i = 2, #ARGV do
	res[i - 1] = redis.call("SADD", key, ARGV[i])
end
if ttl and ttl > 0 then
	redis.call("EXPIRE", key, ttl)
end
return res
`

type lruEntry struct {
	key       ItemKey
	expiresAt time.Time
}

// SeenStore provides 2-tier (pod-local TTL-bounded LRU + optional shared Redis set)
// deduplication so:
//  1. Turns hitting the same pod resolve in ~50 ns with zero network I/O.
//  2. Turns hitting different pods behind a load balancer resolve via Redis so
//     items are neither redundantly written nor double-billed to the customer.
//  3. Redis outages fail open to local LRU + ClickHouse ReplacingMergeTree.
//  4. Entries expire after sessionTTL / projectTTL so long-lived pods refresh
//     active project-scoped prompts/tools into current monthly partitions and
//     never outlive retention boundaries.
type SeenStore struct {
	rdb        *redis.Client
	sessionTTL time.Duration
	projectTTL time.Duration
	nowFn      func() time.Time

	mu       sync.Mutex
	cap      int
	ll       *list.List
	entries  map[ItemKey]*list.Element
	redisErr atomic.Uint64
}

// NewSeenStore creates a 2-tier SeenStore. rdb may be nil (local-only mode).
func NewSeenStore(rdb *redis.Client, lruCap int, sessionTTL, projectTTL time.Duration) *SeenStore {
	if lruCap <= 0 {
		lruCap = DefaultLRUSize
	}
	if sessionTTL <= 0 {
		sessionTTL = DefaultSessionTTL
	}
	if projectTTL <= 0 {
		projectTTL = DefaultProjectTTL
	}
	return &SeenStore{
		rdb:        rdb,
		sessionTTL: sessionTTL,
		projectTTL: projectTTL,
		nowFn:      time.Now,
		cap:        lruCap,
		ll:         list.New(),
		entries:    make(map[ItemKey]*list.Element, min(lruCap, 4096)),
	}
}

func (s *SeenStore) now() time.Time {
	if s.nowFn != nil {
		return s.nowFn()
	}
	return time.Now()
}

func (s *SeenStore) ttlFor(scopeID string) time.Duration {
	if scopeID == ProjectScopeID {
		return s.projectTTL
	}
	return s.sessionTTL
}

// InvalidateProject evicts all pod-local LRU entries belonging to projectID.
// Wired to Redis `fi:project:invalidate` so deleted/recreated projects never
// skip trajectory_items writes based on stale local cache state.
func (s *SeenStore) InvalidateProject(projectID string) int {
	if s == nil || projectID == "" {
		return 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	removed := 0
	for k, elem := range s.entries {
		if k.ProjectID == projectID {
			s.ll.Remove(elem)
			delete(s.entries, k)
			removed++
		}
	}
	return removed
}

// RedisErrors returns the cumulative number of Redis fail-open events.
func (s *SeenStore) RedisErrors() uint64 { return s.redisErr.Load() }

// FilterNew partitions items into genuinely NEW items (to persist & bill) and
// already-seen items (to skip writing & bill 0 bytes). Duplicate keys within
// `items` itself are collapsed first so each unique key is counted at most once.
func (s *SeenStore) FilterNew(ctx context.Context, items []Item) (newItems []Item, reusedCount int, reusedBytes int64) {
	if len(items) == 0 {
		return nil, 0, 0
	}

	// Step 1: in-slice dedup + Tier 1 pod-local LRU check.
	type candidate struct {
		item Item
	}
	inSlice := make(map[ItemKey]struct{}, len(items))
	var misses []candidate

	now := s.now()
	s.mu.Lock()
	for _, it := range items {
		k := it.Key()
		if _, dup := inSlice[k]; dup {
			reusedCount++
			reusedBytes += int64(len(it.Content))
			continue
		}
		inSlice[k] = struct{}{}
		if elem, ok := s.entries[k]; ok {
			ent := elem.Value.(*lruEntry)
			if now.Before(ent.expiresAt) {
				s.ll.MoveToFront(elem)
				reusedCount++
				reusedBytes += int64(len(it.Content))
				continue
			}
			// Expired: evict and re-verify via Tier 2 / ClickHouse so active
			// project-scoped prompts periodically refresh into current partitions.
			s.ll.Remove(elem)
			delete(s.entries, k)
		}
		misses = append(misses, candidate{item: it})
	}
	s.mu.Unlock()

	if len(misses) == 0 {
		return nil, reusedCount, reusedBytes
	}

	// Step 2: Tier 2 cross-pod Redis check (pipelined per (project_id, scope_id)).
	if s.rdb == nil {
		newItems = make([]Item, 0, len(misses))
		s.mu.Lock()
		for _, c := range misses {
			s.putLocked(c.item.Key(), now)
			newItems = append(newItems, c.item)
		}
		s.mu.Unlock()
		return newItems, reusedCount, reusedBytes
	}

	type scopeKey struct {
		projectID string
		scopeID   string
	}
	groups := make(map[scopeKey][]Item)
	order := make([]scopeKey, 0)
	for _, c := range misses {
		sk := scopeKey{projectID: c.item.ProjectID, scopeID: c.item.ScopeID}
		if _, exists := groups[sk]; !exists {
			order = append(order, sk)
		}
		groups[sk] = append(groups[sk], c.item)
	}

	rctx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
	defer cancel()

	pipe := s.rdb.Pipeline()
	cmds := make([]*redis.Cmd, len(order))
	for i, sk := range order {
		group := groups[sk]
		ttlSec := int64(s.sessionTTL.Seconds())
		if sk.scopeID == ProjectScopeID {
			ttlSec = int64(s.projectTTL.Seconds())
		}
		args := make([]any, 0, len(group)+1)
		args = append(args, ttlSec)
		for _, it := range group {
			args = append(args, it.ItemHash)
		}
		cmds[i] = pipe.Eval(rctx, redisSeenLuaSrc, []string{redisSeenKey(sk.projectID, sk.scopeID)}, args...)
	}
	_, pipeErr := pipe.Exec(rctx)

	newItems = make([]Item, 0, len(misses))
	s.mu.Lock()
	defer s.mu.Unlock()

	for i, sk := range order {
		group := groups[sk]
		if pipeErr != nil {
			// Check per-command error; fail open on Redis error so we never lose items.
			if cmds[i].Err() != nil {
				s.redisErr.Add(1)
				for _, it := range group {
					s.putLocked(it.Key(), now)
					newItems = append(newItems, it)
				}
				continue
			}
		}
		vals, err := cmds[i].Int64Slice()
		if err != nil || len(vals) != len(group) {
			s.redisErr.Add(1)
			for _, it := range group {
				s.putLocked(it.Key(), now)
				newItems = append(newItems, it)
			}
			continue
		}
		for j, it := range group {
			s.putLocked(it.Key(), now)
			if vals[j] == 0 {
				// Already stored & billed by another collector pod.
				reusedCount++
				reusedBytes += int64(len(it.Content))
			} else {
				newItems = append(newItems, it)
			}
		}
	}
	return newItems, reusedCount, reusedBytes
}

// Forget removes keys from the local LRU and best-effort from Redis so that if
// a ClickHouse trajectory_items write fails and falls back to inline, future
// turns will re-insert those items cleanly.
func (s *SeenStore) Forget(ctx context.Context, items []Item) {
	if s == nil || len(items) == 0 {
		return
	}
	s.mu.Lock()
	for _, it := range items {
		k := it.Key()
		if elem, ok := s.entries[k]; ok {
			s.ll.Remove(elem)
			delete(s.entries, k)
		}
	}
	s.mu.Unlock()

	if s.rdb == nil {
		return
	}
	type scopeKey struct {
		projectID string
		scopeID   string
	}
	groups := make(map[scopeKey][]any)
	for _, it := range items {
		sk := scopeKey{projectID: it.ProjectID, scopeID: it.ScopeID}
		groups[sk] = append(groups[sk], it.ItemHash)
	}
	rctx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
	defer cancel()
	pipe := s.rdb.Pipeline()
	for sk, hashes := range groups {
		pipe.SRem(rctx, redisSeenKey(sk.projectID, sk.scopeID), hashes...)
	}
	if _, err := pipe.Exec(rctx); err != nil {
		// Best-effort Redis SREM after ClickHouse fallback; ReplacingMergeTree remains authoritative.
		s.redisErr.Add(1)
	}
}

func (s *SeenStore) putLocked(k ItemKey, now time.Time) {
	exp := now.Add(s.ttlFor(k.ScopeID))
	if elem, ok := s.entries[k]; ok {
		elem.Value.(*lruEntry).expiresAt = exp
		s.ll.MoveToFront(elem)
		return
	}
	if s.ll.Len() >= s.cap {
		if oldest := s.ll.Back(); oldest != nil {
			s.ll.Remove(oldest)
			delete(s.entries, oldest.Value.(*lruEntry).key)
		}
	}
	s.entries[k] = s.ll.PushFront(&lruEntry{key: k, expiresAt: exp})
}

func redisSeenKey(projectID, scopeID string) string {
	return fmt.Sprintf("traj:seen:%s:%s", projectID, scopeID)
}
