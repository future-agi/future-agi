package server

import (
	"context"
	"fmt"
	"net/http"
	"runtime"
	"strings"
	"testing"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func TestCatalogSortScratchSmallBudgetStillAcceptsTinyRequest(t *testing.T) {
	// The explicit 1MiB budget is supported by resolveIngestLimits. A fixed
	// 1MiB per-request scratch charge would make EVERY request oversize there,
	// although this tiny batch cannot allocate anywhere near that much scratch.
	s := New(Config{IngestBudgetMiB: 1}, nil, nil, nil, NoopMetering{})
	body := calBody(t, calTraces(1, 1, 1, nil), false)
	rec := postBody(s, "application/x-protobuf", false, nil, body)
	if rec.Code != http.StatusOK {
		t.Fatalf("supported small budget refused tiny request: status=%d body=%s", rec.Code, rec.Body.String())
	}
}

func TestCatalogSortScratchCoversActualMergeAllocations(t *testing.T) {
	for _, shape := range []string{"tiny", "fifty-scalars", "fifty-array-values", "escaping"} {
		t.Run(shape, func(t *testing.T) {
			td := calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				a := sp.Attributes()
				switch shape {
				case "tiny":
					a.PutStr("key", "value")
				case "fifty-scalars":
					for i := 0; i < 50; i++ {
						a.PutStr(fmt.Sprintf("attribute.%02d", i), "twelve-bytes")
					}
				case "fifty-array-values", "escaping":
					slice := a.PutEmptySlice("array")
					n := 50
					if shape == "escaping" {
						n = 16
					}
					for i := 0; i < n; i++ {
						text := fmt.Sprintf("value.%02d", i)
						if shape == "escaping" {
							text += strings.Repeat("\x00<&>\"\\", 4)
						}
						slice.AppendEmpty().SetStr(text)
					}
				}
			})
			scan, err := scanOTLPProto(calBody(t, td, false))
			if err != nil {
				t.Fatal(err)
			}
			rows, _, err := chexp.ConvertWithIdentities(context.Background(), td, nil)
			if err != nil {
				t.Fatal(err)
			}
			batch, _, err := observedcatalog.Extract(observedcatalog.ScopedSpan{OrganizationID: calOrg, WorkspaceID: calWS, Row: rows[0]}, observedcatalog.DefaultLimits())
			if err != nil {
				t.Fatal(err)
			}
			runtime.GC()
			var before, after runtime.MemStats
			runtime.ReadMemStats(&before)
			got := observedcatalog.Merge(batch, batch, batch)
			runtime.ReadMemStats(&after)
			allocated := after.TotalAlloc - before.TotalAlloc
			if allocated > uint64(scan.catalogSortScratch()) {
				t.Fatalf("whole Merge allocated %d, scratch alone prepays %d", allocated, scan.catalogSortScratch())
			}
			runtime.KeepAlive(got)
		})
	}
}

func TestCatalogSortScratchChargeIsBoundedAndPrepaid(t *testing.T) {
	for _, n := range []int{0, 1, 512, 4096} {
		s, err := scanOTLPProto(calBody(t, calTraces(1, 1, n, nil), false))
		if err != nil {
			t.Fatal(err)
		}
		charge := s.catalogSortScratch()
		if charge < 0 || charge > observedcatalog.MergeSortScratchBytes {
			t.Fatalf("unbounded charge %d", charge)
		}
		if n == 0 && charge != 0 {
			t.Fatal("empty request has no catalog rows")
		}
		if n >= 512 && charge != observedcatalog.MergeSortScratchBytes {
			t.Fatal("finite burst must retain its exact tested 1MiB scratch charge")
		}
		if s.price().rows < charge {
			t.Fatal("scratch not prepaid in retained rows")
		}
	}
}
