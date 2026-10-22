package logging

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// What a plugin records with rc.SetLogEvidence reaches
// the log entry as evidence.<name>, agent.identity and the top-level
// eval_export, in every content mode, and the entry validates against
// the log-entry-v2 schema.
func TestLogEntryCarriesEvidenceSidecar(t *testing.T) {
	identity := map[string]any{
		"sponsor":     map[string]any{"user_id": "u_1", "org_id": "org_1"},
		"actor_chain": []any{map[string]any{"agent_id": "researcher", "run_id": "run_R2"}},
		"source":      "runtime",
	}
	for _, mode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
		t.Run(string(mode), func(t *testing.T) {
			rec, rc := goldenRequest(t)
			rc.Privacy = models.Privacy{ContentMode: mode, Source: models.PrivacySourceOrg}
			rc.SetLogEvidence("budget_hold", map[string]any{"hold_id": "bh_1", "tightest": "key=0.42", "enforced": true, "levels": 2, "held_usd": 0.05})
			rc.SetLogEvidence("envelope", map[string]any{"run_id": "run_R2", "state": "warn", "dimension": "usd", "used": 0.8})
			rc.SetLogEvidence("cache", map[string]any{"cache_read_tokens": 1024, "uncached_input_tokens": 796})
			rc.SetLogEvidence("tool_schema", map[string]any{"bytes": 4096, "est_tokens": 1024, "tools": 12})
			rc.SetLogEvidence("overhead", map[string]any{"total_ms": 3.2, "auth_ms": 0.4})
			rc.SetLogEvidence("identity", identity)
			rc.SetLogEvidence("eval_export", "forbidden")
			rc.SetLogEvidence("cache", map[string]any{"cache_read_tokens": 2048}) // a later write replaces
			rc.SetLogEvidence("bad", func() {})                                   // does not encode: dropped

			st := rc.SnapshotV2()
			entry := buildLogEntry(rec, &st, rc.Response.Usage, DefaultLogEntryOptions())
			got := toJSONMap(t, entry)
			if errs := schematest.Validate(t, "log-entry-v2.schema.json", got); len(errs) > 0 {
				t.Fatalf("the entry fails log-entry-v2.schema.json:\n%s", strings.Join(errs, "\n"))
			}
			ev, _ := got["evidence"].(map[string]any)
			for _, name := range []string{"budget_hold", "envelope", "cache", "tool_schema", "overhead"} {
				if _, ok := ev[name]; !ok {
					t.Errorf("evidence.%s missing: %v", name, ev)
				}
			}
			if len(ev) != 5 {
				t.Errorf("evidence has %d entries, want 5 (identity, eval_export and the dropped value stay out): %v", len(ev), ev)
			}
			if c, _ := ev["cache"].(map[string]any); c["cache_read_tokens"] != float64(2048) || len(c) != 1 {
				t.Errorf("evidence.cache = %v, want the later write", ev["cache"])
			}
			agent, _ := got["agent"].(map[string]any)
			if _, ok := agent["identity"].(map[string]any); !ok {
				t.Errorf("agent.identity missing: %v", agent)
			}
			if got["eval_export"] != "forbidden" {
				t.Errorf("eval_export = %v, want forbidden", got["eval_export"])
			}
		})
	}
}

// With no evidence recorded the entry carries none of the three, and an
// identity alone still makes an agent object.
func TestLogEntryEvidenceAbsentAndIdentityOnly(t *testing.T) {
	rec, rc := goldenRequest(t)
	st := rc.SnapshotV2()
	got := toJSONMap(t, buildLogEntry(rec, &st, rc.Response.Usage, DefaultLogEntryOptions()))
	for _, k := range []string{"evidence", "eval_export"} {
		if _, ok := got[k]; ok {
			t.Errorf("%s present with no evidence recorded", k)
		}
	}
	if agent, _ := got["agent"].(map[string]any); agent["identity"] != nil {
		t.Errorf("agent.identity present with no evidence recorded")
	}

	bare := models.AcquireRequestContext()
	t.Cleanup(bare.Release)
	bare.SetLogEvidence("identity", json.RawMessage(`{"actor_chain":[],"source":"key_owner"}`))
	st = bare.SnapshotV2()
	e := buildLogEntry(TraceRecord{RequestID: "r"}, &st, nil, DefaultLogEntryOptions())
	if e.Agent == nil || string(e.Agent.Identity) != `{"actor_chain":[],"source":"key_owner"}` {
		t.Fatalf("identity alone: agent %+v", e.Agent)
	}
}
