package server

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestInspectionR3MissingReceiptHTTP(t *testing.T) {
	for _, mode := range []string{"write", "success-header", "upstream-error-status", "flush", "read-from", "empty", "complete-only", "changed-payload", "replaced-context"} {
		t.Run(mode, func(t *testing.T) {
			up := httptest.NewServer(&dialectUpstream{reply: "unused"})
			defer up.Close()
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
			srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
			defer srv.asyncWorker.Stop()
			var calls atomic.Int32
			srv.router.HandleInspected("GET", "/supported-fixture", func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				state := models.InspectionFromContext(r.Context())
				w.Header().Set("Content-Length", "999")
				w.Header().Set("Location", "/uninspected-content")
				switch mode {
				case "success-header":
					w.WriteHeader(200)
				case "upstream-error-status":
					w.WriteHeader(500)
				case "flush":
					w.(http.Flusher).Flush()
				case "read-from":
					_, _ = w.(io.ReaderFrom).ReadFrom(strings.NewReader("uninspected-content"))
					return
				case "empty":
					return
				case "complete-only":
					state.Complete(models.InspectionOutput, map[string]string{"text": "uninspected-content"})
				case "changed-payload":
					payload := map[string]string{"text": "checked-content"}
					state.Complete(models.InspectionOutput, payload)
					state.Decide(models.InspectionOutput, payload)
					payload["text"] = "uninspected-content"
				case "replaced-context":
					// A handler-local replacement cannot discard the gate's state.
					r = r.WithContext(models.WithInspection(r.Context(), models.NewInspectionState(textInspection("fake"))))
				}
				_, _ = io.WriteString(w, "uninspected-content")
			}, textInspection("test.supported"))
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()
			status, body := inspectionR3HTTP(t, gateway, "GET", "/supported-fixture", "", inspectionR3Headers("a"))
			if status != 403 || calls.Load() != 1 || !strings.Contains(body, "output_guardrail_unsupported") || strings.Contains(body, "uninspected-content") {
				t.Fatalf("status=%d handler_calls=%d body=%s", status, calls.Load(), body)
			}
		})
	}
}

func TestInspectionR3SafeGatewayErrorsHTTP(t *testing.T) {
	for _, key := range []string{"a", "b"} {
		for _, decided := range []bool{false, true} {
			t.Run(fmt.Sprintf("key=%s/decided=%v", key, decided), func(t *testing.T) {
				up := httptest.NewServer(&dialectUpstream{reply: "unused"})
				defer up.Close()
				cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
				srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
				defer srv.asyncWorker.Stop()
				srv.router.HandleInspected("GET", "/supported-error", func(w http.ResponseWriter, r *http.Request) {
					if decided {
						state := models.InspectionFromContext(r.Context())
						payload := map[string]string{"text": "inspected"}
						state.Complete(models.InspectionOutput, payload)
						state.Decide(models.InspectionOutput, payload)
					}
					param := "uninspected-error-content"
					models.WriteError(w, &models.APIError{Status: 502, Type: param, Code: param, Message: param, Param: &param, RetryAfter: param})
					// An accidental second write must not exploit the safe error marker.
					fmt.Fprint(w, param)
				}, textInspection("test.error"))
				gateway := httptest.NewServer(srv.httpServer.Handler)
				defer gateway.Close()
				status, body := inspectionR3HTTP(t, gateway, "GET", "/supported-error", "", inspectionR3Headers(key))
				if status != 502 || strings.Contains(body, "uninspected-error-content") != (key == "b") {
					t.Fatalf("status=%d body=%s", status, body)
				}
			})
		}
	}
}

func TestInspectionR3ExactNAAndDecisionHTTP(t *testing.T) {
	for _, mode := range []string{"decided", "na-with-evidence", "na-without-evidence"} {
		t.Run(mode, func(t *testing.T) {
			up := httptest.NewServer(&dialectUpstream{reply: "unused"})
			defer up.Close()
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
			srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
			defer srv.asyncWorker.Stop()
			contract := textInspection("test.metadata")
			if mode != "decided" {
				contract.Output = models.InspectionNotApplicable
				if mode == "na-with-evidence" {
					contract.Reason = "fixture returns only the literal integer token count, never request/provider content"
				}
			}
			srv.router.HandleInspected("GET", "/count-only-fixture", func(w http.ResponseWriter, r *http.Request) {
				payload := map[string]int{"count": 1}
				if mode == "decided" {
					state := models.InspectionFromContext(r.Context())
					state.Complete(models.InspectionOutput, payload)
					state.Decide(models.InspectionOutput, payload)
				}
				json.NewEncoder(w).Encode(payload)
			}, contract)
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()
			status, body := inspectionR3HTTP(t, gateway, "GET", "/count-only-fixture", "", inspectionR3Headers("a"))
			want := 200
			if mode == "na-without-evidence" {
				want = 403
			}
			if status != want {
				t.Fatalf("status=%d want=%d body=%s", status, want, body)
			}
		})
	}
}

func TestInspectionR3NonbufferingSSEHTTP(t *testing.T) {
	state := models.NewInspectionState(textInspection("test.input-only-sse"))
	state.BindPrincipal("org", "key")
	state.Require(models.InspectionInput)
	payload := map[string]string{"text": "inspected"}
	state.Complete(models.InspectionInput, payload)
	state.Decide(models.InspectionInput, payload)
	release := make(chan struct{})
	finished := make(chan struct{})
	gateway := httptest.NewServer(http.HandlerFunc(func(dst http.ResponseWriter, r *http.Request) {
		defer close(finished)
		guard, w := newInspectionWriter(dst, state)
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		<-release
		fmt.Fprint(w, "data: second\n\n")
		guard.finish()
	}))
	defer gateway.Close()
	defer close(release)
	client := gateway.Client()
	client.Timeout = 2 * time.Second
	resp, err := client.Get(gateway.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	line, err := bufio.NewReader(resp.Body).ReadString('\n')
	if err != nil || line != "data: first\n" || resp.StatusCode != 200 {
		t.Fatalf("status=%d line=%q err=%v", resp.StatusCode, line, err)
	}
	select {
	case <-finished:
		t.Fatal("handler returned before first event was observed")
	default:
	}
}

type inspectionMinimalWriter struct{ recorder *httptest.ResponseRecorder }

func (w inspectionMinimalWriter) Header() http.Header         { return w.recorder.Header() }
func (w inspectionMinimalWriter) WriteHeader(status int)      { w.recorder.WriteHeader(status) }
func (w inspectionMinimalWriter) Write(p []byte) (int, error) { return w.recorder.Write(p) }

type inspectionAllWriter struct {
	inspectionMinimalWriter
	flushes, hijacks, pushes int
}

func (w *inspectionAllWriter) Flush() { w.flushes++ }
func (w *inspectionAllWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	w.hijacks++
	return nil, nil, errors.New("synthetic hijack")
}
func (w *inspectionAllWriter) Push(string, *http.PushOptions) error { w.pushes++; return nil }
func (w *inspectionAllWriter) CloseNotify() <-chan bool             { return make(chan bool) }

func TestInspectionR3WriterOptionalInterfaces(t *testing.T) {
	state := models.NewInspectionState(textInspection("test.interfaces"))
	minimal := inspectionMinimalWriter{httptest.NewRecorder()}
	_, plain := newInspectionWriter(minimal, state)
	if _, ok := plain.(http.Flusher); ok {
		t.Fatal("invented Flusher")
	}
	if _, ok := plain.(http.Hijacker); ok {
		t.Fatal("invented Hijacker")
	}
	if _, ok := plain.(http.Pusher); ok {
		t.Fatal("invented Pusher")
	}
	if _, ok := plain.(http.CloseNotifier); ok {
		t.Fatal("invented CloseNotifier")
	}
	if _, ok := plain.(interface{ Unwrap() http.ResponseWriter }); ok {
		t.Fatal("release boundary exposes unchecked destination")
	}
	all := &inspectionAllWriter{inspectionMinimalWriter: minimal}
	_, wrapped := newInspectionWriter(all, state)
	wrapped.(http.Flusher).Flush()
	_, _, _ = wrapped.(http.Hijacker).Hijack()
	_ = wrapped.(http.Pusher).Push("/asset", nil)
	_ = wrapped.(http.CloseNotifier).CloseNotify()
	if all.flushes != 1 || all.hijacks != 1 || all.pushes != 1 {
		t.Fatalf("optional forwarding: %+v", all)
	}
	state.Require(models.InspectionOutput)
	wrapped.(http.Flusher).Flush()
	_, _, err := wrapped.(http.Hijacker).Hijack()
	pushErr := wrapped.(http.Pusher).Push("/asset", nil)
	if !errors.Is(err, errInspectionRelease) || !errors.Is(pushErr, errInspectionRelease) || all.flushes != 1 || all.hijacks != 1 || all.pushes != 1 {
		t.Fatalf("unchecked optional release: %+v hijack=%v push=%v", all, err, pushErr)
	}
}

func TestInspectionR3RegistrationInventory(t *testing.T) {
	up := httptest.NewServer(&dialectUpstream{reply: "unused"})
	defer up.Close()
	srv, _ := runtimePolicyServer(t, up.URL, &tenant.GuardrailConfig{}, nil)
	defer srv.asyncWorker.Stop()
	srv.router.Handle("post", "/forgotten/{id}", func(http.ResponseWriter, *http.Request) {})
	inventory := srv.router.InspectionInventory()
	if len(inventory) != len(srv.router.static)+func() int {
		n := 0
		for _, routes := range srv.router.paramIndex {
			n += len(routes)
		}
		return n
	}() {
		t.Fatal("inventory does not cover actual registrations")
	}
	found := map[string]RouteInspection{}
	for _, row := range inventory {
		if row.Contract.Operation == "" || row.Auth == "" {
			t.Fatalf("unqualified inventory row: %+v", row)
		}
		found[row.Method+" "+row.Pattern] = row
	}
	for _, key := range []string{"POST /v1/search", "POST /v1/audio/transcriptions", "GET /v1/responses/{id}", "POST /v1/scheduled", "POST /forgotten/{id}"} {
		row, ok := found[key]
		if !ok || row.Contract.Input != models.InspectionUnknown || row.Contract.Output != models.InspectionUnknown {
			t.Fatalf("opaque/forgotten row missing default refusal: %s %+v", key, row)
		}
	}
	row := found["POST /v1/chat/completions"]
	if row.Contract.Input != models.InspectionSupported || row.Contract.Output != models.InspectionSupported {
		t.Fatalf("missing explicit text contract: %+v", row)
	}
	row = found["POST /v1beta/models/{provider}/{model_action}"]
	if row.Discriminator != "genai-action" || row.Auth != inspectionAuthGenAI {
		t.Fatalf("native discriminator missing: %+v", row)
	}
}
