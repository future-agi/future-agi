package trajectory

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

func envOf(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

// Normalize used to write CanaryProjectList into the receiver's map, which the
// server shares across requests.
func TestNormalizeDoesNotMutateReceiver(t *testing.T) {
	shared := map[string]struct{}{"aaaa": {}}
	cfg := Config{Mode: ModeCanary, CanaryProjects: shared, CanaryProjectList: []string{"BBBB", " cccc "}}
	n := cfg.Normalize()
	if len(shared) != 1 {
		t.Fatalf("Normalize wrote into the receiver's map: %v", shared)
	}
	if _, ok := shared["bbbb"]; ok {
		t.Fatal("Normalize wrote into the receiver's map")
	}
	for _, p := range []string{"aaaa", "bbbb", "cccc"} {
		if !n.EnabledForProject(p) {
			t.Fatalf("normalized config lost project %s: %v", p, n.CanaryProjects)
		}
	}
	n.CanaryProjects["dddd"] = struct{}{}
	if _, ok := shared["dddd"]; ok {
		t.Fatal("normalized map aliases the receiver's map")
	}
	if !n.Normalized() || cfg.Normalized() {
		t.Fatal("only the returned config is marked normalized")
	}
	again := n.Normalize()
	if !again.Normalized() || len(again.CanaryProjects) != len(n.CanaryProjects) {
		t.Fatal("Normalize of a normalized config must be a no-op")
	}
}

func TestConfigFromEnvProjectsReplaceYAML(t *testing.T) {
	yamlCfg := Config{Mode: ModeCanary, CanaryProjectList: []string{"aaaa"}}
	cfg, err := ConfigFromEnv(yamlCfg, envOf(map[string]string{"FI_TRAJECTORY_DEDUP_PROJECTS": "BBBB"}))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.EnabledForProject("aaaa") {
		t.Fatal("FI_TRAJECTORY_DEDUP_PROJECTS must replace the YAML list, not merge into it")
	}
	if !cfg.EnabledForProject("bbbb") {
		t.Fatal("env project missing")
	}
	// Without the env the YAML list is kept.
	cfg, err = ConfigFromEnv(yamlCfg, envOf(nil))
	if err != nil || !cfg.EnabledForProject("aaaa") {
		t.Fatalf("YAML list lost: %v %v", cfg.CanaryProjects, err)
	}
}

func TestConfigRejectsCompactWithoutDedup(t *testing.T) {
	for _, mode := range []string{"off", "canary", "on", ""} {
		_, err := ConfigFromEnv(Config{}, envOf(map[string]string{"FI_COMPACT_SPAN_ATTRS": "true", "FI_TRAJECTORY_DEDUP_MODE": mode}))
		if err == nil || !strings.Contains(err.Error(), "no longer supported") {
			t.Fatalf("mode %q: FI_COMPACT_SPAN_ATTRS=true must be rejected, got %v", mode, err)
		}
		if err := (Config{CompactSpanAttrs: true, Mode: Mode(mode)}).Validate(); err == nil {
			t.Fatalf("mode %q: compact_span_attrs=true in YAML must be rejected", mode)
		}
	}
	if _, err := ConfigFromEnv(Config{}, envOf(map[string]string{"FI_COMPACT_SPAN_ATTRS": "false"})); err != nil {
		t.Fatalf("an explicit false stays accepted: %v", err)
	}
}

func TestConfigBillDefaultsOnWithDedup(t *testing.T) {
	f, tr := false, true
	for _, tc := range []struct {
		name string
		mode string
		env  string
		yaml *bool
		want bool
	}{
		{"on/unset", "on", "", nil, true},
		{"on/env true", "on", "true", nil, true},
		{"on/env false", "on", "false", nil, false},
		{"on/yaml false", "on", "", &f, false},
		{"on/yaml true", "on", "", &tr, true},
		{"on/env false over yaml true", "on", "false", &tr, false},
		{"canary/unset", "canary", "", nil, true},
		{"canary/env true", "canary", "true", nil, true},
		{"canary/env false", "canary", "false", nil, false},
		{"canary/yaml false", "canary", "", &f, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			env := map[string]string{"FI_TRAJECTORY_DEDUP_MODE": tc.mode, "FI_TRAJECTORY_DEDUP_PROJECTS": "aaaa"}
			if tc.env != "" {
				env["FI_BILL_DEDUPED_BYTES"] = tc.env
			}
			cfg, err := ConfigFromEnv(Config{BillDedupedBytes: tc.yaml}, envOf(env))
			if err != nil {
				t.Fatal(err)
			}
			if got := cfg.BillsDedupedBytes(); got != tc.want {
				t.Fatalf("BillsDedupedBytes = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestConfigBillIgnoredWhenDedupOff(t *testing.T) {
	for _, env := range []map[string]string{
		{"FI_BILL_DEDUPED_BYTES": "true"},
		{"FI_BILL_DEDUPED_BYTES": "true", "FI_TRAJECTORY_DEDUP_MODE": "off"},
		{},
	} {
		cfg, err := ConfigFromEnv(Config{}, envOf(env))
		if err != nil {
			t.Fatal(err)
		}
		if cfg.BillsDedupedBytes() || cfg.Active() {
			t.Fatalf("%v: billing flag must be ignored with dedup off", env)
		}
	}
}

func TestConfigRejectsMaxReuseAgeAboveCap(t *testing.T) {
	for _, bad := range []map[string]string{
		{"FI_TRAJECTORY_MAX_REUSE_AGE": "49h"},
		{"FI_TRAJECTORY_MAX_REUSE_AGE": "30m"},
		{"FI_TRAJECTORY_MAX_REUSE_AGE": "soon"},
	} {
		if _, err := ConfigFromEnv(Config{}, envOf(bad)); err == nil {
			t.Fatalf("%v must be rejected", bad)
		}
	}
	for _, bad := range []Config{{SessionTTL: MaxReuseAgeCap + time.Second}, {ProjectTTL: 7 * 24 * time.Hour}} {
		if err := bad.Validate(); err == nil {
			t.Fatalf("%+v: a seen TTL above the cap must be rejected", bad)
		}
	}
	cfg, err := ConfigFromEnv(Config{}, envOf(map[string]string{"FI_TRAJECTORY_MAX_REUSE_AGE": "48h"}))
	if err != nil || cfg.MaxReuseAge != MaxReuseAgeCap {
		t.Fatalf("the cap itself is allowed: %v %v", cfg.MaxReuseAge, err)
	}
	cfg = Config{}.Normalize()
	if cfg.MaxReuseAge != DefaultMaxReuseAge || cfg.ProjectTTL > MaxReuseAgeCap || cfg.SessionTTL > MaxReuseAgeCap {
		t.Fatalf("defaults out of bounds: %+v", cfg)
	}
}

func TestConfigItemsInsertKnobs(t *testing.T) {
	cfg := Config{}.Normalize()
	if cfg.ItemsMaxRetries != DefaultItemsMaxRetries || cfg.ItemsInsertTimeout != DefaultItemsInsertTimeout {
		t.Fatalf("defaults: %d %s", cfg.ItemsMaxRetries, cfg.ItemsInsertTimeout)
	}
	cfg, err := ConfigFromEnv(Config{}, envOf(map[string]string{"FI_TRAJECTORY_ITEMS_MAX_RETRIES": "3", "FI_TRAJECTORY_ITEMS_INSERT_TIMEOUT": "8s"}))
	if err != nil || cfg.ItemsMaxRetries != 3 || cfg.ItemsInsertTimeout != 8*time.Second {
		t.Fatalf("env knobs: %+v %v", cfg, err)
	}
	for _, bad := range []map[string]string{
		{"FI_TRAJECTORY_ITEMS_MAX_RETRIES": "9"},
		{"FI_TRAJECTORY_ITEMS_MAX_RETRIES": "-1"},
		{"FI_TRAJECTORY_ITEMS_INSERT_TIMEOUT": "500ms"},
		{"FI_TRAJECTORY_ITEMS_INSERT_TIMEOUT": "2m"},
		{"FI_TRAJECTORY_SEEN_REDIS": "maybe"},
		{"FI_TRAJECTORY_SEEN_REDIS_MAX_MB": "0"},
	} {
		if _, err := ConfigFromEnv(Config{}, envOf(bad)); err == nil {
			t.Fatalf("%v must be rejected", bad)
		}
	}
}

// TestConfigSeenRedisOffUsesLocalOnly: seen_redis=off builds a pod-local seen
// store; nothing is written to Redis even when a client is available.
func TestConfigSeenRedisOffUsesLocalOnly(t *testing.T) {
	cfg, err := ConfigFromEnv(Config{}, envOf(map[string]string{"FI_TRAJECTORY_DEDUP_MODE": "on", "FI_TRAJECTORY_SEEN_REDIS": "off"}))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.SeenRedisEnabled() {
		t.Fatal("seen_redis=off must disable the shared seen store")
	}
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer rdb.Close()
	store := NewSeenStoreFor(cfg, rdb)
	items := []Item{{ProjectID: "11111111-1111-4111-8111-111111111111", ScopeID: "s", ItemHash: strings.Repeat("a", 32), Content: "x"}}
	store.MarkCommitted(context.Background(), items)
	if keys := mr.Keys(); len(keys) != 0 {
		t.Fatalf("seen_redis=off wrote Redis keys: %v", keys)
	}
	if newItems, reused, _ := store.CheckNew(context.Background(), items); len(newItems) != 0 || reused != 1 {
		t.Fatal("the pod-local LRU must still remember committed items")
	}
	on, _ := ConfigFromEnv(Config{}, envOf(map[string]string{"FI_TRAJECTORY_DEDUP_MODE": "on"}))
	NewSeenStoreFor(on, rdb).MarkCommitted(context.Background(), items)
	if len(mr.Keys()) == 0 {
		t.Fatal("seen_redis on (default) must share through Redis, or the test above is vacuous")
	}
}

func TestRequireDormantAcceptsDefaultsAndKillSwitch(t *testing.T) {
	f := false
	for _, ok := range []Config{{}, {Mode: ModeOff}, {BillDedupedBytes: &f}, Config{}.Normalize()} {
		if err := ok.RequireDormant(); err != nil {
			t.Fatalf("%+v must be dormant-compatible: %v", ok, err)
		}
	}
	tr := true
	for _, bad := range []Config{{Mode: ModeOn}, {Mode: ModeCanary}, {BillDedupedBytes: &tr}, {CanaryProjectList: []string{"a"}}, {CompactSpanAttrs: true}} {
		if err := bad.RequireDormant(); err == nil {
			t.Fatalf("%+v must be refused while dormant", bad)
		}
	}
}
