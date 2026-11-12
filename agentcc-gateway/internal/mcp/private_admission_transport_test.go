package mcp

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAdmissionSplitEncoderWritesAreOneLogicalUnit(t *testing.T) {
	w, raw, a := admissionUnitFixture(t, LiveDecision{Decision: LiveAllow, Reason: LiveReasonAllowed})
	err := w.reply(func(b http.ResponseWriter) {
		b.Header().Set("Content-Type", "application/json")
		b.WriteHeader(200)
		_, _ = b.Write([]byte("first"))
		if raw.Body.Len() != 0 {
			t.Fatal("first encoder write escaped buffer")
		}
		_, _ = b.Write([]byte("second"))
		if raw.Body.Len() != 0 {
			t.Fatal("second encoder write escaped buffer")
		}
	})
	if err != nil || raw.Body.String() != "firstsecond" || a.calls != 2 {
		t.Fatal("transport split counted as multiple logical units")
	}
	w.close()
	if a.calls != 2 {
		t.Fatal("EOF/flush became another authorization unit")
	}
}

type admissionShortWriter struct {
	*admissionRecordingWriter
	zero  bool
	calls int
}

func (w *admissionShortWriter) Write(p []byte) (int, error) {
	w.calls++
	if w.zero {
		return 0, nil
	}
	_, _ = w.ResponseRecorder.Write(p[:1])
	return 1, io.ErrShortWrite
}
func TestAdmissionPartialAndZeroTransportWriteCannotRetry(t *testing.T) {
	for _, zero := range []bool{false, true} {
		a := &admissionTestAuthorizer{decision: admissionAllow()}
		watch := newAdmissionWatch(context.Background(), admissionTestRequest(), admissionAllow().MembershipRevision, func() LiveAuthorizer { return a }, newAdmissionFakeTicker(), nil)
		raw := &admissionShortWriter{admissionRecordingWriter: &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}, zero: zero}
		w, err := newAdmissionWriter(raw, httptest.NewRequest(http.MethodPost, "/mcp", nil), watch)
		if err != nil {
			watch.close()
			t.Fatal(err)
		}
		unit, _ := w.BeginUnit()
		if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("bounded")}) != errAdmissionUnavailable {
			t.Fatal("partial/zero write treated as complete")
		}
		if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("retry")}) == nil || raw.calls != 1 {
			t.Fatal("failed write replayed bytes")
		}
		w.close()
		watch.close()
	}
}

type admissionFlushFailure struct{ *admissionRecordingWriter }

func (admissionFlushFailure) FlushError() error { return io.ErrClosedPipe }
func TestAdmissionFailedFlushTerminatesWithoutRetry(t *testing.T) {
	a := &admissionTestAuthorizer{decision: admissionAllow()}
	watch := newAdmissionWatch(context.Background(), admissionTestRequest(), admissionAllow().MembershipRevision, func() LiveAuthorizer { return a }, newAdmissionFakeTicker(), nil)
	defer watch.close()
	raw := admissionFlushFailure{&admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}}
	w, err := newAdmissionWriter(raw, httptest.NewRequest(http.MethodPost, "/mcp", nil), watch)
	if err != nil {
		t.Fatal(err)
	}
	defer w.close()
	unit, _ := w.BeginUnit()
	if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("one")}) != errAdmissionUnavailable {
		t.Fatal("failed flush hidden")
	}
	if _, err := w.BeginUnit(); err == nil {
		t.Fatal("failed flush restarted delivery")
	}
	if raw.Body.String() != "one" {
		t.Fatal("flush failure duplicated bytes")
	}
}

func TestAdmissionReleaseReceivesExactOriginalBinding(t *testing.T) {
	w, raw, a := admissionUnitFixture(t, admissionAllow())
	original := a.request
	unit, _ := w.BeginUnit()
	if w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: []byte("one")}) != nil {
		t.Fatal("release refused")
	}
	expected := original
	expected.Phase = PhaseRelease
	if a.request != expected || raw.Body.String() != "one" {
		t.Fatal("release changed trusted binding")
	}
	next, _ := w.BeginUnit()
	if w.ReleaseUnit(next, HostedUnit{Status: 200, Body: []byte("two")}) != nil {
		t.Fatal("revision-bound continuation refused")
	}
	if a.request.Phase != PhaseContinuation || a.request.UnitID == original.UnitID || a.request.RequestID != original.RequestID {
		t.Fatal("continuation reclassified")
	}
}
