// Package trajectory implements content-addressed storage deduplication for
// multi-turn AI agent traces (Phase 1) alongside Phase 0 intra-span compaction
// and dedup-aware storage billing telemetry.
//
// Core invariant:
//   - Immutable message & tool-definition items are the exact byte ranges of
//     the original payload, hashed over those bytes (no re-encoding) and stored
//     verbatim once in `trajectory_items`; the span keeps the literal text
//     between them so readers rebuild the original string exactly.
//   - Static system prompts & tool schemas are scoped to "__project__" so they
//     deduplicate across every trace in a project.
//   - Conversation items are scoped to trace_session_id (falling back to
//     trace_id) so single-trace/session fetches hit PRIMARY KEY (project_id,
//     scope_id) directly.
//   - Cross-pod deduplication uses a 2-tier filter (fast pod-local LRU +
//     shared Redis seen-set) so customers are never double-billed when turns
//     land on different collector pods.
package trajectory

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Mode controls Phase 1 content-addressed trajectory extraction.
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

	// DefaultLRUSize bounds the pod-local seen-item cache (~40 MB RAM at 500k entries).
	DefaultLRUSize = 500_000
	// DefaultPreviewMaxBytes caps the inline preview kept in spans.input/output
	// for list views so list endpoints never query trajectory_items.
	DefaultPreviewMaxBytes = 512
	// DefaultSessionTTL is the Redis seen-set TTL for session/trace scopes.
	DefaultSessionTTL = 24 * time.Hour
	// DefaultProjectTTL is the Redis seen-set TTL for __project__ scopes.
	DefaultProjectTTL = 7 * 24 * time.Hour

	// Sentinel keys stored in attributes_extra when Phase 0 compaction removes
	// duplicate promoted input/output attributes. Readers pop these and
	// re-inject the original attribute from spans.input / spans.output.
	SentinelPromotedInputKey   = "__fi_promoted_input_key"
	SentinelPromotedInputJSON  = "__fi_promoted_input_json"
	SentinelPromotedOutputKey  = "__fi_promoted_output_key"
	SentinelPromotedOutputJSON = "__fi_promoted_output_json"
)

// Config controls Phase 0 intra-span compaction, Phase 1 trajectory dedup, and
// dedup-aware storage billing. Zero value is completely disabled (legacy behavior).
type Config struct {
	// CompactSpanAttrs enables Phase 0 sentinel compaction (removing duplicate
	// input.value/output.value/gen_ai.prompt copies from attributes_extra once
	// promoted to spans.input/output).
	CompactSpanAttrs bool `yaml:"compact_span_attrs"`
	// Mode controls Phase 1 trajectory dedup: "off" (default), "canary", "on".
	Mode Mode `yaml:"dedup_mode"`
	// CanaryProjects is the lowercase project UUID allowlist when Mode == "canary".
	CanaryProjects map[string]struct{} `yaml:"-"`
	// CanaryProjectList is the YAML form of CanaryProjects.
	CanaryProjectList []string `yaml:"canary_projects"`
	// LRUSize is the per-pod local seen-item cache cap (0 -> DefaultLRUSize).
	LRUSize int `yaml:"lru_size"`
	// PreviewMaxBytes caps inline previews kept in spans.input/output (0 -> DefaultPreviewMaxBytes).
	PreviewMaxBytes int `yaml:"preview_max_bytes"`
	// BillDedupedBytes switches observe_add billing amount from raw OTLP bytes
	// to stored_bytes (lean spans + newly stored unique trajectory items).
	// When false (default), raw bytes are billed while stored_bytes / saved bytes
	// are emitted in observe_add.properties whenever trajectory dedup is active.
	BillDedupedBytes bool `yaml:"bill_deduped_bytes"`
	// SessionTTL and ProjectTTL control Redis cross-pod seen-set expiration.
	SessionTTL time.Duration `yaml:"session_ttl"`
	ProjectTTL time.Duration `yaml:"project_ttl"`
}

// Normalize fills defaults and materializes CanaryProjects from CanaryProjectList.
func (c Config) Normalize() Config {
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
	if c.SessionTTL <= 0 {
		c.SessionTTL = DefaultSessionTTL
	}
	if c.ProjectTTL <= 0 {
		c.ProjectTTL = DefaultProjectTTL
	}
	if len(c.CanaryProjectList) > 0 {
		if c.CanaryProjects == nil {
			c.CanaryProjects = make(map[string]struct{}, len(c.CanaryProjectList))
		}
		for _, p := range c.CanaryProjectList {
			p = strings.ToLower(strings.TrimSpace(p))
			if p != "" {
				c.CanaryProjects[p] = struct{}{}
			}
		}
	}
	return c
}

// Validate checks that Mode is one of off/canary/on.
func (c Config) Validate() error {
	switch c.Mode {
	case "", ModeOff, ModeCanary, ModeOn:
		return nil
	default:
		return fmt.Errorf("trajectory: invalid FI_TRAJECTORY_DEDUP_MODE %q (want off, canary, or on)", c.Mode)
	}
}

// Active reports whether Phase 1 dedup can run for at least one project.
func (c Config) Active() bool {
	return c.Mode == ModeOn || (c.Mode == ModeCanary && len(c.CanaryProjects) > 0)
}

// EnabledForProject reports whether Phase 1 dedup should run for projectID.
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

// ConfigFromEnv applies FI_COMPACT_SPAN_ATTRS, FI_TRAJECTORY_DEDUP_MODE,
// FI_TRAJECTORY_DEDUP_PROJECTS, FI_TRAJECTORY_LRU_SIZE, and
// FI_BILL_DEDUPED_BYTES on top of base.
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
		cfg.CanaryProjects = make(map[string]struct{})
		for _, part := range strings.Split(v, ",") {
			p := strings.ToLower(strings.TrimSpace(part))
			if p != "" {
				cfg.CanaryProjects[p] = struct{}{}
			}
		}
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
		cfg.BillDedupedBytes = b
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
