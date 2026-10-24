package logging

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Django stores a log entry's top-level routing_strategy in
// AgentccRequestLog.routing_strategy (log_ingestion.py _log_row), which the
// cost breakdown groups by (analytics.py get_cost_breakdown) and the CSV
// export lists (export.py). The gateway records the router's strategy as the
// routing_strategy metadata key (server/handlers.go, chat_nonstream.go), and
// the entry carries it at the top level.
func TestLogEntryCarriesTheRoutingStrategy(t *testing.T) {
	rec := TraceRecord{RequestID: "req_routed", Timestamp: time.Unix(1700000000, 0).UTC(), Model: "gpt-4o",
		Provider: "openai", StatusCode: 200,
		Metadata: map[string]string{"routing_strategy": "least-latency", "cost": "0.1"}}

	if v1 := toJSONMap(t, buildLogEntryV1(rec)); v1["routing_strategy"] != "least-latency" {
		t.Errorf("v1 entry routing_strategy = %v; want least-latency", v1["routing_strategy"])
	}
	rc := models.AcquireRequestContext()
	rc.RequestID = rec.RequestID
	rc.CustomMetadataKeys = append(rc.CustomMetadataKeys, "team")
	st := rc.SnapshotV2()
	rc.Release()
	// The key is the gateway's (server/caller_metadata.go reserves it), so
	// content mode none, which drops the caller's keys, keeps it.
	for _, mode := range []models.ContentMode{models.ContentModeFull, models.ContentModeNone} {
		e := toJSONMap(t, buildLogEntry(rec, &st, nil, LogEntryOptions{IncludeBodies: true, ContentMode: mode}))
		if e["routing_strategy"] != "least-latency" {
			t.Errorf("content mode %s: routing_strategy = %v; want least-latency", mode, e["routing_strategy"])
		}
		if errs := schematest.Validate(t, "log-entry-v2.schema.json", e); len(errs) > 0 {
			t.Errorf("content mode %s: entry fails log-entry-v2.schema.json: %v", mode, errs)
		}
	}

	unrouted := rec
	unrouted.Metadata = map[string]string{"cost": "0.1"}
	if v1 := toJSONMap(t, buildLogEntryV1(unrouted)); hasKey(v1, "routing_strategy") {
		t.Errorf("an entry the router did not pick has routing_strategy %v; want none", v1["routing_strategy"])
	}
}
