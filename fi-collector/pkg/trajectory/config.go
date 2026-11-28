// Package trajectory implements content-addressed storage deduplication for
// multi-turn AI agent traces and dedup-aware storage billing telemetry.
//
// Core invariant:
//   - Immutable message & tool-definition items are hashed over canonical JSON
//     (recursively sorted keys) and stored verbatim once in `trajectory_items`.
//   - Static system prompts & tool schemas are scoped to "__project__" so they
//     deduplicate across every trace in a project.
//   - Conversation items are scoped to trace_session_id (falling back to
//     trace_id) so single-trace/session fetches hit PRIMARY KEY (project_id,
//     scope_id) directly.
//   - Cross-pod deduplication uses a 2-tier filter (fast pod-local LRU +
//     shared Redis seen-set) so customers are never double-billed when turns
//     land on different collector pods.
//   - A project dedups only while its Gate is open: the ClickHouse schema
//     matches the 040 contract, the topology is supported, the operator key
//     is present and no recent items insert failed. Otherwise every row is
//     written exactly as with dedup off.
package trajectory

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// Mode controls content-addressed trajectory extraction.
type Mode string

const (
	ModeOff    Mode = "off"
	ModeCanary Mode = "canary"
	ModeOn     Mode = "on"

	// ProjectScopeID is the scope_id for static project-wide items (system
	// prompts and tool definitions).
	ProjectScopeID = "__project__"

	// ProjectHashPrefix prefixes item_hash entries in spans.*_item_hashes that
	// resolve under ProjectScopeID rather than spans.item_scope_id.
	ProjectHashPrefix = "p:"

	// EncodingInline marks spans whose full input/output live inline on `spans`.
	EncodingInline = "inline"
	// EncodingItemsV1 marks spans whose full input/output hydrate from `trajectory_items`.
	EncodingItemsV1 = "items_v1"

	// DefaultLRUSize bounds the pod-local seen-item cache (about 120 MB of RAM
	// at 500k entries: roughly 230 B per entry with the key strings, list
	// element and map bucket).
	DefaultLRUSize = 500_000
	// DefaultPreviewMaxBytes caps the inline preview kept in spans.input/output
	// for list views so list endpoints never query trajectory_items.
	DefaultPreviewMaxBytes = 512

	// DefaultMaxReuseAge is how long after an item copy was persisted a later
	// span may point at it instead of writing it again. Lifecycle GC relies
	// on this bound, so it is capped by MaxReuseAgeCap.
	DefaultMaxReuseAge = 24 * time.Hour
	// MinMaxReuseAge and MaxReuseAgeCap bound max_reuse_age.
	MinMaxReuseAge = time.Hour
	MaxReuseAgeCap = 48 * time.Hour

	// DefaultSessionTTL and DefaultProjectTTL are the Redis seen-set TTLs for
	// session/trace scopes and __project__ scopes. Neither may exceed
	// MaxReuseAgeCap: a seen entry that outlives the reuse window would let a
	// span point at an item copy that lifecycle GC may already have removed.
	DefaultSessionTTL = DefaultMaxReuseAge
	DefaultProjectTTL = DefaultMaxReuseAge

	// DefaultItemsMaxRetries is the retry budget of the trajectory_items
	// insert (attempts = retries + 1). Falling back inline is always safe, so
	// the items insert fails fast instead of doubling the drain's retry time.
	DefaultItemsMaxRetries = 1
	// MaxItemsMaxRetries caps items_max_retries.
	MaxItemsMaxRetries = 5
	// DefaultItemsInsertTimeout is the per-attempt deadline of the
	// trajectory_items insert. The ClickHouse insert_quorum_timeout is derived
	// from it (one second less), so ClickHouse reports a quorum outcome before
	// the client gives up on the request.
	DefaultItemsInsertTimeout = 5 * time.Second
	// MinItemsInsertTimeout and MaxItemsInsertTimeout bound items_insert_timeout.
	MinItemsInsertTimeout = 2 * time.Second
	MaxItemsInsertTimeout = 60 * time.Second

	// DefaultSeenRedisMaxMB is the Redis memory budget for the shared seen
	// store, enforced by the seen store (the same Redis holds billing streams
	// and quota counters).
	DefaultSeenRedisMaxMB = 64

	// SeenRedisOn and SeenRedisOff are the values of seen_redis.
	SeenRedisOn  = "on"
	SeenRedisOff = "off"

	// Sentinel keys stored in attributes_extra when compaction removes the
	// duplicate promoted input/output attribute of an extracted span. Readers
	// pop these and re-inject the original attribute from spans.input /
	// spans.output.
	SentinelPromotedInputKey   = "__fi_promoted_input_key"
	SentinelPromotedInputJSON  = "__fi_promoted_input_json"
	SentinelPromotedOutputKey  = "__fi_promoted_output_key"
	SentinelPromotedOutputJSON = "__fi_promoted_output_json"
)

// Config controls trajectory dedup and dedup-aware storage billing. The zero
// value is completely disabled (legacy behavior).
//
// A Config is read-only once Normalize has returned it: Normalize never writes
// to the receiver's maps, and the server and converter share the normalized
// value across goroutines.
type Config struct {
	// CompactSpanAttrs is no longer supported on its own: compaction of the
	// promoted attribute happens only inside dedup, for extracted spans.
	// Validate rejects true so an old configuration fails loudly instead of
	// silently stripping attributes no reader restores.
	CompactSpanAttrs bool `yaml:"compact_span_attrs"`
	// Mode controls dedup: "off" (default), "canary", "on".
	Mode Mode `yaml:"dedup_mode"`
	// CanaryProjects is the lowercase project UUID allowlist when Mode ==
	// "canary". Normalize builds it from CanaryProjectList (plus any entries
	// already present) into a new map.
	CanaryProjects map[string]struct{} `yaml:"-"`
	// CanaryProjectList is the YAML form of CanaryProjects.
	// FI_TRAJECTORY_DEDUP_PROJECTS replaces it.
	CanaryProjectList []string `yaml:"canary_projects"`
	// LRUSize is the per-pod local seen-item cache cap (0 -> DefaultLRUSize).
	LRUSize int `yaml:"lru_size"`
	// PreviewMaxBytes caps inline previews kept in spans.input/output (0 -> DefaultPreviewMaxBytes).
	PreviewMaxBytes int `yaml:"preview_max_bytes"`
	// BillDedupedBytes chooses the observe_add amount for requests whose spans
	// were deduplicated: stored bytes after dedup ("deduped_storage") or raw
	// OTLP bytes ("raw_shadow"). Unset means true whenever Mode is canary or
	// on; an explicit false is the billing kill switch, which bills raw bytes
	// while dedup keeps storing. With Mode off it is ignored. Read it through
	// BillsDedupedBytes.
	BillDedupedBytes *bool `yaml:"bill_deduped_bytes"`
	// SessionTTL and ProjectTTL control Redis cross-pod seen-set expiration
	// (0 -> the default; above MaxReuseAgeCap is rejected).
	SessionTTL time.Duration `yaml:"session_ttl"`
	ProjectTTL time.Duration `yaml:"project_ttl"`
	// ItemsMaxRetries is the trajectory_items insert retry budget
	// (0 -> DefaultItemsMaxRetries, at most MaxItemsMaxRetries).
	ItemsMaxRetries int `yaml:"items_max_retries"`
	// ItemsInsertTimeout is the per-attempt deadline of the trajectory_items
	// insert (0 -> DefaultItemsInsertTimeout).
	ItemsInsertTimeout time.Duration `yaml:"items_insert_timeout"`
	// MaxReuseAge bounds how old a persisted item copy may be and still be
	// pointed at (0 -> DefaultMaxReuseAge; MinMaxReuseAge..MaxReuseAgeCap).
	MaxReuseAge time.Duration `yaml:"max_reuse_age"`
	// SeenRedis is "on" (default) or "off". Off keeps the seen store in the
	// pod-local LRU only (single-collector installs).
	SeenRedis string `yaml:"seen_redis"`
	// SeenRedisMaxMB is the Redis memory budget of the shared seen store
	// (0 -> DefaultSeenRedisMaxMB).
	SeenRedisMaxMB int `yaml:"seen_redis_max_mb"`

	normalized bool
}

// Normalize fills defaults and builds CanaryProjects from CanaryProjectList.
// It never mutates the receiver: the returned CanaryProjects is a new map.
func (c Config) Normalize() Config {
	if c.normalized {
		return c
	}
	if c.Mode == "" {
		c.Mode = ModeOff
	}
	c.Mode = Mode(strings.ToLower(strings.TrimSpace(string(c.Mode))))
	if c.LRUSize <= 0 {
		c.LRUSize = DefaultLRUSize
	}
	if c.PreviewMaxBytes <= 0 {
		c.PreviewMaxBytes = DefaultPreviewMaxBytes
	}
	if c.MaxReuseAge <= 0 {
		c.MaxReuseAge = DefaultMaxReuseAge
	}
	if c.SessionTTL <= 0 {
		c.SessionTTL = DefaultSessionTTL
	}
	if c.ProjectTTL <= 0 {
		c.ProjectTTL = DefaultProjectTTL
	}
	if c.ItemsMaxRetries <= 0 {
		c.ItemsMaxRetries = DefaultItemsMaxRetries
	}
	if c.ItemsInsertTimeout <= 0 {
		c.ItemsInsertTimeout = DefaultItemsInsertTimeout
	}
	c.SeenRedis = strings.ToLower(strings.TrimSpace(c.SeenRedis))
	if c.SeenRedis == "" {
		c.SeenRedis = SeenRedisOn
	}
	if c.SeenRedisMaxMB <= 0 {
		c.SeenRedisMaxMB = DefaultSeenRedisMaxMB
	}
	projects := make(map[string]struct{}, len(c.CanaryProjectList)+len(c.CanaryProjects))
	for p := range c.CanaryProjects {
		if p = strings.ToLower(strings.TrimSpace(p)); p != "" {
			projects[p] = struct{}{}
		}
	}
	for _, p := range c.CanaryProjectList {
		if p = strings.ToLower(strings.TrimSpace(p)); p != "" {
			projects[p] = struct{}{}
		}
	}
	c.CanaryProjects = projects
	c.CanaryProjectList = append([]string(nil), c.CanaryProjectList...)
	c.normalized = true
	return c
}

// Normalized reports whether c was returned by Normalize.
func (c Config) Normalized() bool { return c.normalized }

// RequireDormant refuses every trajectory setting that turns the store on.
// The store ships disabled in this release: its enable path (attribute
// handling, billing, retention, reader coverage) is not yet safe, so a stray
// env var or config block must stop the collector at startup instead of
// silently writing pointer rows or compacted attributes. An explicit
// bill_deduped_bytes=false (the kill switch) is not an enable setting.
func (c Config) RequireDormant() error {
	billOn := c.BillDedupedBytes != nil && *c.BillDedupedBytes
	if (c.Mode != "" && c.Mode != ModeOff) || c.CompactSpanAttrs || billOn ||
		len(c.CanaryProjectList) > 0 || len(c.CanaryProjects) > 0 {
		return fmt.Errorf("trajectory store is disabled in this release: unset FI_TRAJECTORY_DEDUP_MODE, " +
			"FI_TRAJECTORY_DEDUP_PROJECTS, FI_COMPACT_SPAN_ATTRS, FI_BILL_DEDUPED_BYTES and the trajectory config block")
	}
	return nil
}

// Validate checks every setting. It accepts both raw and normalized configs.
func (c Config) Validate() error {
	switch Mode(strings.ToLower(strings.TrimSpace(string(c.Mode)))) {
	case "", ModeOff, ModeCanary, ModeOn:
	default:
		return fmt.Errorf("trajectory: invalid FI_TRAJECTORY_DEDUP_MODE %q (want off, canary, or on)", c.Mode)
	}
	if c.CompactSpanAttrs {
		return fmt.Errorf("trajectory: compact_span_attrs / FI_COMPACT_SPAN_ATTRS is no longer supported; attributes are compacted only inside trajectory dedup (FI_TRAJECTORY_DEDUP_MODE)")
	}
	if c.LRUSize < 0 || c.PreviewMaxBytes < 0 {
		return fmt.Errorf("trajectory: lru_size and preview_max_bytes must not be negative")
	}
	if c.MaxReuseAge != 0 && (c.MaxReuseAge < MinMaxReuseAge || c.MaxReuseAge > MaxReuseAgeCap) {
		return fmt.Errorf("trajectory: max_reuse_age %s is outside %s..%s", c.MaxReuseAge, MinMaxReuseAge, MaxReuseAgeCap)
	}
	for name, ttl := range map[string]time.Duration{"session_ttl": c.SessionTTL, "project_ttl": c.ProjectTTL} {
		if ttl < 0 || ttl > MaxReuseAgeCap {
			return fmt.Errorf("trajectory: %s %s must be between 0 (default) and %s", name, ttl, MaxReuseAgeCap)
		}
	}
	if c.ItemsMaxRetries < 0 || c.ItemsMaxRetries > MaxItemsMaxRetries {
		return fmt.Errorf("trajectory: items_max_retries %d is outside 0..%d", c.ItemsMaxRetries, MaxItemsMaxRetries)
	}
	if c.ItemsInsertTimeout != 0 && (c.ItemsInsertTimeout < MinItemsInsertTimeout || c.ItemsInsertTimeout > MaxItemsInsertTimeout) {
		return fmt.Errorf("trajectory: items_insert_timeout %s is outside %s..%s", c.ItemsInsertTimeout, MinItemsInsertTimeout, MaxItemsInsertTimeout)
	}
	switch strings.ToLower(strings.TrimSpace(c.SeenRedis)) {
	case "", SeenRedisOn, SeenRedisOff:
	default:
		return fmt.Errorf("trajectory: invalid FI_TRAJECTORY_SEEN_REDIS %q (want on or off)", c.SeenRedis)
	}
	if c.SeenRedisMaxMB < 0 {
		return fmt.Errorf("trajectory: seen_redis_max_mb must not be negative")
	}
	return nil
}

// Active reports whether dedup can run for at least one project.
func (c Config) Active() bool {
	return c.Mode == ModeOn || (c.Mode == ModeCanary && len(c.CanaryProjects) > 0)
}

// EnabledForProject reports whether dedup is configured for projectID. The
// converter additionally requires the Gate to be open.
func (c Config) EnabledForProject(projectID string) bool {
	switch c.Mode {
	case ModeOn:
		return true
	case ModeCanary:
		if projectID == "" || len(c.CanaryProjects) == 0 {
			return false
		}
		_, ok := c.CanaryProjects[strings.ToLower(strings.TrimSpace(projectID))]
		return ok
	default:
		return false
	}
}

// BillsDedupedBytes reports whether deduplicated requests bill stored bytes.
// It is true only with Mode canary or on, unless BillDedupedBytes is an
// explicit false (the kill switch).
func (c Config) BillsDedupedBytes() bool {
	mode := Mode(strings.ToLower(strings.TrimSpace(string(c.Mode))))
	if mode != ModeCanary && mode != ModeOn {
		return false
	}
	return c.BillDedupedBytes == nil || *c.BillDedupedBytes
}

// SeenRedisEnabled reports whether the seen store shares Redis across pods.
func (c Config) SeenRedisEnabled() bool {
	return strings.ToLower(strings.TrimSpace(c.SeenRedis)) != SeenRedisOff
}

// NewSeenStoreFor builds the seen store cfg asks for: shared across pods
// through rdb, or pod-local when seen_redis is off or rdb is nil.
func NewSeenStoreFor(cfg Config, rdb *redis.Client) *SeenStore {
	if !cfg.SeenRedisEnabled() {
		rdb = nil
	}
	return NewSeenStore(rdb, cfg.LRUSize, cfg.SessionTTL, cfg.ProjectTTL)
}

// ConfigFromEnv applies the FI_TRAJECTORY_* environment, FI_BILL_DEDUPED_BYTES
// and FI_COMPACT_SPAN_ATTRS on top of base, validates, and normalizes.
// FI_TRAJECTORY_DEDUP_PROJECTS replaces the YAML canary list.
func ConfigFromEnv(base Config, getenv func(string) string) (Config, error) {
	cfg := base
	if v := strings.TrimSpace(getenv("FI_COMPACT_SPAN_ATTRS")); v != "" {
		b, err := parseBool(v)
		if err != nil {
			return cfg, fmt.Errorf("FI_COMPACT_SPAN_ATTRS: %w", err)
		}
		cfg.CompactSpanAttrs = b
	}
	if v := strings.TrimSpace(getenv("FI_TRAJECTORY_DEDUP_MODE")); v != "" {
		cfg.Mode = Mode(strings.ToLower(v))
	}
	if v := strings.TrimSpace(getenv("FI_TRAJECTORY_DEDUP_PROJECTS")); v != "" {
		var list []string
		for _, part := range strings.Split(v, ",") {
			if p := strings.ToLower(strings.TrimSpace(part)); p != "" {
				list = append(list, p)
			}
		}
		cfg.CanaryProjectList = list
		cfg.CanaryProjects = nil
	}
	if v := strings.TrimSpace(getenv("FI_TRAJECTORY_LRU_SIZE")); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n <= 0 {
			return cfg, fmt.Errorf("FI_TRAJECTORY_LRU_SIZE must be a positive integer")
		}
		cfg.LRUSize = n
	}
	if v := strings.TrimSpace(getenv("FI_BILL_DEDUPED_BYTES")); v != "" {
		b, err := parseBool(v)
		if err != nil {
			return cfg, fmt.Errorf("FI_BILL_DEDUPED_BYTES: %w", err)
		}
		cfg.BillDedupedBytes = &b
	}
	if v := strings.TrimSpace(getenv("FI_TRAJECTORY_ITEMS_MAX_RETRIES")); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return cfg, fmt.Errorf("FI_TRAJECTORY_ITEMS_MAX_RETRIES must be an integer")
		}
		cfg.ItemsMaxRetries = n
	}
	for _, d := range []struct {
		env    string
		target *time.Duration
	}{
		{"FI_TRAJECTORY_ITEMS_INSERT_TIMEOUT", &cfg.ItemsInsertTimeout},
		{"FI_TRAJECTORY_MAX_REUSE_AGE", &cfg.MaxReuseAge},
	} {
		if v := strings.TrimSpace(getenv(d.env)); v != "" {
			parsed, err := time.ParseDuration(v)
			if err != nil || parsed <= 0 {
				return cfg, fmt.Errorf("%s must be a positive duration such as 5s or 24h", d.env)
			}
			*d.target = parsed
		}
	}
	if v := strings.TrimSpace(getenv("FI_TRAJECTORY_SEEN_REDIS")); v != "" {
		cfg.SeenRedis = strings.ToLower(v)
	}
	if v := strings.TrimSpace(getenv("FI_TRAJECTORY_SEEN_REDIS_MAX_MB")); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n <= 0 {
			return cfg, fmt.Errorf("FI_TRAJECTORY_SEEN_REDIS_MAX_MB must be a positive integer")
		}
		cfg.SeenRedisMaxMB = n
	}
	if err := cfg.Validate(); err != nil {
		return cfg, err
	}
	return cfg.Normalize(), nil
}

func parseBool(v string) (bool, error) {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "true", "1", "yes", "on":
		return true, nil
	case "false", "0", "no", "off":
		return false, nil
	default:
		return false, fmt.Errorf("invalid boolean %q", v)
	}
}
