package server

import (
	"context"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// billingRecorder records what the post-plugins saw on each call: the
// characters the cost plugin would bill, and whether the call was recorded as
// failed.
type billingRecorder struct {
	mu         sync.Mutex
	characters []string
	failed     []bool
}

func (p *billingRecorder) Name() string  { return "billing" }
func (p *billingRecorder) Priority() int { return 100 }
func (p *billingRecorder) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *billingRecorder) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.characters = append(p.characters, rc.Metadata["input_characters"])
	p.failed = append(p.failed, len(rc.Errors) > 0)
	return pipeline.ResultContinue()
}

// An embedContent call is priced from the characters it sends, so only an
// embedding that was made may carry them. A call the gateway refused, or that
// failed upstream, used to carry them too, and was charged.
func TestGenAIEmbedIsBilledOnlyWhenItSucceeds(t *testing.T) {
	cases := []struct {
		name           string
		upstreamStatus int
		refused        bool
		wantStatus     int
		wantCharacters string
	}{
		{"an embedding made", http.StatusOK, false, http.StatusOK, "5"},
		{"a call failed upstream", http.StatusBadRequest, false, http.StatusBadRequest, ""},
		{"a call the gateway refused", http.StatusOK, true, http.StatusTooManyRequests, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if !strings.HasSuffix(r.URL.Path, ":embedContent") {
					io.WriteString(w, `{"data":[]}`) // the startup probe
					return
				}
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(tc.upstreamStatus)
				if tc.upstreamStatus == http.StatusOK {
					io.WriteString(w, `{"embedding":{"values":[0.1,0.2]}}`)
				} else {
					io.WriteString(w, `{"error":{"code":400,"message":"bad request","status":"INVALID_ARGUMENT"}}`)
				}
			}))
			t.Cleanup(upstream.Close)

			recorder := &billingRecorder{}
			plugins := []pipeline.Plugin{recorder}
			if tc.refused {
				plugins = append(plugins, refusePlugin{})
			}
			srv := streamingServer(t, upstream.URL, plugins...)
			req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gemini-3.7-flash:embedContent",
				strings.NewReader(`{"content":{"parts":[{"text":"hello"}]}}`))
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			if w.Code != tc.wantStatus {
				t.Fatalf("status = %d, want %d: %s", w.Code, tc.wantStatus, w.Body.String())
			}
			recorder.mu.Lock()
			defer recorder.mu.Unlock()
			if len(recorder.characters) != 1 {
				t.Fatalf("post-plugins ran %d times, want once", len(recorder.characters))
			}
			if got := recorder.characters[0]; got != tc.wantCharacters {
				t.Errorf("billed characters = %q, want %q", got, tc.wantCharacters)
			}
			if failed := recorder.failed[0]; failed != (tc.wantStatus != http.StatusOK) {
				t.Errorf("recorded as failed = %v, want %v", failed, !failed)
			}
		})
	}
}

// A config override can price every endpoint the cost plugin prices. The
// per-query, per-page, per-image and per-second prices used to be dropped on
// the way from the config file to the model table.
func TestModelOverridesCarryEndpointPrices(t *testing.T) {
	var overrides map[string]config.ModelOverrideConfig
	err := yaml.Unmarshal([]byte(`
rerank-fixture: {pricing: {input_per_query: 0.002}}
ocr-fixture: {pricing: {per_page: 0.001}}
image-fixture: {pricing: {output_per_image: 0.04}}
video-fixture: {pricing: {output_per_second: 0.5}}
`), &overrides)
	if err != nil {
		t.Fatal(err)
	}
	db := modeldb.New(nil, ConvertModelOverrides(overrides))

	checks := []struct {
		name string
		cost func() (float64, bool)
		want float64
	}{
		{"per query", func() (float64, bool) { return db.QueryCost("rerank-fixture", 3) }, 0.006},
		{"per page", func() (float64, bool) { return db.PageCost("ocr-fixture", 2) }, 0.002},
		{"per image", func() (float64, bool) { return db.ImageCost("image-fixture", "", "", 2) }, 0.08},
		{"per second", func() (float64, bool) { return db.VideoCost("video-fixture", 4) }, 2},
	}
	for _, c := range checks {
		got, ok := c.cost()
		if !ok || math.Abs(got-c.want) > 1e-9 {
			t.Errorf("%s: cost = %v, %v; want %v", c.name, got, ok, c.want)
		}
	}
}
