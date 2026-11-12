package main

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestResultRejectionBeforeOutputs(t *testing.T) {
	for _, tc := range []struct{ name, field string }{
		{"sentinel-conservation", "lag.sampled_spans"},
		{"verified-chunks-zero", "verification.verified_chunks"},
		{"response-total-off-one", "response_statuses"},
		{"impossible-percentile-order", "otlp_request_latency"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			input := filepath.Join("..", "..", "pkg", "ingestbench", "testdata", "round2-confirmed-"+tc.name+".json")
			dir := t.TempDir()
			var out bytes.Buffer
			for _, args := range [][]string{{"report", input}, {"publish", "--min-sustained", "500ms", "--markdown", filepath.Join(dir, "report.txt"), "--json", filepath.Join(dir, "report.json"), input}} {
				err := command(context.Background(), args, &out)
				if err == nil || !strings.Contains(err.Error(), tc.field) {
					t.Errorf("want %s rejection, got %v", tc.field, err)
				}
			}
			files, err := os.ReadDir(dir)
			if err != nil {
				t.Fatal(err)
			}
			if len(files) != 0 || out.Len() != 0 {
				t.Fatalf("invalid evidence created output: %d files, %d bytes", len(files), out.Len())
			}
		})
	}
}
