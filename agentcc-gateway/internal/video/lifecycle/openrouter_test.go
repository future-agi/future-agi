package lifecycle

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

const orModel = "bytedance/seedance-1-5-pro"

func setupOpenRouter(t *testing.T, script videotest.Script) *fixture {
	t.Helper()
	script.Capabilities = capability.OpenRouter()
	if len(script.Submit.Body) == 0 && script.Submit.Status == 0 {
		script.Submit = videotest.JSONReply(provider.SubmitResult{ProviderJobID: "gen-vid-1", ProviderState: "pending", Normalized: provider.StateQueued})
	}
	return setupProvider(t, script, "openrouter", config.VideoProviderConfig{Enabled: true, Models: []string{orModel}, Region: "global", AccountRef: "openrouter-test", TariffRevision: capability.OpenRouterRevision, Limits: config.VideoProviderLimits{SubmitRPM: 1000, PollQPS: 1000, MaxActiveTasks: 100}, Deadlines: config.VideoProviderDeadlines{Submit: time.Second, Read: time.Second, Run: time.Hour, Reconcile: time.Second}, Poll: config.VideoProviderPoll{BaseInterval: time.Second}})
}
func (f *fixture) acceptOpenRouter(t *testing.T) *video.VideoJob {
	t.Helper()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "video"
	rc.Metadata["org_id"] = "org-a"
	rc.Metadata["auth_key_id"] = "key-a"
	rc.Metadata["key_type"] = "byok"
	off := false
	r, e := f.s.Accept(context.Background(), rc, provider.Request{Model: "openrouter/" + orModel, Prompt: "synthetic prompt sentinel", DurationSeconds: 4, Resolution: "480p", AspectRatio: "16:9", Audio: &off}, video.NewID(), []video.BudgetReservation{{Level: "org", Period: "total", Limit: 1000}})
	if e != nil {
		t.Fatal(e)
	}
	return r.Job
}
func orCompleted(cost *float64) provider.Observation {
	o := provider.Observation{Normalized: provider.StateCompleted, ProviderState: "completed", Outputs: []provider.OutputRef{{ContentType: "video/mp4"}}}
	if cost != nil {
		o.Usage = &provider.Usage{Lines: []provider.UsageLine{{Unit: provider.USD, Quantity: *cost, Source: provider.UsageProviderReported, Currency: "USD"}}}
	}
	return o
}
func (f *fixture) runToTerminal(t *testing.T, id string) *video.VideoJob {
	t.Helper()
	for i := 0; i < 6; i++ {
		f.tick(t)
		f.clock.Advance(6 * time.Second)
	}
	return f.job(t, id)
}

// The reservation is the pinned catalog upper bound; settlement replaces it
// with the USD cost OpenRouter reported, to the microdollar.
func TestOpenRouter_SettlesReportedUSDCost(t *testing.T) {
	cost := 0.0461
	f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(orCompleted(&cost))}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.acceptOpenRouter(t)
	if j.EstimateUnit != provider.USD || j.ReservedMicros != 55444 || j.TariffRevision != capability.OpenRouterRevision || j.RateUSDPerMillion != 1e6 {
		t.Fatalf("estimate unit=%s reserved=%d revision=%s rate=%v", j.EstimateUnit, j.ReservedMicros, j.TariffRevision, j.RateUSDPerMillion)
	}
	j = f.runToTerminal(t, j.ID)
	if j.Status != video.StatusCompleted || j.SettlementState != video.SettlementSettled || j.SettledMicros != 46100 || j.Cost != 0.0461 {
		t.Fatalf("status=%s settlement=%s settled=%d cost=%v", j.Status, j.SettlementState, j.SettledMicros, j.Cost)
	}
	if len(j.Artifacts) != 1 || j.Artifacts[0].State != video.ArtifactAvailable {
		t.Fatalf("%+v", j.Artifacts)
	}
	total, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	if total < 0.04609 || total > 0.04611 {
		t.Fatalf("budget spend %v, want reported 0.0461", total)
	}
	resp := j.ToStatusResponse()
	if c, ok := resp["cost"].(map[string]any); !ok || c["usd"] != 0.0461 {
		t.Fatalf("%v", resp["cost"])
	}
}

// A null or missing usage.cost is unknown: the reservation stays held and the
// job is reported unsettled instead of being settled at zero.
func TestOpenRouter_NullCostIsUnknownNotZero(t *testing.T) {
	f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(orCompleted(nil))}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.acceptOpenRouter(t)
	j = f.runToTerminal(t, j.ID)
	if j.Status != video.StatusCompleted || j.SettlementState != video.SettlementUnsettled || j.BillingKnown || j.SettledMicros != 0 || j.ReservedMicros != 55444 {
		t.Fatalf("status=%s settlement=%s known=%v settled=%d reserved=%d", j.Status, j.SettlementState, j.BillingKnown, j.SettledMicros, j.ReservedMicros)
	}
	if _, ok := j.ToStatusResponse()["cost"]; ok {
		t.Fatal("unknown cost serialized as a settled cost")
	}
	total, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	if total != float64(j.ReservedMicros)/1e6 {
		t.Fatalf("reservation released on unknown cost: %v", total)
	}
}

// OpenRouter "expired" maps to failed and keeps its reason; billing on failure
// is undocumented at runtime, so it stays unsettled without a reported cost.
func TestOpenRouter_ExpiredFailsWithReason(t *testing.T) {
	f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(provider.Observation{Normalized: provider.StateFailed, ProviderState: "expired", Error: &provider.ProviderError{Code: "provider_expired", Message: "provider job expired before completion"}})}})
	j := f.acceptOpenRouter(t)
	j = f.runToTerminal(t, j.ID)
	if j.Status != video.StatusFailed || j.Error == nil || j.Error.Code != "provider_expired" || j.ProviderState != "expired" || j.SettlementState != video.SettlementUnsettled {
		t.Fatalf("status=%s err=%+v provider_state=%s settlement=%s", j.Status, j.Error, j.ProviderState, j.SettlementState)
	}
}

// A failed job with a reported zero cost settles at zero (released).
func TestOpenRouter_FailedWithReportedZeroCostReleases(t *testing.T) {
	zero := provider.Observation{Normalized: provider.StateFailed, ProviderState: "failed", Error: &provider.ProviderError{Code: "provider_failed"}, Usage: &provider.Usage{Lines: []provider.UsageLine{{Unit: provider.USD, Quantity: 0, Source: provider.UsageProviderReported}}}}
	f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(zero)}})
	j := f.runToTerminal(t, f.acceptOpenRouter(t).ID)
	if j.Status != video.StatusFailed || j.SettlementState != video.SettlementReleased || j.SettledMicros != 0 {
		t.Fatalf("status=%s settlement=%s settled=%d", j.Status, j.SettlementState, j.SettledMicros)
	}
}

// OpenRouter has no idempotency key or job lookup. 429, 499 and 5xx after the
// body was sent leave the job submission_unknown; it is never sent twice.
func TestOpenRouter_AmbiguousSubmitNeverResubmits(t *testing.T) {
	for _, status := range []int{429, 499, 502} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			f := setupOpenRouter(t, videotest.Script{Submit: videotest.Reply{Status: status}})
			j := f.acceptOpenRouter(t)
			f.tick(t)
			j = f.job(t, j.ID)
			if j.Status != video.StatusSubmissionUnknown || j.RetrySafe || !j.UpstreamMayContinue {
				t.Fatalf("status=%s retry_safe=%v", j.Status, j.RetrySafe)
			}
			for i := 0; i < 4; i++ {
				f.clock.Advance(5 * time.Minute)
				f.tick(t)
			}
			j = f.job(t, j.ID)
			if n := f.fake.SubmitCount(j.ID); n != 1 {
				t.Fatalf("upstream submits=%d, want 1", n)
			}
			if j.Status != video.StatusFailed || j.ReconcileState != video.ReconcileUnresolved || j.SettlementState != video.SettlementUnsettled {
				t.Fatalf("status=%s reconcile=%s settlement=%s", j.Status, j.ReconcileState, j.SettlementState)
			}
		})
	}
}

// 402 and 400 are definite refusals before a job exists, so the reservation is
// released and nothing is retried.
func TestOpenRouter_DefiniteRejectionReleases(t *testing.T) {
	for _, status := range []int{400, 402} {
		f := setupOpenRouter(t, videotest.Script{Submit: videotest.Reply{Status: status}})
		j := f.acceptOpenRouter(t)
		f.tick(t)
		j = f.job(t, j.ID)
		if j.Status != video.StatusFailed || j.SettlementState != video.SettlementReleased || j.UpstreamMayContinue || f.fake.SubmitCount(j.ID) != 1 {
			t.Fatalf("%d: status=%s settlement=%s", status, j.Status, j.SettlementState)
		}
	}
}

// Cancellation is unsupported upstream; deletion is local-only and says that
// upstream generation may continue.
func TestOpenRouter_CancelUnsupportedDeleteLocalOnly(t *testing.T) {
	f := setupOpenRouter(t, videotest.Script{})
	j := f.acceptOpenRouter(t)
	f.tick(t)
	if _, e := f.s.Get(context.Background(), j.ID, "org-b"); !errors.Is(e, video.ErrVideoJobNotFound) {
		t.Fatal("cross-tenant read", e)
	}
	_, e := f.s.Cancel(context.Background(), j.ID, "org-a")
	var ae *models.APIError
	if !errors.As(e, &ae) || ae.Status != 409 || ae.Code != "cancel_unsupported" {
		t.Fatal(e)
	}
	d, e := f.s.Delete(context.Background(), j.ID, "org-a")
	if e != nil || d.DeletedAt == nil || !d.UpstreamMayContinue {
		t.Fatalf("%v %+v", e, d)
	}
}

// OpenRouter may answer completed with usage.cost:null and report the charge
// on a later read. The terminal job is re-read for accounting only (no copy, no
// resubmit) and settles at the reported amount.
func TestOpenRouter_LaterReportedCostSettles(t *testing.T) {
	cost := 0.0482
	f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(orCompleted(nil)), videotest.JSONReply(orCompleted(&cost))}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.runToTerminal(t, f.acceptOpenRouter(t).ID)
	if j.Status != video.StatusCompleted || j.SettlementState != video.SettlementUnsettled || len(j.Artifacts) != 1 {
		t.Fatalf("status=%s settlement=%s", j.Status, j.SettlementState)
	}
	f.clock.Advance(6 * time.Minute)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.SettlementState != video.SettlementSettled || j.SettledMicros != 48200 || j.UsagePolls != 1 || j.Status != video.StatusCompleted || len(j.Artifacts) != 1 || f.fake.SubmitCount(j.ID) != 1 {
		t.Fatalf("settlement=%s settled=%d usage_polls=%d status=%s submits=%d", j.SettlementState, j.SettledMicros, j.UsagePolls, j.Status, f.fake.SubmitCount(j.ID))
	}
	total, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	if total < 0.04819 || total > 0.04821 {
		t.Fatalf("budget spend %v", total)
	}
}

// A charge that never becomes known stops being re-read after maxUsagePolls;
// the reservation stays held.
func TestOpenRouter_UsageRefreshIsBounded(t *testing.T) {
	f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(orCompleted(nil))}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.runToTerminal(t, f.acceptOpenRouter(t).ID)
	for i := 0; i < maxUsagePolls+4; i++ {
		f.clock.Advance(6 * time.Minute)
		f.tick(t)
	}
	j = f.job(t, j.ID)
	if j.UsagePolls != maxUsagePolls || j.SettlementState != video.SettlementUnsettled || j.ReservedMicros != 55444 {
		t.Fatalf("usage_polls=%d settlement=%s reserved=%d", j.UsagePolls, j.SettlementState, j.ReservedMicros)
	}
	total, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	if total != float64(j.ReservedMicros)/1e6 {
		t.Fatalf("reservation released without a known charge: %v", total)
	}
}

// The sniffed image type replaces a guessed or mislabelled one before the
// request is persisted, so the provider never receives PNG bytes as JPEG.
func TestOpenRouter_VerifiedImageTypeReplacesDeclared(t *testing.T) {
	f := setupOpenRouter(t, videotest.Script{})
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "video"
	rc.Metadata["org_id"] = "org-a"
	rc.Metadata["auth_key_id"] = "key-a"
	rc.Metadata["key_type"] = "byok"
	in := provider.Input{Role: provider.FirstFrame, MediaType: "image/jpeg", Source: provider.Source{Data: base64.StdEncoding.EncodeToString(videotest.PNG())}}
	r, e := f.s.Accept(context.Background(), rc, provider.Request{Model: "openrouter/" + orModel, Prompt: "p", DurationSeconds: 4, Resolution: "480p", Inputs: []provider.Input{in}}, video.NewID(), nil)
	if e != nil {
		t.Fatal(e)
	}
	j := f.job(t, r.Job.ID)
	if !strings.Contains(string(j.RequestCanonical), `"media_type":"image/png"`) || strings.Contains(string(j.RequestCanonical), "image/jpeg") {
		t.Fatalf("%s", j.RequestCanonical)
	}
}

// A 429 during an accounting read honours Retry-After and does not use up one
// of the bounded reads.
func TestOpenRouter_UsageRefreshHonoursCooldown(t *testing.T) {
	limited := videotest.Reply{Status: 429, Header: map[string][]string{"Retry-After": {"3600"}}}
	f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(orCompleted(nil)), limited}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.runToTerminal(t, f.acceptOpenRouter(t).ID)
	f.clock.Advance(6 * time.Minute)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.UsagePolls != 0 || j.NextPollAt.Sub(f.clock.Now()) < time.Hour || j.SettlementState != video.SettlementUnsettled {
		t.Fatalf("usage_polls=%d next=%v settlement=%s", j.UsagePolls, j.NextPollAt.Sub(f.clock.Now()), j.SettlementState)
	}
}

// Accounting reads stop a day after the job ended, so even a cost that would
// now be reported is not fetched (rate-limited reads never count against
// maxUsagePolls, so the age bound is what ends them).
func TestOpenRouter_UsageRefreshEndsAfterMaxAge(t *testing.T) {
	cost := 0.05
	f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(orCompleted(nil)), videotest.JSONReply(orCompleted(&cost))}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.runToTerminal(t, f.acceptOpenRouter(t).ID)
	f.clock.Advance(maxUsageRefreshAge + time.Minute)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.UsagePolls != 0 || j.SettlementState != video.SettlementUnsettled || j.SettledMicros != 0 {
		t.Fatalf("usage_polls=%d settlement=%s settled=%d", j.UsagePolls, j.SettlementState, j.SettledMicros)
	}
}
