package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
)

type handoffRoundTripper func(*http.Request) (*http.Response, error)

func (f handoffRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type handoffReadError struct{ err error }

func (r handoffReadError) Read([]byte) (int, error) { return 0, r.err }

type handoffRootNotifier func([]traceavailable.Root) error

func (f handoffRootNotifier) EnqueueRoots(roots []traceavailable.Root) error { return f(roots) }

// Exercise the real source writer and drain order, replacing only HTTP transport
// with an in-memory response. No listener, database or live ingestion is involved.
// Both observed catalog and Error Feed handoffs require confirmed source storage;
// failures in either handoff must not suppress the other or replay the source.
func TestObservedHandoffGapAfterSourceCommit(t *testing.T) {
	for _, tc := range []struct {
		name, sourceBody, exception string
		sourceStatus                int
		readErr, catalogErr         error
		notifierErr                 error
		length                      int64
		confirmed                   bool
	}{
		{name: "catalog failure", sourceStatus: http.StatusOK, catalogErr: errors.New("spool full"), confirmed: true},
		{name: "notification failure", sourceStatus: http.StatusOK, notifierErr: errors.New("notification queue full"), confirmed: true},
		{name: "both handoffs fail", sourceStatus: http.StatusOK, catalogErr: errors.New("spool full"), notifierErr: errors.New("notification queue full"), confirmed: true},
		{name: "source failure", sourceStatus: http.StatusBadRequest, catalogErr: errors.New("spool full")},
		{name: "success", sourceStatus: http.StatusOK, confirmed: true},
		{name: "HTTP200 exception header", sourceStatus: http.StatusOK, exception: "241"},
		{name: "HTTP200 exception body", sourceStatus: http.StatusOK, sourceBody: "DB::Exception: private-source-value", length: -1},
		{name: "HTTP200 truncated framing", sourceStatus: http.StatusOK, length: 1},
		{name: "HTTP200 unexpected EOF", sourceStatus: http.StatusOK, readErr: io.ErrUnexpectedEOF, length: -1},
		{name: "HTTP200 read error", sourceStatus: http.StatusOK, readErr: errors.New("interrupted response"), length: -1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var bodies [][]byte
			previous := http.DefaultTransport
			t.Cleanup(func() { http.DefaultTransport = previous })
			http.DefaultTransport = handoffRoundTripper(func(r *http.Request) (*http.Response, error) {
				if r.URL.Query().Get("async_insert") != "0" || r.URL.Query().Has("wait_for_async_insert") {
					t.Error("canonical insert did not explicitly request synchronous execution")
				}
				body, err := io.ReadAll(r.Body)
				if err != nil {
					t.Fatal(err)
				}
				bodies = append(bodies, body)
				var reader io.Reader = strings.NewReader(tc.sourceBody)
				if tc.readErr != nil {
					reader = io.MultiReader(reader, handoffReadError{tc.readErr})
				}
				response := &http.Response{StatusCode: tc.sourceStatus, Header: make(http.Header), Body: io.NopCloser(reader), ContentLength: tc.length, Request: r}
				if tc.exception != "" {
					response.Header.Set("X-ClickHouse-Exception-Code", tc.exception)
				}
				return response, nil
			})
			deadLetter := t.TempDir() + "/spans.jsonl"
			writer := newSpanTestWriter(t, "http://offline.invalid", deadLetter)
			t.Cleanup(func() { _ = writer.Close() })
			catalog := &propertyCatalogWriterStub{err: tc.catalogErr}
			var notified []traceavailable.Root
			notifier := handoffRootNotifier(func(roots []traceavailable.Root) error {
				if writer.Snapshot().BatchesInserted != 1 {
					t.Fatal("notification before confirmed canonical insert")
				}
				notified = append(notified, roots...)
				return tc.notifierErr
			})
			var logs bytes.Buffer
			s := New(Config{}, writer, nil, nil, nil, WithPropertyCatalogWriter(catalog), WithTraceNotifier(notifier), WithLogger(slog.New(slog.NewJSONHandler(&logs, nil))))
			org := "11111111-1111-4111-8111-111111111111"
			workspace := "22222222-2222-4222-8222-222222222222"
			project := "33333333-3333-4333-8333-333333333333"
			trace := "44444444-4444-4444-8444-444444444444"
			row := map[string]any{"org_id": org, "project_id": project, "start_time": "2026-09-09 01:02:03.123456", "id": "private-span", "attrs_string": map[string]string{"private-key": "private-value"}, "trace_id": trace, "parent_span_id": "", "end_time": "2026-09-09 01:02:04.123456"}
			var expected bytes.Buffer
			enc := json.NewEncoder(&expected)
			enc.SetEscapeHTML(false)
			if err := enc.Encode(row); err != nil {
				t.Fatal(err)
			}
			s.enqueueScoped([]map[string]any{row}, nil, org, workspace, map[string]struct{}{project: {}})
			if catalog.calls != 0 || len(notified) != 0 {
				t.Fatal("handoff before canonical flush")
			}
			s.drainNow(context.Background())
			// A second, empty drain must not repeat either handoff or the insert.
			s.drainNow(context.Background())
			if len(bodies) != 1 || !bytes.Equal(bodies[0], expected.Bytes()) {
				t.Fatalf("source write replayed or changed: %q", bodies)
			}
			if strings.Contains(logs.String(), "private-") {
				t.Fatal("source payload logged")
			}
			stats := writer.Snapshot()
			if !tc.confirmed {
				if len(notified) != 0 {
					t.Error("unconfirmed source reached Error Feed")
				}
				if catalog.calls != 0 || strings.Contains(logs.String(), "observed_catalog_handoff_gap") {
					t.Error("unconfirmed source reached catalog")
				}
				if stats.BatchesInserted != 0 || stats.BatchesRetried != 0 || stats.BatchesFailed != 1 || stats.RowsDeadLettered != 1 {
					t.Errorf("unconfirmed source reported success or replayed: %+v", stats)
				}
				if dead, err := os.ReadFile(deadLetter); err != nil || !bytes.Equal(dead, expected.Bytes()) {
					t.Error("unconfirmed source not preserved unchanged in dead-letter")
				}
				return
			}
			if len(notified) != 1 || notified[0].OrganizationID != org || notified[0].WorkspaceID != workspace || notified[0].ProjectID != project || notified[0].TraceID != trace || notified[0].SpanID != row["id"] {
				t.Fatalf("notification lost or changed authenticated root: %+v", notified)
			}
			if strings.Contains(logs.String(), "error feed stored-root notification gap") != (tc.notifierErr != nil) {
				t.Fatalf("notification gap diagnostic mismatch: %s", logs.String())
			}
			if catalog.calls != 1 || stats.BatchesInserted != 1 || stats.BatchesFailed != 0 || stats.RowsDeadLettered != 0 {
				t.Fatalf("catalog changed source health: %+v", stats)
			}
			if tc.catalogErr == nil {
				if strings.Contains(logs.String(), "observed_catalog_handoff_gap") {
					t.Fatal("success logged as gap")
				}
				return
			}
			for _, expected := range []string{`"event":"observed_catalog_handoff_gap"`, `"event":"observed_catalog_repair_scope"`, `"workspace_id":"` + workspace + `"`, `"project_id":"` + project + `"`, `"source_first_seen":"2026-09-09 01:02:03.123456"`} {
				if !strings.Contains(logs.String(), expected) {
					t.Errorf("missing repair diagnostic %s in %s", expected, logs.String())
				}
			}
		})
	}
}
