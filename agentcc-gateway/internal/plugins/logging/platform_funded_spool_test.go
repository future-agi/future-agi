package logging

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A platform-funded key's request reaches the spool marked by the request's
// own metadata (key_billing, which the gateway writes only from the key's
// platform-set billing): the spooled batch delivers it with its cost but
// holds no spend line for it, so the budget seed never counts it.
func TestFlusherSpoolsAPlatformFundedRequestWithoutItsSpend(t *testing.T) {
	store := spoolForTest(t)
	wh := newSpoolWebhook(t, http.StatusBadRequest)
	f := spooledFlusher(t, wh.URL, store, 100)
	funded := spoolRecord("req-platform")
	funded.AuthKeyID = "key-platform"
	funded.Metadata = map[string]string{"cost": "4", "key_org_id": "org-a", "key_billing": "platform_funded"}
	f.Enqueue(funded)
	f.Enqueue(spoolRecord("req-1"))
	f.flush()

	all, err := store.All(context.Background())
	if err != nil || len(all) == 0 {
		t.Fatalf("spooled = %d, %v", len(all), err)
	}
	costs := map[string]float64{}
	var lines []string
	for _, st := range all {
		for _, raw := range st.Batch.Entries {
			var e struct {
				RequestID string            `json:"request_id"`
				Cost      float64           `json:"cost"`
				Metadata  map[string]string `json:"metadata"`
			}
			if err := json.Unmarshal(raw, &e); err != nil {
				t.Fatal(err)
			}
			costs[e.RequestID] = e.Cost
			if e.RequestID == "req-platform" && e.Metadata["key_billing"] != "platform_funded" {
				t.Errorf("the spooled entry's key_billing = %q, want platform_funded", e.Metadata["key_billing"])
			}
		}
		for _, l := range st.Batch.Spend {
			lines = append(lines, l.RequestID)
		}
	}
	if c, ok := costs["req-platform"]; !ok || c != 4 {
		t.Fatalf("spooled entries %v: want req-platform delivered with its cost 4", costs)
	}
	if _, ok := costs["req-1"]; !ok {
		t.Fatalf("spooled entries %v: want req-1", costs)
	}
	if len(lines) != 1 || lines[0] != "req-1" {
		t.Fatalf("spend lines = %v, want only req-1's", lines)
	}
}

// The marker survives every content mode: content mode none drops only the
// caller's own metadata keys, and key_billing is never one of them (request
// metadata drops every key_ name).
func TestKeyBillingSurvivesEveryContentMode(t *testing.T) {
	rec := TraceRecord{RequestID: "req-platform", Timestamp: time.Unix(1700000000, 0).UTC(), Model: "m-1",
		Metadata: map[string]string{"cost": "4", "key_org_id": "org-a", "key_billing": "platform_funded", "team": "t-1"}}
	if v1 := buildLogEntryV1(rec); v1.Metadata["key_billing"] != "platform_funded" {
		t.Errorf("v1 entry key_billing = %q, want platform_funded", v1.Metadata["key_billing"])
	}
	rc := models.AcquireRequestContext()
	rc.RequestID = rec.RequestID
	rc.CustomMetadataKeys = append(rc.CustomMetadataKeys, "team")
	st := rc.SnapshotV2()
	rc.Release()
	for _, mode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
		e := buildLogEntry(rec, &st, nil, LogEntryOptions{IncludeBodies: true, ContentMode: mode})
		if e.Metadata["key_billing"] != "platform_funded" {
			t.Errorf("content mode %s: key_billing = %q, want platform_funded", mode, e.Metadata["key_billing"])
		}
	}
}
