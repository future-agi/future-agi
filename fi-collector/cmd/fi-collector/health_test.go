package main

import (
	"bytes"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"runtime"
	"runtime/debug"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
)

type routingStub struct{ snapshot auth.RoutingSnapshot }

func (r routingStub) RoutingSnapshot() auth.RoutingSnapshot { return r.snapshot }

func TestHealthBodyReportsSimulationRouting(t *testing.T) {
	router := routingStub{auth.RoutingSnapshot{
		Dropped:        map[string]int64{"no_agent": 3, "column_missing": 0},
		ColumnsPresent: true,
	}}
	raw, err := json.Marshal(healthBody("ok", chwriter.Stats{}, router))
	if err != nil {
		t.Fatal(err)
	}
	var body struct {
		Status  string         `json:"status"`
		Routing map[string]any `json:"simulation_routing"`
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body.Status != "ok" || body.Routing["no_agent"] != float64(3) || body.Routing["column_missing"] != float64(0) || body.Routing["columns_present"] != true {
		t.Fatalf("body = %s", raw)
	}
	// Without a router the body is what it was.
	raw, err = json.Marshal(healthBody("unhealthy", chwriter.Stats{}, nil))
	if err != nil {
		t.Fatal(err)
	}
	var plain map[string]any
	if err := json.Unmarshal(raw, &plain); err != nil {
		t.Fatal(err)
	}
	if _, present := plain["simulation_routing"]; present || plain["status"] != "unhealthy" {
		t.Fatalf("body = %s", raw)
	}
}

func TestAdminHealthReportsBuildAndAdmissionLimits(t *testing.T) {
	t.Setenv("FI_BUILD_REVISION", "0123456789abcdef0123456789abcdef01234567")
	writer, err := chwriter.New(chwriter.Config{URL: "http://127.0.0.1:1", DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	srv := server.New(server.Config{MaxPendingRows: 321, BatchMaxRows: 17}, writer, nil, nil, nil)
	rec := httptest.NewRecorder()
	adminHandler(writer, nil, srv.Limits()).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("healthz = %d", rec.Code)
	}
	var body struct {
		Status  string         `json:"status"`
		Build   map[string]any `json:"build"`
		Runtime map[string]any `json:"runtime"`
		Limits  struct {
			MaxPendingRows   int   `json:"max_pending_rows"`
			BatchMaxRows     int   `json:"batch_max_rows"`
			BatchMaxAgeMS    int64 `json:"batch_max_age_ms"`
			MaxHTTPBodyBytes int   `json:"max_http_body_bytes"`
		} `json:"limits"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Status != "ok" || body.Limits.MaxPendingRows != 321 || body.Limits.BatchMaxRows != 17 || body.Limits.BatchMaxAgeMS != 5000 || body.Limits.MaxHTTPBodyBytes != 16<<20 {
		t.Fatalf("limits not reported as in force: %s", rec.Body.String())
	}
	if body.Build["configured_revision"] != "0123456789abcdef0123456789abcdef01234567" || body.Build["go_version"] == "" {
		t.Fatalf("build identity missing: %v", body.Build)
	}
	// The CPU the collector process may use, as the Go runtime sees it.
	if cpus, _ := body.Runtime["num_cpu"].(float64); cpus < 1 || body.Runtime["gomaxprocs"] != float64(runtime.GOMAXPROCS(0)) {
		t.Fatalf("runtime hardware missing: %v", body.Runtime)
	}
	limit, reported := body.Runtime["go_memory_limit_bytes"]
	if want := debug.SetMemoryLimit(-1); (want == math.MaxInt64) == reported || (reported && limit != float64(want)) {
		t.Fatalf("memory limit misreported (in force %d): %v", want, body.Runtime)
	}
	// The default budget is reported, not a zero that a reader could take for unlimited.
	rec = httptest.NewRecorder()
	adminHandler(writer, nil, server.New(server.Config{}, writer, nil, nil, nil).Limits()).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if !bytes.Contains(rec.Body.Bytes(), []byte(`"max_pending_rows":20000`)) {
		t.Fatalf("default budget not reported: %s", rec.Body.String())
	}
}
