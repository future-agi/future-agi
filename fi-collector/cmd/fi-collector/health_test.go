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
	"github.com/future-agi/future-agi/fi-collector/pkg/ingestbench"
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
	srv := server.New(server.Config{BatchMaxRows: 17}, writer, nil, nil, nil)
	rec := httptest.NewRecorder()
	adminHandler(writer, nil, srv).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
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
		Admission *server.Admission   `json:"admission"`
		Writer    *chwriter.Policy    `json:"writer"`
		Ingest    *server.IngestStats `json:"ingest"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Status != "ok" || body.Limits.MaxPendingRows != 0 || body.Limits.BatchMaxRows != 17 || body.Limits.BatchMaxAgeMS != 5000 || body.Limits.MaxHTTPBodyBytes != 16<<20 {
		t.Fatalf("limits not reported as in force: %s", rec.Body.String())
	}
	// Backlog counts are observational, separate from effective byte admission.
	if body.Admission == nil || body.Admission.MaxPendingRows != 0 || body.Admission.BufferedRows != 0 || body.Admission.PendingRows != 0 {
		t.Fatalf("admission state not reported: %s", rec.Body.String())
	}
	if body.Ingest == nil || body.Ingest.BudgetBytes != srv.Limits().IngestBudgetBytes {
		t.Fatalf("dev ingest telemetry missing: %s", rec.Body.String())
	}
	// What an acknowledged insert means: the writer's insert mode and retry policy,
	// never its URL or credentials.
	if w := body.Writer; w == nil || w.InsertSettings != "async_insert=0" || w.MaxRetries != 5 || w.RequestTimeoutMS != 30000 || !w.DeadLetter {
		t.Fatalf("writer policy not reported: %s", rec.Body.String())
	}
	if bytes.Contains(rec.Body.Bytes(), []byte("127.0.0.1:1")) || bytes.Contains(rec.Body.Bytes(), []byte("dl.jsonl")) {
		t.Fatalf("writer URL or dead-letter path leaked: %s", rec.Body.String())
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
	// Zero is a legacy row field only; dev's byte budget remains effective.
	rec = httptest.NewRecorder()
	adminHandler(writer, nil, server.New(server.Config{}, writer, nil, nil, nil)).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if !bytes.Contains(rec.Body.Bytes(), []byte(`"max_pending_rows":0`)) {
		t.Fatalf("default budget not reported: %s", rec.Body.String())
	}
}

// The benchmark samples this handler for its backlog series and reads its writer
// policy for the report: the production body must parse as a valid sample and
// policy there, so the two cannot drift apart unnoticed.
func TestAdminHealthFeedsTheBenchmarkBacklogSampler(t *testing.T) {
	writer, err := chwriter.New(chwriter.Config{URL: "http://127.0.0.1:1", AsyncInsert: true, DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	srv := server.New(server.Config{IngestBudgetMiB: 77}, writer, nil, nil, nil)
	admin := httptest.NewServer(adminHandler(writer, nil, srv))
	defer admin.Close()
	resp, err := http.Get(admin.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	body := new(bytes.Buffer)
	_, _ = body.ReadFrom(resp.Body)
	resp.Body.Close()
	sample := ingestbench.ParseCollectorHealth(resp.StatusCode, body.Bytes())
	if !sample.OK || sample.MaxPendingRows != 0 || sample.BufferedRows != 0 || sample.Error != "" {
		t.Fatalf("benchmark could not sample the production /healthz: %+v\n%s", sample, body)
	}
	info := ingestbench.FetchCollectorHealth(t.Context(), http.DefaultClient, admin.URL+"/healthz")
	if info.Error != "" || info.Limits == nil || info.Limits.MaxPendingRows != 0 || info.Limits.AdmissionModel != "byte-leases-v1" || info.Limits.IngestBudgetBytes != 77<<20 || len(info.Ingest) == 0 || info.Writer == nil ||
		info.Writer.InsertSettings != "async_insert=1&wait_for_async_insert=0" || info.Writer.MaxRetries != 5 || !info.Writer.DeadLetter {
		t.Fatalf("benchmark could not read the production limits and writer policy: %+v %+v", info, info.Writer)
	}
}
