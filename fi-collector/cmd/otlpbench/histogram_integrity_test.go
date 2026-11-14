package main

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestHistogramCounterexamplesBeforeOutputs(t *testing.T) {
	files, err := filepath.Glob(filepath.Join("..", "..", "pkg", "ingestbench", "testdata", "round3", "counterexample-*.json"))
	if err != nil || len(files) != 26 {
		t.Fatalf("retained histogram counterexamples: %d, %v", len(files), err)
	}
	for _, input := range files {
		t.Run(filepath.Base(input), func(t *testing.T) {
			for _, mode := range []string{"report", "publish"} {
				dir := t.TempDir()
				args := []string{mode, input}
				if mode == "publish" {
					args = []string{mode, "--min-sustained", "1ns", "--markdown", filepath.Join(dir, "report.txt"), "--json", filepath.Join(dir, "report.json"), input}
				}
				var out bytes.Buffer
				err := command(context.Background(), args, &out)
				if err == nil || (!strings.Contains(err.Error(), "buckets") && !strings.Contains(err.Error(), "lag seen-series") && !strings.Contains(err.Error(), "verification.durable_lag.insert_lag_from_scheduled_send.p50_ms is required")) {
					t.Errorf("%s must reject contradictory histogram: %v", mode, err)
				}
				entries, err := os.ReadDir(dir)
				if err != nil || len(entries) != 0 || out.Len() != 0 {
					t.Errorf("%s created output before refusal: files=%d bytes=%d error=%v", mode, len(entries), out.Len(), err)
				}
			}
		})
	}
}

func TestHistogramDiagnosticControlsRender(t *testing.T) {
	files, err := filepath.Glob(filepath.Join("..", "..", "pkg", "ingestbench", "testdata", "round3", "positive-*.json"))
	if err != nil || len(files) != 10 {
		t.Fatalf("positive histogram controls: %d, %v", len(files), err)
	}
	for _, input := range files {
		t.Run(filepath.Base(input), func(t *testing.T) {
			var out bytes.Buffer
			if err := command(context.Background(), []string{"report", input}, &out); err != nil || out.Len() == 0 {
				t.Fatalf("report diagnostic: %v bytes=%d", err, out.Len())
			}
			dir := t.TempDir()
			md, js := filepath.Join(dir, "report.txt"), filepath.Join(dir, "report.json")
			if err := command(context.Background(), []string{"publish", "--min-sustained", "1ns", "--markdown", md, "--json", js, input}, &out); err != nil {
				t.Fatalf("publish diagnostic: %v", err)
			}
			for _, path := range []string{md, js} {
				if b, err := os.ReadFile(path); err != nil || len(b) == 0 {
					t.Fatalf("missing diagnostic output: %s %v", path, err)
				}
			}
		})
	}
}
