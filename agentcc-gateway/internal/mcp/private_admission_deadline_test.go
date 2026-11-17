package mcp

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
)

type admissionDeadlineRecorder struct {
	*admissionRecordingWriter
	mu        sync.Mutex
	deadlines []time.Time
	flush     func() error
}

func (w *admissionDeadlineRecorder) SetWriteDeadline(d time.Time) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.deadlines = append(w.deadlines, d)
	return nil
}
func (w *admissionDeadlineRecorder) FlushError() error {
	if w.flush != nil {
		return w.flush()
	}
	return nil
}
func (w *admissionDeadlineRecorder) last() time.Time {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.deadlines[len(w.deadlines)-1]
}
func admissionDeadlineFixture(t *testing.T) (*admissionWriter, *admissionDeadlineRecorder, *admissionWatch) {
	t.Helper()
	a := &admissionTestAuthorizer{decision: admissionAllow()}
	watch := newAdmissionWatch(context.Background(), admissionTestRequest(), admissionAllow().MembershipRevision, func() LiveAuthorizer { return a }, newAdmissionFakeTicker(), nil)
	t.Cleanup(watch.close)
	raw := &admissionDeadlineRecorder{admissionRecordingWriter: &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}}
	w, err := newAdmissionWriter(raw, httptest.NewRequest(http.MethodPost, "/mcp", nil), watch)
	if err != nil {
		t.Fatal("deadline-capable writer refused")
	}
	t.Cleanup(w.close)
	return w, raw, watch
}

func TestAdmissionDeadlineOnlyArmedDuringUnitAndFinalization(t *testing.T) {
	w, raw, _ := admissionDeadlineFixture(t)
	if !raw.last().IsZero() {
		t.Fatal("capability probe armed an idle deadline")
	}
	raw.flush = func() error {
		d := raw.last()
		if d.IsZero() || !d.After(time.Now()) || time.Until(d) > admissionWriteTimeout {
			t.Error("flush lacks its finite write budget")
		}
		return nil
	}
	unit, _ := w.BeginUnit()
	if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("one")}) != nil {
		t.Fatal("unit failed")
	}
	if !raw.last().IsZero() {
		t.Fatal("completed unit left a deadline armed between writes")
	}
	w.close()
	if d := raw.last(); d.IsZero() || !d.After(time.Now()) || time.Until(d) > admissionWriteTimeout {
		t.Fatal("response finalization lost its finite deadline")
	}
}

func TestAdmissionCancelDuringFlushNeverClearsTerminalDeadline(t *testing.T) {
	w, raw, watch := admissionDeadlineFixture(t)
	entered, finish := make(chan struct{}), make(chan struct{})
	var once sync.Once
	unblock := func() { once.Do(func() { close(finish) }) }
	defer unblock()
	raw.flush = func() error { close(entered); <-finish; return nil }
	done := make(chan error, 1)
	go func() {
		unit, _ := w.BeginUnit()
		done <- w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("one")})
	}()
	admissionAwait(t, entered)
	watch.cancel()
	admissionAwait(t, w.stopped) // Must not wait behind ReleaseUnit's mutex.
	terminal := raw.last()
	if terminal.IsZero() || terminal.After(time.Now()) {
		t.Fatal("cancellation did not force an immediate transport deadline")
	}
	unblock()
	if err := <-done; err == nil {
		t.Fatal("flush completion raced cancellation into success")
	}
	_ = w.SetWriteDeadline(time.Time{})
	w.close()
	if d := raw.last(); d.IsZero() || d.After(terminal) {
		t.Fatal("terminal forced deadline was cleared or extended")
	}
}

func TestAdmissionRecoveryFlushFailureCancelsLiveWatch(t *testing.T) {
	a := &admissionTestAuthorizer{decision: admissionAllow()}
	watch := newAdmissionWatch(context.Background(), admissionTestRequest(), admissionAllow().MembershipRevision, func() LiveAuthorizer { return a }, newAdmissionFakeTicker(), nil)
	defer watch.close()
	raw := admissionFlushFailure{&admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}}
	req := httptest.NewRequest(http.MethodPost, "/mcp", nil)
	middleware.Recovery(http.HandlerFunc(func(rw http.ResponseWriter, r *http.Request) {
		w, err := newAdmissionWriter(rw, r, watch)
		if err != nil {
			t.Fatal("mounted writer setup failed")
		}
		defer w.close()
		if watch.ctx.Err() != nil || r.Context().Err() != nil {
			t.Fatal("flush-only fixture started cancelled")
		}
		unit, _ := w.BeginUnit()
		if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("one")}) != errAdmissionUnavailable {
			t.Fatal("Recovery masked the underlying flush error")
		}
		if watch.ctx.Err() == nil || r.Context().Err() != nil {
			t.Fatal("flush failure did not independently cancel upstream")
		}
		if _, err := w.BeginUnit(); err == nil {
			t.Fatal("failed mounted flush allowed another unit")
		}
		if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("retry")}) == nil || raw.Body.String() != "one" {
			t.Fatal("failed mounted flush allowed replay")
		}
	})).ServeHTTP(raw, req)
}
