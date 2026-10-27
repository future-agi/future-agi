package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

func usageTraces(accepted, rejected bool) ptrace.Traces {
	td := ptrace.NewTraces()
	for i, include := range []bool{accepted, rejected} {
		if !include {
			continue
		}
		rs := td.ResourceSpans().AppendEmpty()
		name := "accepted"
		if i == 1 {
			name = "uncreatable-project"
			// Below the 64 KiB resource-attribute limit; large rejected data
			// also lives on the span so the request reaches project stamping.
			rs.Resource().Attributes().PutStr("rejected_resource", strings.Repeat("R", 32<<10))
		}
		rs.Resource().Attributes().PutStr("project_name", name)
		sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
		sp.SetName(name)
		sp.SetTraceID(pcommon.TraceID{byte(i + 1)})
		sp.SetSpanID(pcommon.SpanID{byte(i + 1)})
		sp.SetStartTimestamp(pcommon.Timestamp(1_700_000_000_000_000_000))
		sp.SetEndTimestamp(sp.StartTimestamp() + 1_000_000)
		if i == 1 {
			sp.Attributes().PutStr("rejected_payload", strings.Repeat("X", 256<<10))
		}
	}
	return td
}

// Assert the actual observe_add amount written by UsageEmitter, not just call
// counts. JSON whitespace, compression, transport and permanently rejected
// resources must not affect the canonical accepted-traces byte amount.
func TestAuthenticatedAcceptedUsageTransportParity(t *testing.T) {
	expected := usageTraces(true, false)
	attrs := expected.ResourceSpans().At(0).Resource().Attributes()
	attrs.PutStr("fi.org_id", calOrg)
	attrs.PutStr("fi.project_id", calProject)
	attrs.PutStr("project_type", auth.ProjectTypeObserve)
	canonical, err := (&ptrace.ProtoMarshaler{}).MarshalTraces(expected)
	if err != nil {
		t.Fatal(err)
	}
	wantBytes := int64(len(canonical))
	for _, transport := range []string{"protobuf", "protobuf-gzip", "json", "json-gzip", "grpc"} {
		for _, batch := range []string{"full-success", "mixed", "all-dropped"} {
			t.Run(transport+"/"+batch, func(t *testing.T) {
				accepted, rejected := batch != "all-dropped", batch != "full-success"
				td := usageTraces(accepted, rejected)
				mr := miniredis.RunT(t)
				rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
				t.Cleanup(func() { _ = rdb.Close() })
				usage := auth.NewUsageEmitter(rdb, nil, slog.New(slog.NewTextHandler(io.Discard, nil)), 100)
				a := realTestAuth(t, &ingestResolver{}, time.Minute)
				s, conn, ch := startGRPCServer(t, Config{}, func(s *Server) { attachRealAuth(s, a); s.usage = usage })
				ctx, cancel := context.WithTimeout(withKeys(context.Background()), 5*time.Second)
				defer cancel()
				response := ptraceotlp.NewExportResponse()
				if transport == "grpc" {
					response, err = ptraceotlp.NewGRPCClient(conn).Export(ctx, ptraceotlp.NewExportRequestFromTraces(td))
					if err != nil {
						t.Fatal(err)
					}
				} else {
					ct := "application/x-protobuf"
					body := protoBody(t, td)
					if strings.HasPrefix(transport, "json") {
						ct = "application/json"
						body, err = ptraceotlp.NewExportRequestFromTraces(td).MarshalJSON()
						if err != nil {
							t.Fatal(err)
						}
						var pretty bytes.Buffer
						if err := json.Indent(&pretty, body, "", "    "); err != nil {
							t.Fatal(err)
						}
						body = pretty.Bytes()
					}
					if rejected && int64(len(body)) < wantBytes*100 {
						t.Fatal("fixture must be dominated by rejected bytes")
					}
					if strings.HasSuffix(transport, "gzip") {
						body = gzipBytes(t, body)
					}
					req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body)).WithContext(ctx)
					req.Header.Set("Content-Type", ct)
					req.Header.Set("X-Api-Key", "k")
					req.Header.Set("X-Secret-Key", "s")
					if strings.HasSuffix(transport, "gzip") {
						req.Header.Set("Content-Encoding", "gzip")
					}
					rec := httptest.NewRecorder()
					a.HTTPMiddleware(http.HandlerFunc(s.handleHTTPTraces)).ServeHTTP(rec, req)
					if rec.Code != 200 {
						t.Fatalf("HTTP %d: %s", rec.Code, rec.Body.String())
					}
					if ct == "application/json" {
						err = response.UnmarshalJSON(rec.Body.Bytes())
					} else {
						err = response.UnmarshalProto(rec.Body.Bytes())
					}
					if err != nil {
						t.Fatal(err)
					}
				}
				wantRejected := int64(0)
				if rejected {
					wantRejected = 1
				}
				if response.PartialSuccess().RejectedSpans() != wantRejected {
					t.Fatalf("rejected spans = %d, want %d", response.PartialSuccess().RejectedSpans(), wantRejected)
				}
				events, err := rdb.XRange(ctx, "usage:events", "-", "+").Result()
				if err != nil {
					t.Fatal(err)
				}
				wantRows := 0
				if accepted {
					wantRows = 1
					if len(events) != 1 {
						t.Fatalf("usage events = %d, want 1", len(events))
					}
					values := events[0].Values
					if values["event_type"] != "observe_add" || values["org_id"] != calOrg || values["amount"] != strconv.FormatInt(wantBytes, 10) {
						t.Errorf("usage = %v, want accepted semantic protobuf bytes=%d", values, wantBytes)
					}
					if values["properties"] != `{"spans":1,"source":"fi-collector"}` {
						t.Errorf("rejected span included in usage properties: %v", values["properties"])
					}
				} else if len(events) != 0 {
					t.Fatalf("all-dropped batch emitted %d usage events", len(events))
				}
				s.pendMu.Lock()
				rows := len(s.pend)
				s.pendMu.Unlock()
				if rows != wantRows {
					t.Fatalf("pending rows = %d, want %d", rows, wantRows)
				}
				s.drainNow(ctx)
				ch.mu.Lock()
				defer ch.mu.Unlock()
				stored := 0
				for _, body := range ch.tables["spans"] {
					stored += bytes.Count(body, []byte("\n"))
					if bytes.Contains(body, []byte("uncreatable")) || bytes.Contains(body, []byte("rejected_payload")) {
						t.Fatal("rejected resource reached storage")
					}
				}
				if stored != wantRows {
					t.Fatalf("stored rows = %d, want %d", stored, wantRows)
				}
				if accepted {
					t.Logf("actual UsageEmitter amount=%v accepted canonical protobuf=%d rejected_spans=%d stored=%d", events[0].Values["amount"], wantBytes, wantRejected, stored)
				} else {
					t.Logf("all dropped: usage events=%d rejected_spans=%d stored=%d", len(events), wantRejected, stored)
				}
			})
		}
	}
}
