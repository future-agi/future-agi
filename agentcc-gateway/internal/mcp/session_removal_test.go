package mcp

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

type admissionFakeTicker struct {
	ch      chan time.Time
	stopped chan struct{}
	once    sync.Once
}

func newAdmissionFakeTicker() *admissionFakeTicker {
	return &admissionFakeTicker{ch: make(chan time.Time, 1), stopped: make(chan struct{})}
}
func (t *admissionFakeTicker) Chan() <-chan time.Time { return t.ch }
func (t *admissionFakeTicker) Stop()                  { t.once.Do(func() { close(t.stopped) }) }
func (t *admissionFakeTicker) advance()               { t.ch <- time.Unix(1, 0) }
func admissionAwait(t *testing.T, ch <-chan struct{}) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(2 * time.Second):
		t.Fatal("cancellation did not finish")
	}
}

func TestAdmissionTickRemovalAndSiblingContinuity(t *testing.T) {
	for _, decision := range []LiveDecision{{Decision: LiveDenied, Reason: LiveReasonDenied}, {Decision: LiveUnavailable, Reason: LiveReasonUnavailable}, {Decision: LiveAllow, Reason: LiveReasonAllowed, MembershipRevision: NewMembershipRevision([]byte("new-revision"))}} {
		a := &admissionTestAuthorizer{decision: admissionAllow()}
		sibling := &admissionTestAuthorizer{decision: admissionAllow()}
		tick, siblingTick := newAdmissionFakeTicker(), newAdmissionFakeTicker()
		w := newAdmissionWatch(context.Background(), admissionTestRequest(), NewMembershipRevision([]byte("revision-a")), func() LiveAuthorizer { return a }, tick, nil)
		s := newAdmissionWatch(context.Background(), admissionTestRequest(), NewMembershipRevision([]byte("revision-a")), func() LiveAuthorizer { return sibling }, siblingTick, nil)
		a.set(decision)
		tick.advance()
		admissionAwait(t, w.ctx.Done())
		w.close()
		if s.check() != nil || s.ctx.Err() != nil {
			t.Fatal("sibling was cancelled")
		}
		s.close()
		admissionAwait(t, tick.stopped)
		admissionAwait(t, siblingTick.stopped)
	}
}
func TestAdmissionMissingAuthorizerAndLeaseRenewalCancel(t *testing.T) {
	for _, absent := range []bool{false, true} {
		a := &admissionTestAuthorizer{decision: admissionAllow()}
		tick := newAdmissionFakeTicker()
		w := newAdmissionWatch(context.Background(), admissionTestRequest(), NewMembershipRevision([]byte("revision-a")), func() LiveAuthorizer {
			if absent {
				return nil
			}
			return a
		}, tick, func(context.Context) error { return errAdmissionUnavailable })
		tick.advance()
		admissionAwait(t, w.ctx.Done())
		w.close()
	}
}

type admissionBlockingWriter struct {
	mu       sync.Mutex
	h        http.Header
	deadline time.Time
	entered  chan struct{}
	unblock  chan struct{}
	once     sync.Once
}

func newAdmissionBlockingWriter() *admissionBlockingWriter {
	return &admissionBlockingWriter{h: http.Header{}, entered: make(chan struct{}), unblock: make(chan struct{})}
}
func (w *admissionBlockingWriter) Header() http.Header { return w.h }
func (w *admissionBlockingWriter) WriteHeader(int)     {}
func (w *admissionBlockingWriter) Write([]byte) (int, error) {
	close(w.entered)
	<-w.unblock
	return 0, io.ErrClosedPipe
}
func (w *admissionBlockingWriter) FlushError() error { return nil }
func (w *admissionBlockingWriter) SetWriteDeadline(d time.Time) error {
	w.mu.Lock()
	w.deadline = d
	w.mu.Unlock()
	if !d.IsZero() && !d.After(time.Now()) {
		w.once.Do(func() { close(w.unblock) })
	}
	return nil
}

type admissionBody struct {
	closed chan struct{}
	once   sync.Once
}

func (b *admissionBody) Read([]byte) (int, error) { <-b.closed; return 0, io.EOF }
func (b *admissionBody) Close() error             { b.once.Do(func() { close(b.closed) }); return nil }

func TestAdmissionRemovalInterruptsWriterAndReader(t *testing.T) {
	a := &admissionTestAuthorizer{decision: admissionAllow()}
	tick := newAdmissionFakeTicker()
	watch := newAdmissionWatch(context.Background(), admissionTestRequest(), NewMembershipRevision([]byte("revision-a")), func() LiveAuthorizer { return a }, tick, nil)
	defer watch.close()
	body := &admissionBody{closed: make(chan struct{})}
	r := httptest.NewRequest(http.MethodPost, "/mcp", nil)
	r.Body = body
	raw := newAdmissionBlockingWriter()
	w, err := newAdmissionWriter(raw, r, watch)
	if err != nil {
		t.Fatal(err)
	}
	defer w.close()
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		unit, _ := w.BeginUnit()
		_ = w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("synthetic")})
	}()
	admissionAwait(t, raw.entered)
	a.set(LiveDecision{Decision: LiveDenied, Reason: LiveReasonDenied})
	tick.advance()
	admissionAwait(t, finished)
	admissionAwait(t, body.closed)
	if err := w.SetWriteDeadline(time.Now().Add(time.Hour)); err != nil {
		t.Fatal(err)
	}
	raw.mu.Lock()
	deadline := raw.deadline
	raw.mu.Unlock()
	if deadline.After(time.Now()) {
		t.Fatal("cancelled writer extended its deadline")
	}
}
func TestAdmissionDeliveryRechecksAndRequiresDeadlines(t *testing.T) {
	a := &admissionTestAuthorizer{decision: admissionAllow()}
	tick := newAdmissionFakeTicker()
	watch := newAdmissionWatch(context.Background(), admissionTestRequest(), NewMembershipRevision([]byte("revision-a")), func() LiveAuthorizer { return a }, tick, nil)
	defer watch.close()
	r := httptest.NewRequest(http.MethodGet, "/mcp", nil)
	if _, err := newAdmissionWriter(httptest.NewRecorder(), r, watch); err != errAdmissionUnavailable {
		t.Fatal("writer without deadline support admitted")
	}
	raw := &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}
	w, err := newAdmissionWriter(raw, r, watch)
	if err != nil {
		t.Fatal(err)
	}
	defer w.close()
	unit, _ := w.BeginUnit()
	if err := w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("one")}); err != nil {
		t.Fatal(err)
	}
	a.set(LiveDecision{Decision: LiveUnavailable, Reason: LiveReasonUnavailable})
	next, _ := w.BeginUnit()
	if err := w.ReleaseUnit(next, HostedUnit{Status: 200, Body: []byte("two")}); err == nil {
		t.Fatal("delivery reused stale authorization")
	}
	if raw.Body.String() != "one" {
		t.Fatal("unauthorized content reached response")
	}
}

type admissionRecordingWriter struct{ *httptest.ResponseRecorder }

func (*admissionRecordingWriter) SetWriteDeadline(time.Time) error { return nil }
func (*admissionRecordingWriter) SetReadDeadline(time.Time) error  { return nil }

func TestAdmissionStreamLimitAndIdempotentRemoval(t *testing.T) {
	h := newStreamHub()
	defer h.close()
	a, ok := h.registerLimited("session-a", 1)
	if !ok {
		t.Fatal("first stream refused")
	}
	if _, ok := h.registerLimited("session-a", 1); ok {
		t.Fatal("stream cap bypassed")
	}
	b, ok := h.registerLimited("session-b", 1)
	if !ok {
		t.Fatal("sibling refused")
	}
	h.endSession("session-a")
	h.endSession("session-a")
	admissionAwait(t, a.done)
	select {
	case <-b.done:
		t.Fatal("sibling stream ended")
	default:
	}
	h.unregister("session-a", a)
}
