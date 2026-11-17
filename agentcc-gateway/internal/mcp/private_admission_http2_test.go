package mcp

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
)

// Real TLS HTTP/2 is required: recorder deadlines cannot reset an idle stream.
func admissionHTTP2Server(t *testing.T, handler http.Handler) *httptest.Server {
	t.Helper()
	srv := httptest.NewUnstartedServer(middleware.Recovery(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.ProtoMajor != 2 {
			t.Error("test request did not negotiate HTTP/2")
			return
		}
		handler.ServeHTTP(w, r)
	})))
	srv.EnableHTTP2 = true
	srv.StartTLS()
	t.Cleanup(srv.Close)
	return srv
}

func admissionHTTP2Identity(s *Server, w http.ResponseWriter, r *http.Request) (*http.Request, bool) {
	p := Principal{Kind: PrincipalKey, OrgID: "org-a", UserID: "person-a", KeyID: "key-a"}
	return s.admissionAuthenticate(w, admissionRoute(r.WithContext(context.WithValue(r.Context(), sessionPrincipalKey{}, p))))
}

func TestAdmissionHTTP2QuietStreamReachesKeepalive(t *testing.T) {
	for _, event := range []bool{false, true} {
		name := "connected"
		if event {
			name = "metadata-event"
		}
		t.Run(name, func(t *testing.T) {
			s, h, _, _ := admissionTestServer(t)
			h.Limits.Idle, h.Limits.Absolute = time.Minute, time.Minute
			token, sess, err := s.createAdmissionSession(context.Background(), h, admissionTestRequest(), admissionAllow().MembershipRevision, "", "2025-03-26")
			if err != nil {
				t.Fatal("session setup failed")
			}
			finished := make(chan struct{})
			srv := admissionHTTP2Server(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				defer close(finished)
				r, ok := admissionHTTP2Identity(s, w, r)
				if !ok {
					return
				}
				defer releaseAdmissionIngress(r)
				s.handleAdmittedGet(w, r)
			}))
			ctx, cancel := context.WithTimeout(context.Background(), streamKeepAlive+8*time.Second)
			defer cancel()
			req, _ := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL, nil)
			req.Header.Set("MCP-Session-Id", token)
			resp, err := srv.Client().Do(req)
			if err != nil {
				t.Fatal("HTTP/2 stream failed before connected frame")
			}
			defer resp.Body.Close()
			if resp.ProtoMajor != 2 || resp.StatusCode != http.StatusOK {
				t.Fatal("expected successful HTTP/2 stream")
			}
			reader := bufio.NewReader(resp.Body)
			if frame := admissionReadFrame(t, reader); frame != ": connected\n\n" {
				t.Fatal("missing connected frame")
			}
			if event {
				if !s.sessions.hub.deliver(sess.hash, streamEvent{id: "0-1", data: []byte(`{"jsonrpc":"2.0","method":"notifications/resources/list_changed"}`)}) {
					t.Fatal("event was not queued")
				}
				if frame := admissionReadFrame(t, reader); frame == ": keepalive\n\n" {
					t.Fatal("metadata event was not delivered")
				}
			}
			start := time.Now()
			if frame := admissionReadFrame(t, reader); frame != ": keepalive\n\n" {
				t.Fatal("quiet stream did not reach keepalive")
			}
			if time.Since(start) <= admissionWriteTimeout {
				t.Fatal("quiet interval did not cross the write timeout")
			}
			cancel()
			admissionAwait(t, finished)
		})
	}
}

func admissionReadFrame(t *testing.T, reader *bufio.Reader) string {
	t.Helper()
	var frame string
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			t.Fatal("authorized idle HTTP/2 stream reset before next frame")
		}
		frame += line
		if line == "\n" {
			return frame
		}
	}
}

type admissionDelayedRuntime struct {
	entered chan struct{}
	result  chan error
}

func (rt *admissionDelayedRuntime) ListMetadata(w HostedUnitSink, r *http.Request, _ *Message, _ LiveAuthorizationRequest) {
	close(rt.entered)
	timer := time.NewTimer(admissionWriteTimeout + 500*time.Millisecond)
	defer timer.Stop()
	select {
	case <-r.Context().Done():
		rt.result <- r.Context().Err()
		return
	case <-timer.C:
	}
	unit, err := w.BeginUnit()
	if err == nil {
		err = w.ReleaseUnit(unit, HostedUnit{Status: 200, MediaType: "application/json", Body: []byte(`{"result":"delayed"}`)})
	}
	rt.result <- err
}
func (rt *admissionDelayedRuntime) DispatchContent(w HostedUnitSink, r *http.Request, m *Message, a LiveAuthorizationRequest) {
	rt.ListMetadata(w, r, m, a)
}

func TestAdmissionHTTP2DelayedFirstUnitSurvives(t *testing.T) {
	s, h, _, _ := admissionTestServer(t)
	h.Limits.Idle, h.Limits.Absolute = time.Minute, time.Minute
	rt := &admissionDelayedRuntime{entered: make(chan struct{}), result: make(chan error, 1)}
	h.Runtime = rt
	token, _, err := s.createAdmissionSession(context.Background(), h, admissionTestRequest(), admissionAllow().MembershipRevision, "", "2025-03-26")
	if err != nil {
		t.Fatal("session setup failed")
	}
	srv := admissionHTTP2Server(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		r, ok := admissionHTTP2Identity(s, w, r)
		if !ok {
			return
		}
		defer releaseAdmissionIngress(r)
		s.handleAdmittedPost(w, r, &Message{JSONRPC: "2.0", ID: json.RawMessage("1"), Method: "resources/list"})
	}))
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, srv.URL, nil)
	req.Header.Set("MCP-Session-Id", token)
	req.Header.Set("MCP-Protocol-Version", "2025-03-26")
	req.Header.Set("Accept", "application/json, text/event-stream")
	resp, err := srv.Client().Do(req)
	admissionAwait(t, rt.entered)
	if err != nil {
		t.Fatal("HTTP/2 deadline expired during authorized computation")
	}
	defer resp.Body.Close()
	if resp.ProtoMajor != 2 || resp.StatusCode != http.StatusOK {
		t.Fatal("expected successful HTTP/2 response")
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil || string(body) != `{"result":"delayed"}` {
		t.Fatal("delayed first unit did not finish")
	}
	if err := <-rt.result; err != nil {
		t.Fatal("runtime release failed after computation")
	}
}
