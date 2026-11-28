package trajectory

import (
	"container/list"
	"context"
	"crypto/rand"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
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
		"project_id":   it.ProjectID,
		"scope_id":     it.ScopeID,
		"item_hash":    it.ItemHash,
		"item_type":    it.ItemType,
		"role":         it.Role,
		"content":      it.Content,
		"persisted_at": now.UTC().Format("2006-01-02 15:04:05.000000"),
	}
}

// Seen store v2.
//
// Redis layout (one format for reuse age, committer and store epoch):
//
//	traj:seen:v2:{project}:{storeEpoch}:{scope}:{gen}   HASH item_hash -> value
//
// gen = floor(unix(persisted_at) / R) with R = max_reuse_age / 2, so a key
// holds the items committed during one R-long generation. value is the
// committer token (first 16 hex of sha1(billing key), or "*" for requests
// without a single billing key), plus "|r" when the commit was a refresh and
// "|u" when the committing request's usage event is known to be a duplicate
// (its bytes were never billed). Each key expires at (gen+4)*R + 1h and no
// read ever touches a TTL, so every entry has a hard age bound.
//
// Classification of an item against the generations g (now) .. g-3:
//
//	ReuseOther  billed hit in g or g-1 by another committer or by "*": credited, not written
//	ReuseSelf   billed hit in g or g-1 by the same committer: billed as new, not written
//	            (so a re-poll of the same trace bills the same amount in either order)
//	Refresh     billed hit only in g-2 or g-3: a fresh copy is written and credited;
//	            a same-committer hit whose value carries "|r" is classed Refresh again
//	            (credited, not rewritten), so a re-poll of a refresh bills the same
//	New         no billed hit (missing, expired, or only "|u" hits); a Redis error is New
//
// Every pointer-only span emitted at time t therefore references a copy whose
// persisted_at is newer than t - max_reuse_age. The store epoch (Redis key
// traj:epoch) lets an operator invalidate every pod's seen state at once after
// a ClickHouse restore; legacy traj:seen:<project>:<scope> SETs are ignored.

const (
	// SeenAnyCommitter is the committer token for requests without a single
	// billing key (multi-trace requests) and for requests that are not billed
	// on stored bytes (events-mode, unknown-mode or no usage emitter). Such
	// requests commit their items right after the trajectory_items insert
	// succeeds, independent of any usage XADD. A hit committed by "*" is always
	// credited to the next request, and a "*" caller never gets ReuseSelf.
	SeenAnyCommitter = "*"

	// SeenDefaultReuseAge is the default bound on how old a reused item copy may be.
	SeenDefaultReuseAge = 24 * time.Hour
	// SeenMinReuseAge and SeenMaxReuseAge bound the configurable reuse age.
	// The lifecycle GC relies on the upper bound (see SeenContractKey).
	SeenMinReuseAge = time.Hour
	SeenMaxReuseAge = 48 * time.Hour

	// SeenEpochKey holds the store epoch. Changing it invalidates every pod's
	// pod-local and Redis seen state within seenEpochCacheTTL.
	SeenEpochKey = "traj:epoch"

	// SeenContractKey is written by collectors that run with dedup on. It tells
	// the lifecycle GC that the v2 bounded-reuse contract is in force and what
	// the reuse age is. It is written only by an explicit call, never as a side
	// effect of a check or a commit.
	SeenContractKey     = "traj:contract:v2"
	SeenContractTTL     = 24 * time.Hour
	SeenContractRefresh = time.Hour

	// SeenDefaultRedisMaxBytes and SeenDefaultRedisMaxFraction cap the Redis
	// memory the seen store may grow into. The seen store shares Redis with the
	// billing usage stream and quota counters, so once Redis used_memory is above
	// either limit the store stops writing (reads continue; the cost is only
	// lost cross-pod dedup, never correctness).
	SeenDefaultRedisMaxBytes    int64 = 256 << 20
	SeenDefaultRedisMaxFraction       = 0.7

	seenKeyPrefix      = "traj:seen:v2:"
	seenLegacyPrefix   = "traj:seen:"
	seenFlagRefresh    = "r"
	seenFlagUnbilled   = "u"
	seenEpochCacheTTL  = 5 * time.Second
	seenEpochRetry     = time.Second
	seenMemProbeTTL    = 10 * time.Second
	seenKeySlack       = time.Hour
	seenRedisTimeout   = 500 * time.Millisecond
	seenLogInterval    = 30 * time.Second
	seenScanCount      = 1000
	seenLocalEpoch     = "local"
	seenProbeProjectID = "__probe__"
)

// SeenClass is the classification of one item by SeenStore.Check.
type SeenClass uint8

const (
	SeenNew SeenClass = iota
	SeenReuseOther
	SeenReuseSelf
	SeenRefresh
	// SeenDuplicate is a repeat of an earlier item in the same Check call.
	SeenDuplicate
)

func (c SeenClass) String() string {
	switch c {
	case SeenNew:
		return "new"
	case SeenReuseOther:
		return "reuse_other"
	case SeenReuseSelf:
		return "reuse_self"
	case SeenRefresh:
		return "refresh"
	case SeenDuplicate:
		return "duplicate"
	default:
		return "unknown"
	}
}

// SeenDecision is the classification of one input item.
type SeenDecision struct {
	Item  Item
	Class SeenClass
	// Write reports whether the item must be written to trajectory_items.
	Write bool
	// supersede marks a New item whose only recent hit was an unbilled ("|u")
	// commit; committing it replaces that value.
	supersede bool
}

// SeenCheck is the result of SeenStore.Check, one decision per input item in
// input order.
type SeenCheck struct {
	Decisions []SeenDecision
	// RedisFailed reports that at least one Redis read failed and its items
	// were classed New (fail open).
	RedisFailed bool
	// EpochUnknown reports that the store epoch could not be read after it had
	// been known, so nothing was reused in this check.
	EpochUnknown bool
}

// Write returns the items that must be written to trajectory_items (New and
// Refresh decisions that need a fresh copy), each once.
func (c SeenCheck) Write() []Item {
	out := make([]Item, 0, len(c.Decisions))
	for _, d := range c.Decisions {
		if d.Write {
			out = append(out, d.Item)
		}
	}
	return out
}

// Count returns how many decisions have class cl.
func (c SeenCheck) Count(cl SeenClass) int {
	n := 0
	for _, d := range c.Decisions {
		if d.Class == cl {
			n++
		}
	}
	return n
}

// Bytes returns the content bytes of the decisions with class cl.
func (c SeenCheck) Bytes(cl SeenClass) int64 {
	var n int64
	for _, d := range c.Decisions {
		if d.Class == cl {
			n += int64(len(d.Item.Content))
		}
	}
	return n
}

// BilledItemBytes is the item content billed to this request: New plus
// ReuseSelf (a re-poll of the same billing key bills what the first poll did).
func (c SeenCheck) BilledItemBytes() int64 {
	return c.Bytes(SeenNew) + c.Bytes(SeenReuseSelf)
}

// CreditedItemBytes is the item content not billed to this request.
func (c SeenCheck) CreditedItemBytes() int64 {
	return c.Bytes(SeenReuseOther) + c.Bytes(SeenRefresh) + c.Bytes(SeenDuplicate)
}

// SeenStamp describes one commit.
type SeenStamp struct {
	// Committer is SeenCommitterToken(billing key), or SeenAnyCommitter.
	Committer string
	// At is the persisted_at stamp written to trajectory_items for these
	// items; it decides the generation. Zero means now.
	At time.Time
	// Unbilled marks a commit whose usage event is known to be a duplicate
	// event_id (its bytes will not be billed). Later checks class such hits New.
	Unbilled bool
}

// SeenCommitterToken returns the committer token for a billing key: the
// first 16 hex chars of sha1(key), or SeenAnyCommitter for an empty key.
func SeenCommitterToken(billingKey string) string {
	if billingKey == "" {
		return SeenAnyCommitter
	}
	sum := sha1.Sum([]byte(billingKey))
	return hex.EncodeToString(sum[:])[:16]
}

// SeenOptions configures NewSeenStoreWithOptions.
type SeenOptions struct {
	// LRUSize caps the pod-local cache (0 -> DefaultLRUSize).
	LRUSize int
	// MaxReuseAge bounds the age of a reused copy (0 -> SeenDefaultReuseAge,
	// clamped to [SeenMinReuseAge, SeenMaxReuseAge]).
	MaxReuseAge time.Duration
	// ProjectMaxReuseAge overrides MaxReuseAge for the __project__ scope
	// (0 -> MaxReuseAge; same clamp).
	ProjectMaxReuseAge time.Duration
	// RedisMaxBytes stops Redis writes while used_memory is above it
	// (0 -> SeenDefaultRedisMaxBytes, negative disables).
	RedisMaxBytes int64
	// RedisMaxFraction stops Redis writes while used_memory is above this
	// fraction of maxmemory (0 -> SeenDefaultRedisMaxFraction, negative disables).
	RedisMaxFraction float64
	// MemoryProbe reports Redis used_memory and maxmemory. nil -> INFO memory.
	// A probe error does not stop writes; it is counted and logged.
	MemoryProbe func(ctx context.Context) (used, maxBytes int64, err error)
	// Logger receives rate-limited trajectory_seen_* events (nil -> slog.Default()).
	Logger *slog.Logger
}

// SeenStats are cumulative counters for health reporting.
type SeenStats struct {
	RedisErrors      uint64
	BudgetSkips      uint64
	MemProbeErrors   uint64
	EpochUnavailable uint64
}

type lruEntry struct {
	key        ItemKey
	gen        int64  // generation of the newest known commit (never "now" on a read)
	value      string // committer value of that commit
	storeEpoch string
	projEpoch  uint32
}

// SeenStore provides 2-tier (pod-local LRU + optional shared Redis) seen
// state for trajectory items. rdb == nil gives an LRU-only store. Redis errors
// fail open: the affected items are classed New and written again.
type SeenStore struct {
	rdb      *redis.Client
	rSession time.Duration // generation length for session/trace scopes
	rProject time.Duration // generation length for __project__
	nowFn    func() time.Time
	log      *slog.Logger

	memProbe func(ctx context.Context) (used, maxBytes int64, err error)
	maxBytes int64
	maxFrac  float64

	mu        sync.Mutex
	cap       int
	ll        *list.List
	entries   map[ItemKey]*list.Element
	projEpoch map[string]uint32
	projCount map[string]int

	epochMu     sync.Mutex
	epoch       string
	epochKnown  bool
	epochAt     time.Time
	epochFailed bool
	epochFailAt time.Time

	memMu    sync.Mutex
	memAt    time.Time
	memValid bool
	memOver  bool

	logMu   sync.Mutex
	lastLog map[string]time.Time

	redisErr    atomic.Uint64
	budgetSkips atomic.Uint64
	probeErrs   atomic.Uint64
	epochUnav   atomic.Uint64
}

// NewSeenStore keeps the original constructor. sessionTTL and projectTTL are
// now the reuse ages of the session and __project__ scopes, clamped to
// [SeenMinReuseAge, SeenMaxReuseAge] (so the old 7-day project default becomes
// 48h). rdb may be nil (local-only mode).
func NewSeenStore(rdb *redis.Client, lruCap int, sessionTTL, projectTTL time.Duration) *SeenStore {
	if sessionTTL <= 0 {
		sessionTTL = DefaultSessionTTL
	}
	if projectTTL <= 0 {
		projectTTL = DefaultProjectTTL
	}
	return NewSeenStoreWithOptions(rdb, SeenOptions{
		LRUSize:            lruCap,
		MaxReuseAge:        sessionTTL,
		ProjectMaxReuseAge: projectTTL,
	})
}

// NewSeenStoreWithOptions creates a seen store v2. rdb may be nil (LRU only).
func NewSeenStoreWithOptions(rdb *redis.Client, opts SeenOptions) *SeenStore {
	lruCap := opts.LRUSize
	if lruCap <= 0 {
		lruCap = DefaultLRUSize
	}
	sessAge := clampSeenReuseAge(opts.MaxReuseAge)
	projAge := sessAge
	if opts.ProjectMaxReuseAge > 0 {
		projAge = clampSeenReuseAge(opts.ProjectMaxReuseAge)
	}
	maxBytes := opts.RedisMaxBytes
	if maxBytes == 0 {
		maxBytes = SeenDefaultRedisMaxBytes
	}
	maxFrac := opts.RedisMaxFraction
	if maxFrac == 0 {
		maxFrac = SeenDefaultRedisMaxFraction
	}
	logger := opts.Logger
	if logger == nil {
		logger = slog.Default()
	}
	s := &SeenStore{
		rdb:       rdb,
		rSession:  seenGenLength(sessAge),
		rProject:  seenGenLength(projAge),
		nowFn:     time.Now,
		log:       logger,
		memProbe:  opts.MemoryProbe,
		maxBytes:  maxBytes,
		maxFrac:   maxFrac,
		cap:       lruCap,
		ll:        list.New(),
		entries:   make(map[ItemKey]*list.Element, min(lruCap, 4096)),
		projEpoch: make(map[string]uint32),
		projCount: make(map[string]int),
		lastLog:   make(map[string]time.Time),
	}
	if s.memProbe == nil && rdb != nil {
		s.memProbe = func(ctx context.Context) (int64, int64, error) { return redisInfoMemory(ctx, rdb) }
	}
	return s
}

func clampSeenReuseAge(d time.Duration) time.Duration {
	if d <= 0 {
		d = SeenDefaultReuseAge
	}
	if d < SeenMinReuseAge {
		d = SeenMinReuseAge
	}
	if d > SeenMaxReuseAge {
		d = SeenMaxReuseAge
	}
	return d
}

// seenGenLength returns R = age/2 rounded down to whole seconds.
func seenGenLength(age time.Duration) time.Duration {
	return (age / 2).Truncate(time.Second)
}

func seenGen(t time.Time, r time.Duration) int64 {
	return t.Unix() / int64(r/time.Second)
}

func (s *SeenStore) now() time.Time {
	if s.nowFn != nil {
		return s.nowFn()
	}
	return time.Now()
}

func (s *SeenStore) genLen(scopeID string) time.Duration {
	if scopeID == ProjectScopeID {
		return s.rProject
	}
	return s.rSession
}

// MaxReuseAge returns the largest reuse age the store enforces (both scopes).
func (s *SeenStore) MaxReuseAge() time.Duration {
	return 2 * max(s.rSession, s.rProject)
}

// SeenKey returns the Redis key for one generation of a scope.
func SeenKey(projectID, storeEpoch, scopeID string, gen int64) string {
	return seenKeyPrefix + projectID + ":" + storeEpoch + ":" + scopeID + ":" + strconv.FormatInt(gen, 10)
}

func seenExpireAt(gen int64, r time.Duration) time.Time {
	return time.Unix((gen+4)*int64(r/time.Second), 0).Add(seenKeySlack)
}

func seenValue(committer string, refresh, unbilled bool) string {
	v := committer
	if refresh {
		v += "|" + seenFlagRefresh
	}
	if unbilled {
		v += "|" + seenFlagUnbilled
	}
	return v
}

func parseSeenValue(v string) (committer string, refresh, unbilled bool) {
	parts := strings.Split(v, "|")
	committer = parts[0]
	for _, f := range parts[1:] {
		switch f {
		case seenFlagRefresh:
			refresh = true
		case seenFlagUnbilled:
			unbilled = true
		}
	}
	return committer, refresh, unbilled
}

func normalizeCommitter(c string) string {
	c = strings.TrimSpace(c)
	if c == "" || strings.Contains(c, "|") {
		return SeenAnyCommitter
	}
	return c
}

// classifySeen classifies one item from its hits in the reuse window
// (generations g, g-1) and the refresh window (g-2, g-3).
func classifySeen(caller string, recent, older []string) (cl SeenClass, write, supersede bool) {
	billedRecent := false
	unbilledRecent := false
	selfRefresh, selfPlain := false, false
	for _, v := range recent {
		c, refresh, unbilled := parseSeenValue(v)
		if unbilled {
			unbilledRecent = true
			continue
		}
		billedRecent = true
		if caller != SeenAnyCommitter && c == caller {
			if refresh {
				selfRefresh = true
			} else {
				selfPlain = true
			}
		}
	}
	if billedRecent {
		switch {
		case selfPlain:
			return SeenReuseSelf, false, false
		case selfRefresh:
			return SeenRefresh, false, false
		default:
			return SeenReuseOther, false, false
		}
	}
	if unbilledRecent {
		return SeenNew, true, true
	}
	for _, v := range older {
		if _, _, unbilled := parseSeenValue(v); !unbilled {
			return SeenRefresh, true, false
		}
	}
	return SeenNew, true, false
}

// epochState returns the store epoch and whether Redis and the pod-local LRU
// may be used for reuse right now.
//   - rdb == nil: ("local", false, true).
//   - epoch read (or cached for 5 s): (epoch, true, true).
//   - never read and Redis unreachable: ("", false, true). The store behaves as
//     LRU-only for items this pod committed while Redis was unreachable.
//   - read before but unreadable now: ("", false, false). Nothing is reused.
func (s *SeenStore) epochState(ctx context.Context) (epoch string, redisOK, lruOK bool) {
	if s.rdb == nil {
		return seenLocalEpoch, false, true
	}
	now := s.now()
	s.epochMu.Lock()
	defer s.epochMu.Unlock()
	if s.epochKnown && !s.epochFailed && now.Sub(s.epochAt) < seenEpochCacheTTL && !now.Before(s.epochAt) {
		return s.epoch, true, true
	}
	if s.epochFailed && now.Sub(s.epochFailAt) < seenEpochRetry && !now.Before(s.epochFailAt) {
		return "", false, !s.epochKnown
	}
	rctx, cancel := context.WithTimeout(ctx, seenRedisTimeout)
	defer cancel()
	pipe := s.rdb.Pipeline()
	pipe.SetNX(rctx, SeenEpochKey, newSeenEpoch(), 0)
	get := pipe.Get(rctx, SeenEpochKey)
	_, err := pipe.Exec(rctx)
	v := ""
	if err == nil {
		v, err = get.Result()
	}
	if err == nil && v == "" {
		err = errors.New("empty store epoch")
	}
	if err != nil {
		s.epochFailed = true
		s.epochFailAt = now
		s.epochUnav.Add(1)
		s.redisErr.Add(1)
		s.logRateLimited("epoch", slog.LevelWarn, "trajectory_seen_redis_error",
			"op", "epoch", "err", err.Error(), "epoch_known", s.epochKnown)
		return "", false, !s.epochKnown
	}
	s.epoch = v
	s.epochKnown = true
	s.epochAt = now
	s.epochFailed = false
	return v, true, true
}

func newSeenEpoch() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	h := hex.EncodeToString(b[:])
	return h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:32]
}

// liveEntryLocked returns the LRU element for k if it belongs to the current
// store epoch and project epoch.
func (s *SeenStore) liveEntryLocked(k ItemKey, epoch string) (*list.Element, *lruEntry) {
	elem, ok := s.entries[k]
	if !ok {
		return nil, nil
	}
	ent := elem.Value.(*lruEntry)
	if ent.storeEpoch != epoch || ent.projEpoch != s.projEpoch[k.ProjectID] {
		return nil, nil
	}
	return elem, ent
}

// Check classifies items for a request whose committer token is committer.
// It is read-only against the seen state: it never writes an item and never
// touches a TTL (it may create the store epoch key once). Callers commit with
// CommitChecked only after trajectory_items has durably committed.
func (s *SeenStore) Check(ctx context.Context, items []Item, committer string) SeenCheck {
	res := SeenCheck{Decisions: make([]SeenDecision, len(items))}
	if s == nil || len(items) == 0 {
		for i, it := range items {
			res.Decisions[i] = SeenDecision{Item: it, Class: SeenNew, Write: true}
		}
		return res
	}
	caller := normalizeCommitter(committer)
	epoch, redisOK, lruOK := s.epochState(ctx)
	if !lruOK {
		res.EpochUnknown = true
	}
	lruAuthoritative := s.rdb == nil || !redisOK
	now := s.now()

	inSlice := make(map[ItemKey]struct{}, len(items))
	var misses []int
	s.mu.Lock()
	for i, it := range items {
		d := &res.Decisions[i]
		d.Item = it
		k := it.Key()
		if _, dup := inSlice[k]; dup {
			d.Class = SeenDuplicate
			continue
		}
		inSlice[k] = struct{}{}
		if !lruOK {
			d.Class, d.Write = SeenNew, true
			continue
		}
		if elem, ent := s.liveEntryLocked(k, epoch); ent != nil {
			g := seenGen(now, s.genLen(k.ScopeID))
			switch {
			case ent.gen >= g-1:
				_, _, unbilled := parseSeenValue(ent.value)
				if !unbilled || lruAuthoritative {
					s.ll.MoveToFront(elem)
					d.Class, d.Write, d.supersede = classifySeen(caller, []string{ent.value}, nil)
					continue
				}
			case ent.gen >= g-3 && lruAuthoritative:
				s.ll.MoveToFront(elem)
				d.Class, d.Write, d.supersede = classifySeen(caller, nil, []string{ent.value})
				continue
			}
		}
		if lruAuthoritative {
			d.Class, d.Write = SeenNew, true
			continue
		}
		misses = append(misses, i)
	}
	s.mu.Unlock()
	if s.rdb != nil && !redisOK && lruOK && len(items) > 0 {
		res.RedisFailed = true
	}
	if len(misses) == 0 {
		return res
	}

	type scopeKey struct{ projectID, scopeID string }
	groups := make(map[scopeKey][]int)
	order := make([]scopeKey, 0)
	for _, i := range misses {
		it := items[i]
		sk := scopeKey{it.ProjectID, it.ScopeID}
		if _, ok := groups[sk]; !ok {
			order = append(order, sk)
		}
		groups[sk] = append(groups[sk], i)
	}

	rctx, cancel := context.WithTimeout(ctx, seenRedisTimeout)
	defer cancel()
	pipe := s.rdb.Pipeline()
	cmds := make([][4]*redis.SliceCmd, len(order))
	gens := make([]int64, len(order))
	for gi, sk := range order {
		idx := groups[sk]
		fields := make([]string, len(idx))
		for j, i := range idx {
			fields[j] = items[i].ItemHash
		}
		g := seenGen(now, s.genLen(sk.scopeID))
		gens[gi] = g
		for back := 0; back < 4; back++ {
			cmds[gi][back] = pipe.HMGet(rctx, SeenKey(sk.projectID, epoch, sk.scopeID, g-int64(back)), fields...)
		}
	}
	_, pipeErr := pipe.Exec(rctx)

	s.mu.Lock()
	defer s.mu.Unlock()
	for gi, sk := range order {
		idx := groups[sk]
		var vals [4][]any
		failed := false
		for back := 0; back < 4; back++ {
			v, err := cmds[gi][back].Result()
			if err != nil || len(v) != len(idx) {
				failed = true
				if err == nil {
					err = errors.New("short HMGET reply")
				}
				if pipeErr == nil {
					pipeErr = err
				}
				break
			}
			vals[back] = v
		}
		if failed {
			s.redisErr.Add(1)
			res.RedisFailed = true
			s.logRateLimited("read", slog.LevelWarn, "trajectory_seen_redis_error", "op", "hmget", "err", pipeErr.Error())
			for _, i := range idx {
				res.Decisions[i].Class, res.Decisions[i].Write = SeenNew, true
			}
			continue
		}
		for j, i := range idx {
			var recent, older []string
			newestGen, newestVal, hit := int64(0), "", false
			for back := 0; back < 4; back++ {
				str, ok := vals[back][j].(string)
				if !ok || str == "" {
					continue
				}
				if !hit {
					newestGen, newestVal, hit = gens[gi]-int64(back), str, true
				}
				if back < 2 {
					recent = append(recent, str)
				} else {
					older = append(older, str)
				}
			}
			d := &res.Decisions[i]
			d.Class, d.Write, d.supersede = classifySeen(caller, recent, older)
			if hit {
				// Warm the LRU with the generation the hit was committed in, never now.
				s.mergeLocked(items[i].Key(), newestGen, newestVal, epoch, true)
			}
		}
	}
	return res
}

// CommitChecked records the Write decisions of check as committed by stamp.
// Call it only after those items' trajectory_items insert has committed.
// Refresh decisions are stored with the "|r" mark; New decisions that replace
// an unbilled commit overwrite it.
func (s *SeenStore) CommitChecked(ctx context.Context, check SeenCheck, stamp SeenStamp) {
	if s == nil {
		return
	}
	cs := make([]seenCommit, 0, len(check.Decisions))
	for _, d := range check.Decisions {
		if d.Write {
			cs = append(cs, seenCommit{item: d.Item, refresh: d.Class == SeenRefresh, supersede: d.supersede})
		}
	}
	s.commit(ctx, cs, stamp)
}

// Commit records items as committed by stamp (first committer wins). Use it
// for items that were not classified by Check.
func (s *SeenStore) Commit(ctx context.Context, items []Item, stamp SeenStamp) {
	if s == nil {
		return
	}
	cs := make([]seenCommit, len(items))
	for i, it := range items {
		cs[i] = seenCommit{item: it}
	}
	s.commit(ctx, cs, stamp)
}

type seenCommit struct {
	item      Item
	refresh   bool
	supersede bool
}

func (s *SeenStore) commit(ctx context.Context, cs []seenCommit, stamp SeenStamp) {
	if len(cs) == 0 {
		return
	}
	committer := normalizeCommitter(stamp.Committer)
	at := stamp.At
	if at.IsZero() {
		at = s.now()
	}
	epoch, redisOK, _ := s.epochState(ctx)

	seenInCall := make(map[ItemKey]struct{}, len(cs))
	uniq := cs[:0:0]
	for _, c := range cs {
		k := c.item.Key()
		if _, dup := seenInCall[k]; dup {
			continue
		}
		seenInCall[k] = struct{}{}
		uniq = append(uniq, c)
	}
	values := make([]string, len(uniq))
	gens := make([]int64, len(uniq))
	for i, c := range uniq {
		values[i] = seenValue(committer, c.refresh, stamp.Unbilled)
		gens[i] = seenGen(at, s.genLen(c.item.ScopeID))
	}
	winners := append([]string(nil), values...)
	fromRedis := false

	if redisOK && s.redisWritable(ctx) {
		rctx, cancel := context.WithTimeout(ctx, seenRedisTimeout)
		defer cancel()
		pipe := s.rdb.Pipeline()
		gets := make([]*redis.StringCmd, len(uniq))
		expired := make(map[string]struct{})
		for i, c := range uniq {
			key := SeenKey(c.item.ProjectID, epoch, c.item.ScopeID, gens[i])
			if c.supersede {
				pipe.HSet(rctx, key, c.item.ItemHash, values[i])
			} else {
				pipe.HSetNX(rctx, key, c.item.ItemHash, values[i])
			}
			gets[i] = pipe.HGet(rctx, key, c.item.ItemHash)
			if _, done := expired[key]; !done {
				expired[key] = struct{}{}
				pipe.ExpireAt(rctx, key, seenExpireAt(gens[i], s.genLen(c.item.ScopeID)))
			}
		}
		if _, err := pipe.Exec(rctx); err != nil && !errors.Is(err, redis.Nil) {
			s.redisErr.Add(1)
			s.logRateLimited("write", slog.LevelWarn, "trajectory_seen_redis_error", "op", "commit", "err", err.Error())
		} else {
			fromRedis = true
			for i, g := range gets {
				if v, err := g.Result(); err == nil && v != "" {
					winners[i] = v
				}
			}
		}
	}

	lruEpoch := epoch
	s.mu.Lock()
	for i, c := range uniq {
		s.mergeLocked(c.item.Key(), gens[i], winners[i], lruEpoch, fromRedis || c.supersede)
	}
	s.mu.Unlock()
}

// mergeLocked records a commit of k in generation gen. A newer generation
// replaces the entry; within the same generation the first committer wins
// unless authoritative (the value came from Redis, or replaces an unbilled
// commit) or the existing value is unbilled and the new one is not.
func (s *SeenStore) mergeLocked(k ItemKey, gen int64, value, storeEpoch string, authoritative bool) {
	pe := s.projEpoch[k.ProjectID]
	if elem, ok := s.entries[k]; ok {
		ent := elem.Value.(*lruEntry)
		if ent.storeEpoch != storeEpoch || ent.projEpoch != pe {
			if ent.projEpoch != pe {
				s.projCount[k.ProjectID]++
			}
			ent.gen, ent.value, ent.storeEpoch, ent.projEpoch = gen, value, storeEpoch, pe
			s.ll.MoveToFront(elem)
			return
		}
		switch {
		case gen > ent.gen:
			ent.gen, ent.value = gen, value
		case gen == ent.gen:
			_, _, oldUnbilled := parseSeenValue(ent.value)
			_, _, newUnbilled := parseSeenValue(value)
			if authoritative || (oldUnbilled && !newUnbilled) {
				ent.value = value
			}
		}
		s.ll.MoveToFront(elem)
		return
	}
	if s.ll.Len() >= s.cap {
		if oldest := s.ll.Back(); oldest != nil {
			s.removeLocked(oldest)
		}
	}
	s.entries[k] = s.ll.PushFront(&lruEntry{key: k, gen: gen, value: value, storeEpoch: storeEpoch, projEpoch: pe})
	s.projCount[k.ProjectID]++
}

func (s *SeenStore) removeLocked(elem *list.Element) {
	ent := elem.Value.(*lruEntry)
	s.ll.Remove(elem)
	delete(s.entries, ent.key)
	if ent.projEpoch == s.projEpoch[ent.key.ProjectID] {
		if n := s.projCount[ent.key.ProjectID] - 1; n > 0 {
			s.projCount[ent.key.ProjectID] = n
		} else {
			delete(s.projCount, ent.key.ProjectID)
		}
	}
}

// redisWritable reports whether Redis memory is under the seen-store budget.
// The answer is cached for seenMemProbeTTL. A probe error allows writes.
func (s *SeenStore) redisWritable(ctx context.Context) bool {
	if s.memProbe == nil || (s.maxBytes < 0 && s.maxFrac < 0) {
		return true
	}
	now := s.now()
	s.memMu.Lock()
	defer s.memMu.Unlock()
	if !s.memValid || now.Sub(s.memAt) >= seenMemProbeTTL || now.Before(s.memAt) {
		pctx, cancel := context.WithTimeout(ctx, seenRedisTimeout)
		used, maxMem, err := s.memProbe(pctx)
		cancel()
		s.memAt, s.memValid = now, true
		if err != nil {
			s.probeErrs.Add(1)
			s.memOver = false
			s.logRateLimited("memprobe", slog.LevelWarn, "trajectory_seen_redis_memory_probe_unavailable", "err", err.Error())
		} else {
			over := s.maxBytes > 0 && used > s.maxBytes
			if s.maxFrac > 0 && maxMem > 0 && float64(used) > s.maxFrac*float64(maxMem) {
				over = true
			}
			s.memOver = over
		}
	}
	if s.memOver {
		s.budgetSkips.Add(1)
		s.logRateLimited("budget", slog.LevelWarn, "trajectory_seen_redis_budget_exceeded",
			"max_bytes", s.maxBytes, "max_fraction", s.maxFrac)
		return false
	}
	return true
}

// redisInfoMemory reads used_memory and maxmemory from INFO memory.
func redisInfoMemory(ctx context.Context, rdb *redis.Client) (int64, int64, error) {
	txt, err := rdb.Info(ctx, "memory").Result()
	if err != nil {
		return 0, 0, err
	}
	return parseRedisInfoMemory(txt)
}

func parseRedisInfoMemory(txt string) (used, maxBytes int64, err error) {
	foundUsed := false
	for _, line := range strings.Split(txt, "\n") {
		line = strings.TrimSpace(line)
		name, val, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		switch name {
		case "used_memory":
			n, perr := strconv.ParseInt(val, 10, 64)
			if perr != nil {
				return 0, 0, fmt.Errorf("used_memory: %w", perr)
			}
			used, foundUsed = n, true
		case "maxmemory":
			n, perr := strconv.ParseInt(val, 10, 64)
			if perr != nil {
				return 0, 0, fmt.Errorf("maxmemory: %w", perr)
			}
			maxBytes = n
		}
	}
	if !foundUsed {
		return 0, 0, errors.New("INFO memory has no used_memory")
	}
	return used, maxBytes, nil
}

func (s *SeenStore) logRateLimited(kind string, level slog.Level, msg string, args ...any) {
	if s.log == nil {
		return
	}
	now := time.Now()
	s.logMu.Lock()
	last, seen := s.lastLog[kind]
	if seen && now.Sub(last) < seenLogInterval {
		s.logMu.Unlock()
		return
	}
	s.lastLog[kind] = now
	s.logMu.Unlock()
	s.log.Log(context.Background(), level, msg, append(args, "event", msg)...)
}

// InvalidateProject drops every pod-local entry of projectID in O(1) by
// bumping the project's epoch; stale entries are replaced lazily. It returns
// how many entries were invalidated. Wired to Redis `fi:project:invalidate`.
func (s *SeenStore) InvalidateProject(projectID string) int {
	if s == nil || projectID == "" {
		return 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	n := s.projCount[projectID]
	delete(s.projCount, projectID)
	s.projEpoch[projectID]++
	return n
}

// RedisErrors returns the cumulative number of Redis fail-open events.
func (s *SeenStore) RedisErrors() uint64 { return s.redisErr.Load() }

// Stats returns cumulative counters for health reporting.
func (s *SeenStore) Stats() SeenStats {
	return SeenStats{
		RedisErrors:      s.redisErr.Load(),
		BudgetSkips:      s.budgetSkips.Load(),
		MemProbeErrors:   s.probeErrs.Load(),
		EpochUnavailable: s.epochUnav.Load(),
	}
}

// CheckNew is the original read-only check, kept for existing callers. It
// classifies with SeenAnyCommitter: newItems are the items to write (New and
// Refresh), everything else is reported as reused.
func (s *SeenStore) CheckNew(ctx context.Context, items []Item) (newItems []Item, reusedCount int, reusedBytes int64) {
	if len(items) == 0 {
		return nil, 0, 0
	}
	res := s.Check(ctx, items, SeenAnyCommitter)
	for _, d := range res.Decisions {
		if d.Write {
			newItems = append(newItems, d.Item)
			continue
		}
		reusedCount++
		reusedBytes += int64(len(d.Item.Content))
	}
	return newItems, reusedCount, reusedBytes
}

// MarkCommitted is the original commit, kept for existing callers: it commits
// items as SeenAnyCommitter stamped now. Call it only after the
// trajectory_items insert has committed.
func (s *SeenStore) MarkCommitted(ctx context.Context, items []Item) {
	if s == nil || len(items) == 0 {
		return
	}
	s.Commit(ctx, items, SeenStamp{Committer: SeenAnyCommitter})
}

// FilterNew runs CheckNew followed by MarkCommitted for single-step callers
// and tests. Production pipelines check at convert time and commit only after
// ClickHouse commits `trajectory_items`.
func (s *SeenStore) FilterNew(ctx context.Context, items []Item) (newItems []Item, reusedCount int, reusedBytes int64) {
	newItems, reusedCount, reusedBytes = s.CheckNew(ctx, items)
	if len(newItems) > 0 {
		s.MarkCommitted(ctx, newItems)
	}
	return newItems, reusedCount, reusedBytes
}

// Forget removes items from the pod-local LRU and, best effort, from every
// generation still read in Redis, so that a failed trajectory_items write
// that fell back to inline makes later turns write the items again.
func (s *SeenStore) Forget(ctx context.Context, items []Item) {
	if s == nil || len(items) == 0 {
		return
	}
	s.mu.Lock()
	for _, it := range items {
		if elem, ok := s.entries[it.Key()]; ok {
			s.removeLocked(elem)
		}
	}
	s.mu.Unlock()

	if s.rdb == nil {
		return
	}
	epoch, redisOK, _ := s.epochState(ctx)
	if !redisOK {
		return
	}
	now := s.now()
	type scopeKey struct{ projectID, scopeID string }
	groups := make(map[scopeKey][]string)
	order := make([]scopeKey, 0)
	for _, it := range items {
		sk := scopeKey{it.ProjectID, it.ScopeID}
		if _, ok := groups[sk]; !ok {
			order = append(order, sk)
		}
		groups[sk] = append(groups[sk], it.ItemHash)
	}
	rctx, cancel := context.WithTimeout(ctx, seenRedisTimeout)
	defer cancel()
	pipe := s.rdb.Pipeline()
	for _, sk := range order {
		g := seenGen(now, s.genLen(sk.scopeID))
		for back := int64(0); back < 4; back++ {
			pipe.HDel(rctx, SeenKey(sk.projectID, epoch, sk.scopeID, g-back), groups[sk]...)
		}
	}
	if _, err := pipe.Exec(rctx); err != nil {
		s.redisErr.Add(1)
		s.logRateLimited("forget", slog.LevelWarn, "trajectory_seen_redis_error", "op", "forget", "err", err.Error())
	}
}

// StartupCheck pings Redis and runs one HMGET against the v2 layout, logging
// a WARN when either fails. It never changes seen state.
func (s *SeenStore) StartupCheck(ctx context.Context) error {
	if s == nil || s.rdb == nil {
		return nil
	}
	rctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	err := s.rdb.Ping(rctx).Err()
	if err == nil {
		err = s.rdb.HMGet(rctx, SeenKey(seenProbeProjectID, seenLocalEpoch, ProjectScopeID, 0), "probe").Err()
	}
	if err != nil {
		s.log.Warn("trajectory seen store Redis check failed; items will be written again until Redis answers",
			"event", "trajectory_seen_redis_unavailable", "err", err.Error())
	}
	return err
}

// SeenProjectKeyPatterns returns the SCAN MATCH patterns covering every seen
// key of projectID: the v2 layout and the legacy traj:seen:<project>:<scope>
// SETs. Erasure must clear both.
func SeenProjectKeyPatterns(projectID string) ([]string, error) {
	if strings.TrimSpace(projectID) == "" {
		return nil, errors.New("trajectory: empty project id")
	}
	if projectID == "v2" || strings.Contains(projectID, ":") {
		// "v2" would make the legacy pattern match every project's v2 keys.
		return nil, fmt.Errorf("trajectory: invalid project id %q for seen-key erasure", projectID)
	}
	p := escapeRedisGlob(projectID)
	return []string{seenKeyPrefix + p + ":*", seenLegacyPrefix + p + ":*"}, nil
}

func escapeRedisGlob(s string) string {
	var b strings.Builder
	for _, r := range s {
		switch r {
		case '*', '?', '[', ']', '\\', '^':
			b.WriteByte('\\')
		}
		b.WriteRune(r)
	}
	return b.String()
}

// EraseProjectSeenKeys removes every seen key of projectID (v2 and legacy)
// with SCAN MATCH and UNLINK in batches, never KEYS. It returns the number of
// keys removed.
func EraseProjectSeenKeys(ctx context.Context, rdb *redis.Client, projectID string) (int, error) {
	patterns, err := SeenProjectKeyPatterns(projectID)
	if err != nil {
		return 0, err
	}
	if rdb == nil {
		return 0, nil
	}
	removed := 0
	for _, pat := range patterns {
		var cursor uint64
		for {
			keys, next, err := rdb.Scan(ctx, cursor, pat, seenScanCount).Result()
			if err != nil {
				return removed, err
			}
			if len(keys) > 0 {
				n, err := rdb.Unlink(ctx, keys...).Result()
				if err != nil {
					return removed, err
				}
				removed += int(n)
			}
			cursor = next
			if cursor == 0 {
				break
			}
		}
	}
	return removed, nil
}

// EraseProject invalidates the project's pod-local entries and removes its
// Redis seen keys.
func (s *SeenStore) EraseProject(ctx context.Context, projectID string) (int, error) {
	if s == nil {
		return 0, nil
	}
	if _, err := SeenProjectKeyPatterns(projectID); err != nil {
		return 0, err
	}
	s.InvalidateProject(projectID)
	return EraseProjectSeenKeys(ctx, s.rdb, projectID)
}

// SeenContract is the value of SeenContractKey.
type SeenContract struct {
	Version            int   `json:"v"`
	MaxReuseAgeSeconds int64 `json:"max_reuse_age_s"`
	// KeyLifetimeSeconds is the longest a seen entry can stay readable after
	// its commit: 4 generations plus slack (2 * max_reuse_age + 1h).
	KeyLifetimeSeconds int64 `json:"key_lifetime_s"`
}

// WriteSeenContract sets SeenContractKey (EX SeenContractTTL) for a collector
// running with dedup on and reuse age maxReuseAge.
func WriteSeenContract(ctx context.Context, rdb *redis.Client, maxReuseAge time.Duration) error {
	if rdb == nil {
		return errors.New("trajectory: no Redis client for the seen contract key")
	}
	age := clampSeenReuseAge(maxReuseAge)
	body, err := json.Marshal(SeenContract{
		Version:            2,
		MaxReuseAgeSeconds: int64(age / time.Second),
		KeyLifetimeSeconds: int64((2*age + seenKeySlack) / time.Second),
	})
	if err != nil {
		return err
	}
	rctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	return rdb.Set(rctx, SeenContractKey, string(body), SeenContractTTL).Err()
}

// RunSeenContractKeeper writes the contract key now and every interval
// (0 -> SeenContractRefresh) until ctx ends. Start it only when dedup is on.
func RunSeenContractKeeper(ctx context.Context, rdb *redis.Client, maxReuseAge, interval time.Duration, log *slog.Logger) {
	if interval <= 0 {
		interval = SeenContractRefresh
	}
	write := func() {
		if err := WriteSeenContract(ctx, rdb, maxReuseAge); err != nil && ctx.Err() == nil && log != nil {
			log.Warn("trajectory seen contract key write failed",
				"event", "trajectory_seen_redis_error", "op", "contract", "err", err.Error())
		}
	}
	write()
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			write()
		}
	}
}
