package lifecycle

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
	"testing"
	"time"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/models"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

// A video that is reserved and settled through the real lifecycle leaves its
// tenant and global counters exactly as chat expects them: spend fields only,
// with the period TTL. Chat HGETALLs these hashes on every request.
func TestReviewSettledVideoKeepsBudgetCountersSpendOnly(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	ctx := context.Background()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "video"
	rc.Metadata["org_id"] = "org-a"
	rc.Metadata["auth_key_id"] = "key-a"
	rc.Metadata["key_type"] = "byok"
	global, err := json.Marshal([]video.BudgetReservation{{Global: true, Level: "global_org", Period: "monthly", Limit: 1000}})
	if err != nil {
		t.Fatal(err)
	}
	rc.Metadata["video_global_scopes"] = string(global)
	r, err := f.s.Accept(ctx, rc, provider.Request{Model: "byteplus/" + modelID, Prompt: "receipt sentinel", DurationSeconds: 5}, video.NewID(), []video.BudgetReservation{{Level: "org", Period: "monthly", Limit: 1000}})
	if err != nil {
		t.Fatal(err)
	}
	f.tick(t)
	f.clock.Advance(2 * time.Second)
	f.tick(t)
	j := f.job(t, r.Job.ID)
	if j.Status != video.StatusCompleted || j.SettlementState != video.SettlementSettled || j.SettledMicros <= 0 {
		t.Fatalf("job not settled: status=%s settlement=%s settled=%d", j.Status, j.SettlementState, j.SettledMicros)
	}
	start := budgetpkg.PeriodStart("monthly", j.CreatedAt).Format("2006-01-02")
	for _, key := range []string{f.prefix + "budget:org-a:org:" + start, f.prefix + "budget::global_org:" + start} {
		fields, err := f.redis.Redis().HGetAll(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		if len(fields) == 0 {
			t.Fatalf("counter %s missing", key)
		}
		for field := range fields {
			if field != "total" && !strings.HasPrefix(field, "model:") {
				t.Errorf("counter %s carries non-spend field %q", strings.TrimPrefix(key, f.prefix), field)
			}
		}
		if total, _ := strconv.ParseInt(fields["total"], 10, 64); total != j.SettledMicros {
			t.Errorf("counter %s total=%d, want settled %d", strings.TrimPrefix(key, f.prefix), total, j.SettledMicros)
		}
		ttl, err := f.redis.Redis().TTL(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		if ttl <= 0 || ttl > 35*24*time.Hour {
			t.Errorf("counter %s ttl=%s, want the 840h monthly period TTL", strings.TrimPrefix(key, f.prefix), ttl)
		}
	}
}
