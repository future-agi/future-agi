package server

// Base-compatible source-boundary probe. A cap refusal installed at both
// authorized seams must prevent unsupported async admission. This fixture
// measures queued jobs, not provider execution: the base has no video worker.
import (
	"context"
	"encoding/json"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type g8CapBoundaryProbe struct {
	budgetCalls atomic.Int32
	runCalls    atomic.Int32
}

func (*g8CapBoundaryProbe) Name() string  { return "budget" }
func (*g8CapBoundaryProbe) Priority() int { return 40 }
func (p *g8CapBoundaryProbe) ProcessRequest(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	p.budgetCalls.Add(1)
	return pipeline.ResultError(agentbudget.ErrCallCapUnenforceable("scope-test", "unsupported"))
}
func (*g8CapBoundaryProbe) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *g8CapBoundaryProbe) CallCap(*models.RequestContext, *tenant.OrgConfig) *models.APIError {
	p.runCalls.Add(1)
	return agentbudget.ErrCallCapUnenforceable("scope-test", "unsupported")
}

func TestG8CapUnsupportedVideoBoundary(t *testing.T) {
	srv := newJobRoutesServer(t, true)
	probe := &g8CapBoundaryProbe{}
	srv.handlers.engine = pipeline.NewEngine(authplugin.New(srv.keyStore, true), probe)
	var cfg tenant.OrgConfig
	if err := json.Unmarshal([]byte(`{"budgets":{"enabled":true,"admission_caps_enabled":true,"agents":{"agent-a":{"limit":0,"period":"daily"}}},"routing":{"agents":{"max_run_usd":0}}}`), &cfg); err != nil {
		t.Fatal(err)
	}
	srv.TenantStore.Set(videoOrgA, &cfg)

	t.Run("budget seam controls pipeline", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Metadata["authorization"] = "Bearer " + videoKeyA
		rc.EndpointType = "chat"
		err := srv.handlers.engine.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error {
			t.Error("provider call escaped budget gate control")
			return nil
		})
		apiErr, ok := err.(*models.APIError)
		if !ok || apiErr.Status != http.StatusPaymentRequired || apiErr.Code != agentbudget.CodeCallCapUnenforceable || probe.budgetCalls.Load() != 1 {
			t.Fatalf("budget gate control: calls=%d err=%v", probe.budgetCalls.Load(), err)
		}
	})
	srv.handlers.SetRunBudget(probe)
	t.Cleanup(func() { srv.handlers.SetRunBudget(nil) })
	t.Run("run seam controls chat", func(t *testing.T) {
		w := jobRoutesCall(srv, videoKeyA, "POST", "/v1/chat/completions", `{"model":"fake-video/clip-1","messages":[{"role":"user","content":"synthetic"}]}`)
		if w.Code != http.StatusPaymentRequired || errorCode(t, w) != agentbudget.CodeCallCapUnenforceable || probe.runCalls.Load() != 1 {
			t.Fatalf("run gate control: status=%d calls=%d body=%s", w.Code, probe.runCalls.Load(), w.Body)
		}
	})
	if t.Failed() {
		t.Fatal("scope probe invalid: a control failed")
	}
	beforeBudget, beforeRun := probe.budgetCalls.Load(), probe.runCalls.Load()
	w := jobRoutesCall(srv, videoKeyA, "POST", "/v1/videos", videoBody)
	jobs, err := srv.handlers.videoStore.GetActiveJobs()
	if err != nil {
		t.Fatal(err)
	}
	if w.Code == http.StatusAccepted && len(jobs) == 1 && probe.budgetCalls.Load() == beforeBudget && probe.runCalls.Load() == beforeRun {
		t.Fatal("G8_SCOPE_RED: unsupported video accepted and queued; neither budget priority 40 nor runBudgetCap was called; no provider execution is claimed")
	}
	if w.Code != http.StatusPaymentRequired || len(jobs) != 0 {
		t.Fatalf("want unsupported-cap refusal and no queued jobs: status=%d jobs=%d body=%s", w.Code, len(jobs), w.Body)
	}
}
