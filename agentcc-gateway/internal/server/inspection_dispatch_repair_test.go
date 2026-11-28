package server

import (
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type inspectionRepairObserver struct {
	pre, post                        int
	cost, used, requestID, operation string
	short                            bool
}

func (*inspectionRepairObserver) Name() string  { return "inspection-repair-observer" }
func (*inspectionRepairObserver) Priority() int { return 999 }
func (p *inspectionRepairObserver) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	p.pre++
	return pipeline.ResultContinue()
}
func (p *inspectionRepairObserver) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.post++
	p.cost, p.used = rc.Metadata["cost"], rc.Metadata["credits_used"]
	p.requestID, p.operation = rc.RequestID, rc.Metadata["inspection_operation"]
	p.short = rc.Flags.ShortCircuited
	return pipeline.ResultContinue()
}

// Use the real cost, credits and logging plugins, not just an attribution spy.
// Refused dispatches finalize once without a provider call or charge; admitted
// controls are finalized and charged only by the handler's normal pipeline.
func TestInspectionR3DispatchRefusalTelemetryAndAccounting(t *testing.T) {
	for _, tc := range []struct {
		name, path, body, key, mode string
		blocked                     bool
	}{
		{"embedding-refused", "/v1/embeddings", `{"model":"text-embedding-3-small","input":"clean text"}`, "a", "enforce", true},
		{"speech-refused", "/v1/audio/speech", `{"model":"tts-1","input":"clean text","voice":"alloy"}`, "a", "enforce", true},
		{"speech-stream-refused", "/v1/audio/speech/stream", `{"model":"tts-1","input":"clean text","voice":"alloy"}`, "a", "enforce", true},
		{"unselected-control", "/v1/embeddings", `{"model":"text-embedding-3-small","input":"clean text"}`, "b", "enforce", false},
		{"monitor-control", "/v1/embeddings", `{"model":"text-embedding-3-small","input":"clean text"}`, "a", "monitor", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv, wire, audit := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy(tc.mode, "block", "r2-post", nil))})
			defer srv.asyncWorker.Stop()
			keyID := "key_1"
			if tc.key == "b" {
				keyID = "key_2"
			}
			key := srv.keyStore.Get(keyID)
			key.KeyType = "managed"
			key.SetBalanceMicros(10_000_000)
			org := srv.TenantStore.Get("org-r2")
			org.Providers = map[string]*tenant.ProviderConfig{
				"openai": {Enabled: true, APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"text-embedding-3-small", "tts-1"}},
			}
			org.CostTracking = &tenant.CostTrackingConfig{Enabled: true, CustomPricing: map[string]*tenant.CustomPricing{
				"text-embedding-3-small": {InputPerMTok: 1000},
			}}
			srv.TenantStore.Set("org-r2", org)

			logs := &safeBuffer{}
			previous := slog.Default()
			slog.SetDefault(slog.New(slog.NewJSONHandler(logs, nil)))
			defer slog.SetDefault(previous)
			logger := logging.New(config.RequestLoggingConfig{Enabled: true, Workers: 1, BufferSize: 10}, srv.TenantStore)
			defer logger.Close()
			observer := &inspectionRepairObserver{}
			db := testModelDBPtr()
			engine := pipeline.NewEngine(srv.engine.Plugin("auth"), srv.engine.Plugin("guardrails"),
				cost.New(true, db.Load, srv.TenantStore, nil), credits.New(true, srv.keyStore), logger, audit, observer)
			srv.engine, srv.handlers.engine = engine, engine
			srv.router.configureInspection(engine, srv.keyStore, srv.cfg.IPACL, srv.TenantStore)

			w := routeR2Send(srv, tc.path, tc.body, tc.key)
			logger.Close() // Drain asynchronous traces before assertions.
			paths, _ := wire.snapshot()
			wantStatus, wantCalls, wantPre, wantBalance := 200, 1, 1, int64(9_995_000)
			if tc.blocked {
				wantStatus, wantCalls, wantPre, wantBalance = 403, 0, 0, 10_000_000
				if !strings.Contains(w.Body.String(), "output_guardrail_unsupported") {
					t.Fatalf("missing refusal code: %s", w.Body.String())
				}
				routeR2AssertOutcome(t, audit, "r2-post", "enforce", "blocked")
				if observer.cost != "" && observer.cost != "0.000000" || observer.used != "" || !observer.short || observer.operation == "" {
					t.Fatalf("refusal accounting/context: %+v", observer)
				}
			} else if observer.cost != "0.005000" || observer.used != "0.005000" || observer.short {
				t.Fatalf("control accounting/context: %+v status=%d body=%s", observer, w.Code, w.Body.String())
			}
			if w.Code != wantStatus || len(paths) != wantCalls || observer.pre != wantPre || observer.post != 1 || key.BalanceMicros() != wantBalance {
				t.Fatalf("status=%d calls=%d pre=%d post=%d balance=%d", w.Code, len(paths), observer.pre, observer.post, key.BalanceMicros())
			}
			var traces []map[string]any
			for _, line := range strings.Split(strings.TrimSpace(logs.String()), "\n") {
				var record map[string]any
				if err := json.Unmarshal([]byte(line), &record); err != nil {
					t.Fatal(err)
				}
				if record["msg"] == "request.trace" {
					traces = append(traces, record)
				}
			}
			if len(traces) != 1 {
				t.Fatalf("traces=%d logs=%s", len(traces), logs.String())
			}
			trace := traces[0]
			if trace["status_code"] != float64(wantStatus) || trace["auth_key_id"] != keyID || observer.requestID == "" || trace["request_id"] != observer.requestID {
				t.Fatalf("trace attribution: %+v observer=%+v", trace, observer)
			}
			if tc.blocked && (trace["error_code"] != "output_guardrail_unsupported" || trace["guardrail_triggered"] != true || trace["short_circuited"] != true || !strings.Contains(trace["guardrail_results"].(string), `"policy_id":"r2-block"`)) {
				t.Fatalf("missing refusal telemetry: %+v", trace)
			}
			t.Logf("status=%d provider_calls=%d pre_calls=%d post_calls=%d traces=%d cost=%q credits_used=%q balance_micros=%d", w.Code, len(paths), observer.pre, observer.post, len(traces), observer.cost, observer.used, key.BalanceMicros())
		})
	}
}
