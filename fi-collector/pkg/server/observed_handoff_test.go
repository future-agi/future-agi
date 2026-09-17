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
)

type handoffRoundTripper func(*http.Request) (*http.Response, error)

func (f handoffRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type handoffReadError struct{ err error }

func (r handoffReadError) Read([]byte) (int, error) { return 0, r.err }

// Exercise the real source writer and drain order, replacing only HTTP transport
// with an in-memory response. No listener, database or live ingestion is involved.
func TestObservedHandoffGapAfterSourceCommit(t *testing.T) {
	for _, tc := range []struct {
		name, sourceBody, exception string
		sourceStatus                int
		readErr, catalogErr         error
		length                      int64
		confirmed                   bool
	}{
		{name: "catalog failure", sourceStatus: http.StatusOK, catalogErr: errors.New("spool full"), confirmed: true},
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
			var logs bytes.Buffer
			s := New(Config{}, writer, nil, nil, nil, WithPropertyCatalogWriter(catalog), WithLogger(slog.New(slog.NewJSONHandler(&logs, nil))))
			org := "11111111-1111-4111-8111-111111111111"
			workspace := "22222222-2222-4222-8222-222222222222"
			project := "33333333-3333-4333-8333-333333333333"
			row := map[string]any{"org_id": org, "project_id": project, "start_time": "2026-09-09 01:02:03.123456", "id": "private-span", "attrs_string": map[string]string{"private-key": "private-value"}}
			var expected bytes.Buffer
			enc := json.NewEncoder(&expected)
			enc.SetEscapeHTML(false)
			if err := enc.Encode(row); err != nil {
				t.Fatal(err)
			}
			s.enqueueScoped([]map[string]any{row}, nil, org, workspace, map[string]struct{}{project: {}})
			s.drainNow(context.Background())
			if len(bodies) != 1 || !bytes.Equal(bodies[0], expected.Bytes()) {
				t.Fatalf("source write replayed or changed: %q", bodies)
			}
			if strings.Contains(logs.String(), "private-") {
				t.Fatal("source payload logged")
			}
			stats := writer.Snapshot()
			if !tc.confirmed {
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
