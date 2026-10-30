package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/protobuf/encoding/protowire"
)

// Walk the real scanner -> pdata decoder -> converter -> chunk writer. The
// capture is ClickHouse's HTTP boundary, not a second model of row encoding.
func assertCorrectnessWirePrice(t *testing.T, body []byte, isJSON bool) otlpShape {
	t.Helper()
	shape, err := scanBody(body, isJSON)
	if err != nil {
		t.Fatal(err)
	}
	req := ptraceotlp.NewExportRequest()
	if isJSON {
		err = req.UnmarshalJSON(body)
	} else {
		err = req.UnmarshalProto(body)
	}
	if err != nil {
		t.Fatal(err)
	}
	rows, ids, err := chexp.ConvertWithIdentities(context.Background(), req.Traces(), nil)
	if err != nil {
		t.Fatal(err)
	}
	ch, writer := newCHBodies(t)
	s := New(Config{}, writer, nil, nil, nil)
	s.enqueue(rows, ids)
	s.drainNow(context.Background())
	total, largest := ch.lines("spans")
	var curated int64
	for _, table := range []string{"traces", "end_users", "trace_sessions"} {
		n, _ := ch.lines(table)
		curated += n
	}
	t.Logf("body=%d rows=%d scan_rows=%d largest=%d scan_max=%d curated=%d scan_curated=%d roots=%d", len(body), total, shape.rowJSONBytes, largest, shape.maxRowJSON, curated, shape.curatedJSONBytes, shape.rootSpans)
	if total > shape.rowJSONBytes || largest > shape.maxRowJSON {
		t.Errorf("actual canonical JSON exceeds scan: total %d > %d or largest %d > %d", total, shape.rowJSONBytes, largest, shape.maxRowJSON)
	}
	if curated > shape.curatedJSONBytes {
		t.Errorf("actual curated JSON %d exceeds scan %d", curated, shape.curatedJSONBytes)
	}
	return shape
}

func TestCorrectnessPromotedStringWirePrice(t *testing.T) {
	aliases := []string{"llm.model_name", "gen_ai.request.model", "gen_ai.response.model", "llm.request.model", "gen_ai.provider.name", "llm.system", "gen_ai.system", "llm.vendor", "llm.provider", "gen_ai.operation.name"}
	for _, key := range aliases {
		for _, isJSON := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/json=%t", key, isJSON), func(t *testing.T) {
				td := calTraces(1, 1, 2, func(_ ptrace.ResourceSpans, sp ptrace.Span, i int) {
					sp.Attributes().PutStr(key, strings.Repeat("x\x01\"<\\ ", 2048))
					sp.Attributes().PutStr("input.value", strings.Repeat("\x02\"<&", 1024))
					sp.Attributes().PutStr("output.value", strings.Repeat("\n\\\"", 1024))
				})
				assertCorrectnessWirePrice(t, calBody(t, td, isJSON), isJSON)
			})
		}
	}
}

func TestCorrectnessZeroParentWirePrice(t *testing.T) {
	for _, parents := range []struct {
		name   string
		fields [][]byte
		roots  int64
	}{
		{"absent", nil, 1},
		{"explicit-zero", [][]byte{make([]byte, 8)}, 1},
		{"nonzero", [][]byte{{1, 2, 3, 4, 5, 6, 7, 8}}, 0},
		{"last-zero", [][]byte{{1, 2, 3, 4, 5, 6, 7, 8}, make([]byte, 8)}, 1},
		{"last-nonzero", [][]byte{make([]byte, 8), {1, 2, 3, 4, 5, 6, 7, 8}}, 0},
		{"last-empty", [][]byte{{1, 2, 3, 4, 5, 6, 7, 8}, {}}, 1},
	} {
		t.Run(parents.name, func(t *testing.T) {
			span := pbCat(pbField(1, bytes.Repeat([]byte{1}, 16)), pbField(2, bytes.Repeat([]byte{2}, 8)), pbField(5, []byte("root")))
			span = protowire.AppendFixed64(protowire.AppendTag(span, 7, protowire.Fixed64Type), 1700000000000000000)
			span = protowire.AppendFixed64(protowire.AppendTag(span, 8, protowire.Fixed64Type), 1700000001000000000)
			span = append(span, pbField(9, pbKV("input.value", pbField(1, []byte(strings.Repeat("x\x01\"<", 4096)))))...)
			for _, p := range parents.fields {
				span = append(span, pbField(4, p)...)
			}
			resource := pbField(1, pbKV("fi.project_id", pbField(1, []byte(calProject))))
			body := pbField(1, pbCat(pbField(1, resource), pbField(2, pbField(2, span))))
			req := ptraceotlp.NewExportRequest()
			if err := req.UnmarshalProto(body); err != nil {
				t.Fatal(err)
			}
			decodedRoot := req.Traces().ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).ParentSpanID().IsEmpty()
			want := int64(0)
			if decodedRoot {
				want = 1
			}
			t.Logf("decoder root=%t (fixture expectation %d)", decodedRoot, parents.roots)
			if want != parents.roots {
				t.Fatalf("pdata parent semantics changed: roots %d want %d", want, parents.roots)
			}
			shape := assertCorrectnessWirePrice(t, body, false)
			if shape.rootSpans != want {
				t.Errorf("scan roots %d != real decoder %d", shape.rootSpans, want)
			}
			// pdata's JSON output and an escaped zero must agree with the real decoder.
			jb, err := req.MarshalJSON()
			if err != nil {
				t.Fatal(err)
			}
			js := assertCorrectnessWirePrice(t, jb, true)
			if js.rootSpans != want {
				t.Errorf("JSON roots %d != decoder %d", js.rootSpans, want)
			}
		})
	}
}

func TestCorrectnessFinalHeaderShapeBeforeEnqueue(t *testing.T) {
	for _, isJSON := range []bool{false, true} {
		t.Run(fmt.Sprintf("json=%t", isJSON), func(t *testing.T) {
			td := calTraces(1, 1, 4, nil)
			td.ResourceSpans().At(0).Resource().Attributes().Clear()
			body := calBody(t, td, isJSON)
			shape, err := scanBody(body, isJSON)
			if err != nil {
				t.Fatal(err)
			}
			a := realTestAuth(t, &ingestResolver{}, time.Minute)
			usage := &recordingUsage{}
			s := New(Config{}, nil, a, usage, NoopMetering{})
			// Original shape fits, but the valid 255-codepoint four-byte name repeated
			// into every stored resource map does not. It must not be billed/enqueued.
			s.limits.maxEncoded = shape.rowJSONBytes + 512
			req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/x-protobuf")
			if isJSON {
				req.Header.Set("Content-Type", "application/json")
			}
			req.Header.Set("X-Api-Key", "k")
			req.Header.Set("X-Secret-Key", "s")
			req.Header.Set(auth.ProjectNameHeader, strings.Repeat("\U0001f680", 255))
			rec := httptest.NewRecorder()
			s.auth.HTTPMiddleware(http.HandlerFunc(s.handleHTTPTraces)).ServeHTTP(rec, req)
			if rec.Code != http.StatusRequestEntityTooLarge {
				t.Errorf("final stamped shape status=%d, want 413 before enqueue", rec.Code)
			}
			if usage.calls.Load() != 0 || len(s.pend) != 0 {
				t.Errorf("oversized final shape enqueued=%d usage=%d", len(s.pend), usage.calls.Load())
			}
		})
	}
}

func TestCorrectnessPartialChunkHandoff(t *testing.T) {
	for _, concurrent := range []int{1, 3} {
		for _, ambiguous := range []bool{false, true} {
			t.Run(fmt.Sprintf("concurrent=%d/ambiguous=%t", concurrent, ambiguous), func(t *testing.T) {
				var mu sync.Mutex
				sent := map[string]int{}
				srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					b, _ := io.ReadAll(r.Body)
					if insertTable(r) != "spans" {
						w.WriteHeader(200)
						return
					}
					var row map[string]any
					if err := json.Unmarshal(bytes.TrimSpace(b), &row); err != nil {
						t.Error(err)
						w.WriteHeader(400)
						return
					}
					id := row["id"].(string)
					mu.Lock()
					sent[id]++
					mu.Unlock()
					if id == "bad" {
						if ambiguous {
							w.Header().Set("X-ClickHouse-Exception-Code", "241")
							w.WriteHeader(200)
						} else {
							w.WriteHeader(400)
						}
						return
					}
					w.WriteHeader(200)
				}))
				defer srv.Close()
				dl := t.TempDir() + "/dl.jsonl"
				writer := newSpanTestWriter(t, srv.URL, dl)
				defer writer.Close()
				catalog := &propertyCatalogWriterStub{}
				var roots []traceavailable.Root
				s := New(Config{}, writer, nil, nil, nil, WithPropertyCatalogWriter(catalog), WithTraceNotifier(handoffRootNotifier(func(r []traceavailable.Root) error { roots = append(roots, r...); return nil })))
				writer.SetMaxInsertBytes(1)
				writer.SetMaxConcurrentInserts(concurrent)
				for _, id := range []string{"good-a", "bad", "good-b"} {
					row := map[string]any{"id": id, "trace_id": "44444444-4444-4444-8444-444444444444", "parent_span_id": "", "org_id": calOrg, "project_id": calProject, "start_time": "2026-10-27 01:00:00.000000", "end_time": "2026-10-27 01:00:01.000000"}
					s.enqueueScoped([]map[string]any{row}, nil, calOrg, calWS, map[string]struct{}{calProject: {}})
				}
				s.drainNow(context.Background())
				s.drainNow(context.Background())
				if len(roots) != 2 || len(catalog.rows) != 2 {
					t.Errorf("confirmed siblings lost: notifications=%d catalog=%d want=2 each", len(roots), len(catalog.rows))
				}
				for _, r := range roots {
					if r.SpanID == "bad" {
						t.Error("unconfirmed notification")
					}
				}
				for _, r := range catalog.rows {
					if r.Row["id"] == "bad" {
						t.Error("unconfirmed catalog row")
					}
				}
				mu.Lock()
				defer mu.Unlock()
				for _, id := range []string{"good-a", "bad", "good-b"} {
					if sent[id] != 1 {
						t.Errorf("canonical %s sent %d times", id, sent[id])
					}
				}
				dead, err := os.ReadFile(dl)
				if err != nil {
					t.Fatal(err)
				}
				if bytes.Count(dead, []byte("\n")) != 1 || !bytes.Contains(dead, []byte(`"id":"bad"`)) {
					t.Error("deadletter must preserve only unconfirmed row")
				}
			})
		}
	}
}
