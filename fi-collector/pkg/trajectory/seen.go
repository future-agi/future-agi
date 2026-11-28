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
	"os"
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
	// Commit is how the seen store records the item once its trajectory_items
	// insert has committed (MarkCommitted, MarkCommittedAt). It is not written
	// to ClickHouse. Batch keeps the first occurrence of an item, and with it
	// that occurrence's Commit. The zero value is a plain commit by
	// SeenAnyCommitter.
	Commit ItemCommit
}

// ItemCommit is the seen-store commit record an Item carries from the check
// that classified it to the commit after its trajectory_items insert.
// SeenCheck.CommitItems fills it in.
type ItemCommit struct {
	// Committer is SeenCommitterToken(billing key); "" means SeenAnyCommitter.
	Committer string
	// Refresh marks an item classed SeenRefresh: it is stored with "|r" so a
	// re-poll by the same committer is classed SeenRefresh again.
	Refresh bool
	// Unbilled marks an item whose request's usage event is known to be a
	// duplicate (its bytes are never billed). Such a commit records nothing,
	// so the next request that sends the item is billed for it.
	Unbilled bool
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
// without a single billing key), plus "|r" when the commit was a refresh.
// Every field is written with HSETNX, so the first committer of a generation
// always wins. HSETNX and EXPIREAT run in one MULTI/EXEC, so each key expires
// at (gen+4)*R + 1h and no read ever touches a TTL: every entry has a hard age
// bound. A commit whose usage event is a known duplicate (unbilled) records
// nothing.
//
// Classification of an item against the generations g (now) .. g-3:
//
//	ReuseOther  hit in g or g-1 by another committer or by "*": credited, not written
//	ReuseSelf   hit in g or g-1 by the same committer: billed as new, not written
//	            (so a re-poll of the same trace bills the same amount in either order)
//	Refresh     hit only in g-2 or g-3: a fresh copy is written and credited;
//	            a same-committer hit whose value carries "|r" is classed Refresh again
//	            (credited, not rewritten), so a re-poll of a refresh bills the same
//	New         no hit (missing or expired); a Redis error is New
//
// A copy reused at time t was recorded in generation g-1 or later, so its
// recorded commit time is newer than t - max_reuse_age. Callers pass the
// persisted_at stamp of the copy (MarkCommittedAt) so the recorded time is the
// stored one. The store epoch (Redis key traj:epoch) lets an operator
// invalidate every pod's seen state at once after a ClickHouse restore; while
// the epoch cannot be read nothing is reused. Legacy traj:seen:<project>:<scope>
// SETs are ignored.

const (
	// SeenAnyCommitter is the committer token for requests without a single
	// billing key (multi-trace requests) and for requests that are not billed
	// on stored bytes. A hit committed by "*" is always credited to the next
	// request, and a "*" caller never gets ReuseSelf.
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

	// SeenContractKey is a HASH written by every collector that runs with the
	// store active (SeenStore.Run): field = writer id, value = SeenContract
	// JSON with the store's reuse age and the write time. Its presence tells
	// the lifecycle GC that the v2 bounded-reuse contract is in force. The key
	// expires SeenContractTTL after the last write; each writer refreshes its
	// field every SeenContractRefresh, removes fields whose written_at is older
	// than SeenContractTTL, and removes its own field on shutdown. It is never
	// written as a side effect of a check or a commit.
	SeenContractKey     = "traj:contract:v2"
	SeenContractTTL     = 24 * time.Hour
	SeenContractRefresh = time.Hour

	// SeenDefaultRedisMaxFraction caps the Redis memory the seen store may grow
	// into, as a fraction of Redis maxmemory. The seen store shares Redis with
	// the billing usage stream and quota counters, so once used_memory is above
	// the limit the store stops writing (reads continue; the cost is only lost
	// cross-pod dedup, never correctness). An absolute cap
	// (SeenOptions.RedisMaxBytes) applies only when it is set explicitly,
	// because used_memory counts the whole instance, not the seen keys.
	SeenDefaultRedisMaxFraction = 0.7

	seenKeyPrefix      = "traj:seen:v2:"
	seenLegacyPrefix   = "traj:seen:"
	seenFlagRefresh    = "r"
	seenEpochCacheTTL  = 5 * time.Second
	seenEpochRetry     = time.Second
	seenMemProbeTTL    = 10 * time.Second
	seenKeySlack       = time.Hour
	seenRedisTimeout   = 500 * time.Millisecond
	seenLogInterval    = 30 * time.Second
	seenScanCount      = 1000
	seenLocalEpoch     = "local"
	seenProbeProjectID = "__probe__"
	seenContractVer    = 2
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
}

// SeenCheck is the result of SeenStore.Check, one decision per input item in
// input order.
type SeenCheck struct {
	Decisions []SeenDecision
	// RedisFailed reports that at least one Redis read failed and its items
	// were classed New (fail open).
	RedisFailed bool
	// EpochUnknown reports that the store epoch could not be read, so nothing
	// was reused in this check.
	EpochUnknown bool
	// committer is the normalized committer token the check classified for.
	committer string
}

// Write returns the items that must be written to trajectory_items (New and
// Refresh decisions that need a fresh copy), each once, as they were passed
// to Check.
func (c SeenCheck) Write() []Item {
	out := make([]Item, 0, len(c.Decisions))
	for _, d := range c.Decisions {
		if d.Write {
			out = append(out, d.Item)
		}
	}
	return out
}

// CommitItems returns Write() with each item's Commit filled in for the
// committer the check classified for (Refresh decisions carry Refresh). The
// items can travel in a Batch and be committed with MarkCommittedAt once the
// trajectory_items insert has committed. Set Commit.Unbilled on them before
// that commit if the request's usage event turns out to be a duplicate.
func (c SeenCheck) CommitItems() []Item {
	committer := c.committer
	if committer == "" {
		committer = SeenAnyCommitter
	}
	out := make([]Item, 0, len(c.Decisions))
	for _, d := range c.Decisions {
		if d.Write {
			it := d.Item
			it.Commit = ItemCommit{Committer: committer, Refresh: d.Class == SeenRefresh}
			out = append(out, it)
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
	// event_id (its bytes will not be billed). Nothing is recorded for it.
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
	// RedisMaxBytes, when positive, stops Redis writes while Redis used_memory
	// (the whole instance) is above it. 0 or negative: no absolute cap.
	RedisMaxBytes int64
	// RedisMaxFraction stops Redis writes while used_memory is above this
	// fraction of maxmemory (0 -> SeenDefaultRedisMaxFraction, negative
	// disables). It has no effect while Redis has no maxmemory.
	RedisMaxFraction float64
	// MemoryProbe reports Redis used_memory and maxmemory. nil -> INFO memory.
	// A probe error does not stop writes; it is counted and logged.
	MemoryProbe func(ctx context.Context) (used, maxBytes int64, err error)
	// Logger receives rate-limited trajectory_seen_* events (nil -> slog.Default()).
	Logger *slog.Logger
	// Now is the store clock (nil -> time.Now). It decides generations.
	Now func() time.Time
	// WriterID names this collector in SeenContractKey (empty -> host name).
	WriterID string
}

// SeenStats are cumulative counters for health reporting.
type SeenStats struct {
	RedisErrors       uint64
	BudgetSkips       uint64
	MemProbeErrors    uint64
	EpochUnavailable  uint64
	InvalidCommitters uint64
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
	writerID string

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
	badCommit   atomic.Uint64
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
	if maxBytes < 0 {
		maxBytes = 0
	}
	maxFrac := opts.RedisMaxFraction
	if maxFrac == 0 {
		maxFrac = SeenDefaultRedisMaxFraction
	}
	logger := opts.Logger
	if logger == nil {
		logger = slog.Default()
	}
	nowFn := opts.Now
	if nowFn == nil {
		nowFn = time.Now
	}
	writerID := strings.TrimSpace(opts.WriterID)
	if writerID == "" {
		writerID = defaultSeenWriterID()
	}
	s := &SeenStore{
		rdb:       rdb,
		rSession:  seenGenLength(sessAge),
		rProject:  seenGenLength(projAge),
		nowFn:     nowFn,
		log:       logger,
		writerID:  writerID,
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

func defaultSeenWriterID() string {
	if h, err := os.Hostname(); err == nil && strings.TrimSpace(h) != "" {
		return strings.TrimSpace(h)
	}
	var b [6]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "collector-" + strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	return "collector-" + hex.EncodeToString(b[:])
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

func seenValue(committer string, refresh bool) string {
	if refresh {
		return committer + "|" + seenFlagRefresh
	}
	return committer
}

func parseSeenValue(v string) (committer string, refresh bool) {
	parts := strings.Split(v, "|")
	committer = parts[0]
	for _, f := range parts[1:] {
		if f == seenFlagRefresh {
			refresh = true
		}
	}
	return committer, refresh
}

// normalizeCommitter maps "" to SeenAnyCommitter. A token that is not a
// valid committer (it contains "|") is also treated as SeenAnyCommitter, so
// that caller is always credited and never ReuseSelf; that is counted and
// logged because it means the caller passed something other than
// SeenCommitterToken output.
func (s *SeenStore) normalizeCommitter(c string) string {
	c = strings.TrimSpace(c)
	if c == "" {
		return SeenAnyCommitter
	}
	if strings.Contains(c, "|") {
		s.badCommit.Add(1)
		s.logRateLimited("committer", slog.LevelWarn, "trajectory_seen_invalid_committer",
			"reason", "token contains '|'", "len", len(c))
		return SeenAnyCommitter
	}
	return c
}

// classifySeen classifies one item from its hits in the reuse window
// (generations g, g-1) and the refresh window (g-2, g-3).
func classifySeen(caller string, recent, older []string) (cl SeenClass, write bool) {
	if len(recent) > 0 {
		selfRefresh, selfPlain := false, false
		for _, v := range recent {
			c, refresh := parseSeenValue(v)
			if caller != SeenAnyCommitter && c == caller {
				if refresh {
					selfRefresh = true
				} else {
					selfPlain = true
				}
			}
		}
		switch {
		case selfPlain:
			return SeenReuseSelf, false
		case selfRefresh:
			return SeenRefresh, false
		default:
			return SeenReuseOther, false
		}
	}
	if len(older) > 0 {
		return SeenRefresh, true
	}
	return SeenNew, true
}

// epochState returns the store epoch and whether seen state may be used for
// reuse right now.
//   - rdb == nil: ("local", true).
//   - epoch read (or cached for 5 s): (epoch, true).
//   - unreadable (never read since start, or not now): ("", false). Nothing is
//     reused and nothing is recorded, as SS-1 requires: a pod that has not
//     seen the current epoch cannot know whether its LRU is still valid.
func (s *SeenStore) epochState(ctx context.Context) (epoch string, ok bool) {
	if s.rdb == nil {
		return seenLocalEpoch, true
	}
	now := s.now()
	s.epochMu.Lock()
	defer s.epochMu.Unlock()
	if s.epochKnown && !s.epochFailed && now.Sub(s.epochAt) < seenEpochCacheTTL && !now.Before(s.epochAt) {
		return s.epoch, true
	}
	if s.epochFailed && now.Sub(s.epochFailAt) < seenEpochRetry && !now.Before(s.epochFailAt) {
		return "", false
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
		return "", false
	}
	s.epoch = v
	s.epochKnown = true
	s.epochAt = now
	s.epochFailed = false
	return v, true
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
// touches a TTL (it may create the store epoch key once). Callers commit
// (CommitChecked, or CommitItems + MarkCommittedAt) only after
// trajectory_items has durably committed.
func (s *SeenStore) Check(ctx context.Context, items []Item, committer string) SeenCheck {
	res := SeenCheck{Decisions: make([]SeenDecision, len(items)), committer: SeenAnyCommitter}
	if s == nil || len(items) == 0 {
		for i, it := range items {
			res.Decisions[i] = SeenDecision{Item: it, Class: SeenNew, Write: true}
		}
		return res
	}
	caller := s.normalizeCommitter(committer)
	res.committer = caller
	epoch, ok := s.epochState(ctx)
	if !ok {
		res.EpochUnknown = true
		res.RedisFailed = true
	}
	lruAuthoritative := s.rdb == nil
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
		if !ok {
			d.Class, d.Write = SeenNew, true
			continue
		}
		if elem, ent := s.liveEntryLocked(k, epoch); ent != nil {
			g := seenGen(now, s.genLen(k.ScopeID))
			switch {
			case ent.gen >= g-1:
				s.ll.MoveToFront(elem)
				d.Class, d.Write = classifySeen(caller, []string{ent.value}, nil)
				continue
			case ent.gen >= g-3 && lruAuthoritative:
				s.ll.MoveToFront(elem)
				d.Class, d.Write = classifySeen(caller, nil, []string{ent.value})
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
			d.Class, d.Write = classifySeen(caller, recent, older)
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
// Refresh decisions are stored with the "|r" mark.
func (s *SeenStore) CommitChecked(ctx context.Context, check SeenCheck, stamp SeenStamp) {
	if s == nil {
		return
	}
	committer := s.normalizeCommitter(stamp.Committer)
	cs := make([]seenCommit, 0, len(check.Decisions))
	for _, d := range check.Decisions {
		if d.Write {
			cs = append(cs, seenCommit{item: d.Item, committer: committer, refresh: d.Class == SeenRefresh, unbilled: stamp.Unbilled})
		}
	}
	s.commit(ctx, cs, stamp.At)
}

// Commit records items as committed by stamp (first committer wins), ignoring
// their Commit field. Use it for items that were not classified by Check.
func (s *SeenStore) Commit(ctx context.Context, items []Item, stamp SeenStamp) {
	if s == nil {
		return
	}
	committer := s.normalizeCommitter(stamp.Committer)
	cs := make([]seenCommit, len(items))
	for i, it := range items {
		cs[i] = seenCommit{item: it, committer: committer, unbilled: stamp.Unbilled}
	}
	s.commit(ctx, cs, stamp.At)
}

type seenCommit struct {
	item      Item
	committer string
	refresh   bool
	unbilled  bool
}

func (s *SeenStore) commit(ctx context.Context, cs []seenCommit, at time.Time) {
	if len(cs) == 0 {
		return
	}
	if at.IsZero() {
		at = s.now()
	}
	seenInCall := make(map[ItemKey]struct{}, len(cs))
	uniq := cs[:0:0]
	for _, c := range cs {
		if c.unbilled {
			// Never credited to a later request: record nothing.
			continue
		}
		k := c.item.Key()
		if _, dup := seenInCall[k]; dup {
			continue
		}
		seenInCall[k] = struct{}{}
		uniq = append(uniq, c)
	}
	if len(uniq) == 0 {
		return
	}
	epoch, ok := s.epochState(ctx)
	if !ok {
		// Without the epoch nothing recorded now could be trusted later.
		return
	}
	values := make([]string, len(uniq))
	gens := make([]int64, len(uniq))
	for i, c := range uniq {
		values[i] = seenValue(c.committer, c.refresh)
		gens[i] = seenGen(at, s.genLen(c.item.ScopeID))
	}
	winners := append([]string(nil), values...)
	fromRedis := false

	if s.rdb != nil && s.redisWritable(ctx) {
		rctx, cancel := context.WithTimeout(ctx, seenRedisTimeout)
		defer cancel()
		// MULTI/EXEC: a key never exists without its EXPIREAT.
		pipe := s.rdb.TxPipeline()
		gets := make([]*redis.StringCmd, len(uniq))
		expired := make(map[string]struct{})
		for i, c := range uniq {
			key := SeenKey(c.item.ProjectID, epoch, c.item.ScopeID, gens[i])
			pipe.HSetNX(rctx, key, c.item.ItemHash, values[i])
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

	s.mu.Lock()
	for i, c := range uniq {
		s.mergeLocked(c.item.Key(), gens[i], winners[i], epoch, fromRedis)
	}
	s.mu.Unlock()
}

// mergeLocked records a commit of k in generation gen. A newer generation
// replaces the entry; within the same generation the first committer wins
// unless the value is authoritative (it came from Redis).
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
		case gen == ent.gen && authoritative:
			ent.value = value
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
	if s.memProbe == nil || (s.maxBytes <= 0 && s.maxFrac < 0) {
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
// It does not touch Redis: while the project's Redis keys exist, a later
// check reads them again. Erasure must also remove those keys
// (EraseProjectSeenKeys).
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

// Stats returns cumulative counters for health reporting. Run logs them.
func (s *SeenStore) Stats() SeenStats {
	return SeenStats{
		RedisErrors:       s.redisErr.Load(),
		BudgetSkips:       s.budgetSkips.Load(),
		MemProbeErrors:    s.probeErrs.Load(),
		EpochUnavailable:  s.epochUnav.Load(),
		InvalidCommitters: s.badCommit.Load(),
	}
}

// CheckNew is the original read-only check, kept for existing callers. It
// classifies with SeenAnyCommitter: newItems are the items to write (New and
// Refresh), as passed in, and everything else is reported as reused.
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

// MarkCommitted commits items stamped now, each as its Commit field says
// (zero Commit: a plain commit by SeenAnyCommitter). Call it only after the
// trajectory_items insert has committed. Prefer MarkCommittedAt with the
// persisted_at stamp of the insert.
func (s *SeenStore) MarkCommitted(ctx context.Context, items []Item) {
	s.MarkCommittedAt(ctx, items, time.Time{})
}

// MarkCommittedAt commits items as each one's Commit field says, in the
// generation of at, the persisted_at stamp the trajectory_items insert wrote
// (zero: now). Call it only after that insert has committed. Items that come
// from a Batch keep the Commit of their first occurrence.
func (s *SeenStore) MarkCommittedAt(ctx context.Context, items []Item, at time.Time) {
	if s == nil || len(items) == 0 {
		return
	}
	cs := make([]seenCommit, len(items))
	for i, it := range items {
		cs[i] = seenCommit{
			item:      it,
			committer: s.normalizeCommitter(it.Commit.Committer),
			refresh:   it.Commit.Refresh,
			unbilled:  it.Commit.Unbilled,
		}
	}
	s.commit(ctx, cs, at)
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
// that fell back to inline makes later turns write the items again. Other
// pods' LRU entries are not reached: they keep reusing until their entry
// leaves the reuse window, an epoch change or an invalidation.
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
	epoch, ok := s.epochState(ctx)
	if !ok {
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
// a WARN when either fails. It never changes seen state. Run calls it.
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
		s.log.Warn("trajectory seen store Redis check failed; nothing is reused until Redis answers",
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

// SeenContract is the value of one writer's field in SeenContractKey.
type SeenContract struct {
	Version int `json:"v"`
	// Writer is the collector that wrote this field (also the field name).
	Writer string `json:"writer"`
	// WrittenAt is when the field was last written (unix seconds).
	WrittenAt int64 `json:"written_at"`
	// MaxReuseAgeSeconds is the largest reuse age the writer's store enforces
	// on any scope (SeenStore.MaxReuseAge).
	MaxReuseAgeSeconds int64 `json:"max_reuse_age_s"`
	// KeyLifetimeSeconds is the longest a seen entry can stay readable after
	// its commit: 4 generations plus slack (2 * max_reuse_age + 1h).
	KeyLifetimeSeconds int64 `json:"key_lifetime_s"`
}

// WriteContract writes this collector's field of SeenContractKey with the
// store's own reuse age, refreshes the key's TTL, and removes fields whose
// written_at is older than SeenContractTTL (collectors that stopped without
// removing theirs).
func (s *SeenStore) WriteContract(ctx context.Context) error {
	if s == nil || s.rdb == nil {
		return errors.New("trajectory: no Redis client for the seen contract key")
	}
	age := s.MaxReuseAge()
	now := s.now()
	body, err := json.Marshal(SeenContract{
		Version:            seenContractVer,
		Writer:             s.writerID,
		WrittenAt:          now.Unix(),
		MaxReuseAgeSeconds: int64(age / time.Second),
		KeyLifetimeSeconds: int64((2*age + seenKeySlack) / time.Second),
	})
	if err != nil {
		return err
	}
	rctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	pipe := s.rdb.TxPipeline()
	pipe.HSet(rctx, SeenContractKey, s.writerID, string(body))
	pipe.Expire(rctx, SeenContractKey, SeenContractTTL)
	all := pipe.HGetAll(rctx, SeenContractKey)
	if _, err := pipe.Exec(rctx); err != nil {
		return err
	}
	var stale []string
	for field, raw := range all.Val() {
		var c SeenContract
		if json.Unmarshal([]byte(raw), &c) != nil || now.Sub(time.Unix(c.WrittenAt, 0)) > SeenContractTTL {
			stale = append(stale, field)
		}
	}
	if len(stale) > 0 {
		return s.rdb.HDel(rctx, SeenContractKey, stale...).Err()
	}
	return nil
}

// Run does the store's background work until ctx ends: a startup Redis check
// (a WARN when it fails), this collector's SeenContractKey field written at
// once and every SeenContractRefresh (with Stats logged), and the field
// removed on shutdown. It returns at once for an LRU-only store. The server
// starts it only when the trajectory store is active.
func (s *SeenStore) Run(ctx context.Context) {
	s.run(ctx, SeenContractRefresh)
}

func (s *SeenStore) run(ctx context.Context, interval time.Duration) {
	if s == nil || s.rdb == nil {
		return
	}
	_ = s.StartupCheck(ctx)
	write := func() {
		if err := s.WriteContract(ctx); err != nil && ctx.Err() == nil {
			s.redisErr.Add(1)
			s.logRateLimited("contract", slog.LevelWarn, "trajectory_seen_redis_error", "op", "contract", "err", err.Error())
		}
	}
	write()
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			dctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
			_ = s.rdb.HDel(dctx, SeenContractKey, s.writerID).Err()
			cancel()
			return
		case <-t.C:
			write()
			st := s.Stats()
			s.log.Info("trajectory seen store stats", "event", "trajectory_seen_stats",
				"redis_errors", st.RedisErrors, "budget_skips", st.BudgetSkips,
				"mem_probe_errors", st.MemProbeErrors, "epoch_unavailable", st.EpochUnavailable,
				"invalid_committers", st.InvalidCommitters)
		}
	}
}
