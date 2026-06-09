package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestRecovery_NoPanic(t *testing.T) {
	handler := Recovery(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if w.Body.String() != "ok" {
		t.Errorf("expected body 'ok', got %q", w.Body.String())
	}
}

func TestRecovery_WithPanic(t *testing.T) {
	handler := Recovery(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic("test panic")
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d", w.Code)
	}
}

func TestRequestID_SetsHeaders(t *testing.T) {
	var capturedRequestID string
	handler := RequestID(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedRequestID = models.GetRequestID(r.Context())
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	requestID := w.Header().Get("x-agentcc-request-id")
	if requestID == "" {
		t.Error("expected x-agentcc-request-id header to be set")
	}

	traceID := w.Header().Get("x-agentcc-trace-id")
	if traceID == "" {
		t.Error("expected x-agentcc-trace-id header to be set")
	}

	if capturedRequestID != requestID {
		t.Errorf("expected context request ID %q to match header %q", capturedRequestID, requestID)
	}
}

func TestRequestID_PreservesCallerTraceID(t *testing.T) {
	handler := RequestID(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	req.Header.Set("x-agentcc-trace-id", "my-trace-123")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	traceID := w.Header().Get("x-agentcc-trace-id")
	if traceID != "my-trace-123" {
		t.Errorf("expected trace ID 'my-trace-123', got %q", traceID)
	}
}

func TestRequestID_UniquePerRequest(t *testing.T) {
	handler := RequestID(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	ids := make(map[string]bool)
	for i := 0; i < 100; i++ {
		req := httptest.NewRequest("GET", "/test", nil)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)

		id := w.Header().Get("x-agentcc-request-id")
		if ids[id] {
			t.Errorf("duplicate request ID: %s", id)
		}
		ids[id] = true
	}
}

func TestTimeout_DefaultTimeout(t *testing.T) {
	var deadline time.Time
	var hasDeadline bool

	handler := Timeout(5 * time.Second)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		deadline, hasDeadline = r.Context().Deadline()
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if !hasDeadline {
		t.Error("expected context to have a deadline")
	}

	// Deadline should be roughly 5 seconds from now (allow some slack).
	remaining := time.Until(deadline)
	if remaining < 4*time.Second || remaining > 6*time.Second {
		t.Errorf("expected deadline ~5s from now, got %v", remaining)
	}
}

func TestTimeout_CustomHeaderOverride(t *testing.T) {
	var deadline time.Time
	var hasDeadline bool

	handler := Timeout(60 * time.Second)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		deadline, hasDeadline = r.Context().Deadline()
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	req.Header.Set("x-agentcc-request-timeout", "2000") // 2 seconds
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if !hasDeadline {
		t.Error("expected context to have a deadline")
	}

	remaining := time.Until(deadline)
	if remaining < 1*time.Second || remaining > 3*time.Second {
		t.Errorf("expected deadline ~2s from now, got %v", remaining)
	}
}

func TestTimeout_InvalidHeaderUsesDefault(t *testing.T) {
	var hasDeadline bool

	handler := Timeout(5 * time.Second)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, hasDeadline = r.Context().Deadline()
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("GET", "/test", nil)
	req.Header.Set("x-agentcc-request-timeout", "not-a-number")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if !hasDeadline {
		t.Error("expected context to have a deadline with default timeout")
	}
}
