package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
)

func langfuseRouteCredentials() (string, string) {
	return strings.Join([]string{"test", "langfuse", "public"}, "-"),
		strings.Join([]string{"test", "langfuse", "private"}, "-")
}

type langfuseRouteResolver struct{}

func (langfuseRouteResolver) ValidateKey(_ context.Context, key, secret string) (*auth.ResolveResult, error) {
	public, private := langfuseRouteCredentials()
	if key != public || secret != private {
		return nil, nil
	}
	return targetResult(), nil
}

func (langfuseRouteResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return targetResult().Projects, nil
}

func (langfuseRouteResolver) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	return "", errors.New("unexpected project creation in route test")
}

// Exercise Run's real mux and authentication middleware, not a hand-built
// handler. Only persistence is replaced; the default Langfuse path must ingest
// the same protobuf request and enforce the same target checks as /v1/traces.
func TestOTLPHTTPLangfuseDefaultRoute(t *testing.T) {
	for _, tc := range []struct {
		name, credentials, expectedProject, suffix string
		gzip                                       bool
		want                                       int
	}{
		{name: "valid-basic", credentials: "valid", want: http.StatusOK},
		{name: "valid-basic-gzip", credentials: "valid", gzip: true, want: http.StatusOK},
		{name: "expected-project-match", credentials: "valid", expectedProject: expectedProject, want: http.StatusOK},
		{name: "expected-project-mismatch", credentials: "valid", expectedProject: otherProject, want: http.StatusForbidden},
		{name: "missing-basic", want: http.StatusUnauthorized},
		{name: "invalid-basic", credentials: "invalid", want: http.StatusUnauthorized},
		{name: "trailing-slash", credentials: "valid", suffix: "/", want: http.StatusNotFound},
		{name: "descendant", credentials: "valid", suffix: "/extra", want: http.StatusNotFound},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a, err := auth.NewWithResolver(auth.Config{CacheTTL: time.Minute, WarmTTL: time.Minute}, langfuseRouteResolver{}, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
			if err != nil || a == nil {
				t.Fatalf("auth.NewWithResolver: %v", err)
			}
			t.Cleanup(a.Close)
			s := newServerOn(t, a)
			s.cfg.GRPCAddr = ""
			s.cfg.HTTPAddr = testTCPAddr(t)
			// Keep accepted rows available for assertions until shutdown drains
			// them to the existing in-memory ClickHouse HTTP stub.
			s.cfg.BatchMaxRows = 1000
			s.cfg.BatchMaxAge = time.Hour
			ctx, cancel := context.WithCancel(context.Background())
			done := make(chan error, 1)
			go func() { done <- s.Run(ctx) }()
			t.Cleanup(func() {
				cancel()
				select {
				case err := <-done:
					if !errors.Is(err, context.Canceled) {
						t.Errorf("server shutdown: %v", err)
					}
				case <-time.After(5 * time.Second):
					t.Error("server did not stop")
				}
			})
			if !waitPort(s.cfg.HTTPAddr, 2*time.Second) {
				t.Fatal("collector HTTP listener did not start")
			}

			traces := targetTraces("")
			traces.ResourceSpans().At(0).Resource().Attributes().Clear()
			body := protoBody(t, traces)
			if tc.gzip {
				body = gzipBytes(t, body)
			}
			client := &http.Client{Timeout: 2 * time.Second}
			for i, path := range []string{"/v1/traces", "/tracer/v1/traces", "/api/public/otel/v1/traces"} {
				req, err := http.NewRequest(http.MethodPost, "http://"+s.cfg.HTTPAddr+path+tc.suffix, bytes.NewReader(body))
				if err != nil {
					t.Fatal(err)
				}
				req.Header.Set("Content-Type", "application/x-protobuf")
				req.Header.Set("X-Project-Name", "selected")
				if tc.gzip {
					req.Header.Set("Content-Encoding", "gzip")
				}
				if tc.credentials != "" {
					public, private := langfuseRouteCredentials()
					if tc.credentials == "invalid" {
						private += "-invalid"
					}
					req.SetBasicAuth(public, private)
				}
				if tc.expectedProject != "" {
					req.Header.Set(auth.ExpectedOrganizationHeader, expectedOrg)
					req.Header.Set(auth.ExpectedWorkspaceHeader, expectedWorkspace)
					req.Header.Set(auth.ExpectedProjectHeader, tc.expectedProject)
				}
				resp, err := client.Do(req)
				if err != nil {
					t.Fatal(err)
				}
				responseBody, readErr := io.ReadAll(resp.Body)
				resp.Body.Close()
				if readErr != nil {
					t.Fatal(readErr)
				}
				if resp.StatusCode != tc.want {
					t.Fatalf("%s: status=%d want=%d: %s", path, resp.StatusCode, tc.want, responseBody)
				}
				if tc.want == http.StatusOK {
					if resp.Header.Get("Content-Type") != "application/x-protobuf" {
						t.Fatalf("%s did not return an OTLP protobuf response", path)
					}
					projects := s.pendingProjects()
					if len(projects) != 1 || projects[expectedProject] != i+1 || len(s.events.snapshot()) != i+1 {
						t.Fatalf("%s: wrong project or usage: projects=%v usage=%v", path, projects, s.events.snapshot())
					}
					s.pendMu.Lock()
					stored, err := json.Marshal(s.pend[i])
					s.pendMu.Unlock()
					if err != nil || !bytes.Contains(stored, []byte(`"project_name":"selected"`)) || !bytes.Contains(stored, []byte(expectedOrg)) {
						t.Fatalf("%s: project header or authenticated org not stamped: %s (%v)", path, stored, err)
					}
				} else {
					rows, properties, roots := s.pending()
					if rows+properties+roots != 0 || len(s.events.snapshot()) != 0 {
						t.Fatalf("%s: rejected request enqueued or billed spans", path)
					}
				}
			}
			if tc.want == http.StatusUnauthorized && s.IngestStats().Rejected["http/auth/401"] != 3 {
				t.Fatal("canonical, legacy and Langfuse auth refusals must all be counted")
			}
		})
	}
}
