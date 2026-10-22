package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/ingestbench"
)

var dryArgs = []string{"--run-id", "0123456789abcdef", "--project-id", "11111111-1111-4111-8111-111111111111", "--project-name", "fixture"}

func TestCLIIsOfflineByDefault(t *testing.T) {
	var out bytes.Buffer
	if err := command(context.Background(), dryArgs, &out); err != nil {
		t.Fatal(err)
	}
	var r ingestbench.Result
	if err := json.Unmarshal(out.Bytes(), &r); err != nil {
		t.Fatal(err)
	}
	if r.Status != "dry-run" || r.Config.Execute || r.PlannedSpans != 500 {
		t.Fatalf("unsafe default: %+v", r.Config)
	}
}

func TestCLIRefusesOverwriteAndUnboundedProfile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.json")
	if err := os.WriteFile(path, []byte("do not overwrite"), 0600); err != nil {
		t.Fatal(err)
	}
	args := append(append([]string{}, dryArgs...), "--out", path)
	if command(context.Background(), args, &bytes.Buffer{}) == nil {
		t.Fatal("overwrote artifact")
	}
	b, _ := os.ReadFile(path)
	if string(b) != "do not overwrite" {
		t.Fatal("artifact changed")
	}
	args = append(append([]string{}, dryArgs...), "--execute", "--rate", "15000", "--duration", "24h")
	if err := command(context.Background(), args, &bytes.Buffer{}); err == nil {
		t.Fatal("full-scale profile bypassed safety gates")
	}
}

func TestCLIReportAndSizing(t *testing.T) {
	path := filepath.Join(t.TempDir(), "plan.json")
	args := append(append([]string{}, dryArgs...), "--out", path)
	if err := command(context.Background(), args, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	if err := command(context.Background(), []string{"report", path}, &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "No zero-loss conclusion") {
		t.Fatal(out.String())
	}
	size := filepath.Join(t.TempDir(), "size.json")
	if err := os.WriteFile(size, []byte(`{"spans_per_day":86400,"retention_days":10,"physical_bytes_per_span_per_replica":1000,"replicas":2,"disk_bytes_per_node":1000000000,"usable_disk_fraction":0.8,"measured_spans_per_second_per_shard":10,"peak_spans_per_second":25}`), 0600); err != nil {
		t.Fatal(err)
	}
	out.Reset()
	if err := command(context.Background(), []string{"size", size}, &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "extrapolation_not_measured_capacity") {
		t.Fatal(out.String())
	}
}

func TestCLIRejectsTrailingAndUnknownInput(t *testing.T) {
	for _, body := range []string{`{} {}`, `{"unknown":1}`} {
		path := filepath.Join(t.TempDir(), "input.json")
		if err := os.WriteFile(path, []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
		if err := command(context.Background(), []string{"size", path}, &bytes.Buffer{}); err == nil {
			t.Fatal("invalid sizing input accepted")
		}
	}
}

func TestCLIRecordsRedactedCommandLineAndEncoding(t *testing.T) {
	var out bytes.Buffer
	args := append(append([]string{}, dryArgs...), "--encoding", "grpc", "--endpoint", "http://127.0.0.1:4317", "--gzip")
	if err := command(context.Background(), args, &out); err != nil {
		t.Fatal(err)
	}
	var r ingestbench.Result
	if err := json.Unmarshal(out.Bytes(), &r); err != nil {
		t.Fatal(err)
	}
	if r.Config.Encoding != "grpc" || !r.Config.Gzip || len(r.Provenance.CommandLine) != len(args)+1 || r.Provenance.CommandLine[0] != "otlpbench" {
		t.Fatalf("wire path or command line not recorded: %+v %v", r.Config, r.Provenance.CommandLine)
	}
}

// B5: the budget check is offline without a health URL, and warns loudly when
// a documented profile needs more than the collector's default budget.
func TestCLICollectorBudgetWarnsLoudly(t *testing.T) {
	var warn bytes.Buffer
	old := warnings
	warnings = &warn
	defer func() { warnings = old }()
	var out bytes.Buffer
	if err := command(context.Background(), []string{"collector-budget", "--rate", "15000", "--batch-size", "500", "--concurrency", "16"}, &out); err != nil {
		t.Fatal(err)
	}
	var info ingestbench.CollectorInfo
	if err := json.Unmarshal(out.Bytes(), &info); err != nil {
		t.Fatal(err)
	}
	if info.Budget.Sufficient || info.Budget.RequiredPendingRows != 43000 || !strings.Contains(warn.String(), "*** WARNING") || !strings.Contains(warn.String(), "FI_MAX_PENDING_ROWS=43000") {
		t.Fatalf("budget %+v warnings %q", info.Budget, warn.String())
	}
	if err := command(context.Background(), []string{"collector-budget", "--rate", "100", "--collector-health-url", "http://example.invalid:9464/healthz"}, &bytes.Buffer{}); err == nil {
		t.Fatal("a non-loopback health URL was contacted without approval")
	}
}

func TestCLIVerifyNeedsACheckpointAndNeverOverwrites(t *testing.T) {
	dir := t.TempDir()
	if err := command(context.Background(), []string{"verify"}, &bytes.Buffer{}); err == nil {
		t.Fatal("verify without a checkpoint accepted")
	}
	existing := filepath.Join(dir, "existing.json")
	if err := os.WriteFile(existing, []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := command(context.Background(), []string{"verify", "--checkpoint", filepath.Join(dir, "missing.json"), "--out", existing}, &bytes.Buffer{}); err == nil {
		t.Fatal("verify overwrote an artifact")
	}
	if b, _ := os.ReadFile(existing); string(b) != "keep" {
		t.Fatal("artifact changed")
	}
	if err := command(context.Background(), []string{"verify", "--checkpoint", filepath.Join(dir, "missing.json"), "--out", filepath.Join(dir, "new.json")}, &bytes.Buffer{}); err == nil {
		t.Fatal("a missing checkpoint was not reported")
	}
}
