package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestAdmissionHTTPInitialDenialDoesNotDispatch(t *testing.T) {
	for _, d := range []LiveDecision{{Decision: LiveDenied, Reason: LiveReasonDenied}, {Decision: LiveUnavailable, Reason: LiveReasonUnavailable}} {
		s, h, _, a := admissionTestServer(t)
		a.set(d)
		w := &admissionRecordingWriter{ResponseRecorder: httptest.NewRecorder()}
		r, ok := s.admissionAuthenticate(w, admissionTrustedRequest(http.MethodPost))
		if !ok {
			t.Fatal("trusted identity failed before authorization")
		}
		r.Header.Set("Accept", "application/json, text/event-stream")
		s.handleAdmittedPost(w, r, &Message{JSONRPC: "2.0", ID: json.RawMessage("1"), Method: "initialize", Params: json.RawMessage(`{"protocolVersion":"2025-03-26"}`)})
		releaseAdmissionIngress(r)
		want := http.StatusNotFound
		if d.Decision == LiveUnavailable {
			want = http.StatusServiceUnavailable
		}
		if w.Code != want || h.Runtime.(*admissionTestRuntime).calls != 0 {
			t.Fatal("initial refusal reached runtime")
		}
	}
}

func TestAdmissionIngressBoundAndSiblingCapacity(t *testing.T) {
	h := &HostedAdmission{Limits: admissionTestLimits()}
	var releases []func()
	for i := 0; i < h.Limits.ProcessStreams/2; i++ {
		release, err := h.reserveIngress("org-a")
		if err != nil {
			t.Fatal(err)
		}
		releases = append(releases, release)
	}
	if _, err := h.reserveIngress("org-a"); err != errAdmissionLimit {
		t.Fatal("org occupied sibling reserve")
	}
	release, err := h.reserveIngress("org-b")
	if err != nil {
		t.Fatal("sibling denied under saturation")
	}
	release()
	release()
	for _, release := range releases {
		release()
		release()
	}
	if h.ingressCount != 0 || len(h.ingress) != 0 {
		t.Fatal("ingress cancellation leaked local capacity")
	}
}

func TestAdmissionOffReplacementCancelsOnlyItsWatch(t *testing.T) {
	s, h, _, _ := admissionTestServer(t)
	tick := newAdmissionFakeTicker()
	w := newAdmissionWatch(context.Background(), admissionTestRequest(), NewMembershipRevision([]byte("revision-a")), func() LiveAuthorizer { return s.admissionAuthorizer(h) }, tick, nil)
	defer w.close()
	s.SetDeps(Deps{HostedAdmission: &HostedAdmission{Enabled: false}})
	tick.advance()
	admissionAwait(t, w.ctx.Done())
}

func TestAdmissionShutdownAndConcurrentCloseJoinWatch(t *testing.T) {
	a := &admissionTestAuthorizer{decision: admissionAllow()}
	tick := newAdmissionFakeTicker()
	stop := make(chan struct{})
	w := newAdmissionWatch(context.Background(), admissionTestRequest(), NewMembershipRevision([]byte("revision-a")), func() LiveAuthorizer { return a }, tick, nil, stop)
	close(stop)
	admissionAwait(t, w.ctx.Done())
	first, second := make(chan struct{}), make(chan struct{})
	go func() { w.close(); close(first) }()
	go func() { w.close(); close(second) }()
	admissionAwait(t, first)
	admissionAwait(t, second)
}

type admissionCapacityLeases struct{ *admissionFakeLeases }

func (admissionCapacityLeases) Acquire(context.Context, LeaseRequest) (SessionLease, error) {
	return SessionLease{}, LeaseCapacityExceeded
}
func TestAdmissionAdapterCapacityRefusalIsTyped(t *testing.T) {
	f := admissionCapacityLeases{newAdmissionFakeLeases()}
	if _, err := acquireAdmissionLease(context.Background(), f, admissionTestLeaseRequest("lease-a"), time.Now()); err != errAdmissionLimit {
		t.Fatal("adapter capacity refusal lost its class")
	}
}

func TestAdmissionLeaseUsesServerTimeWithoutExtendingAbsolute(t *testing.T) {
	f := newAdmissionFakeLeases()
	f.now = f.now.Add(time.Hour)
	r := admissionTestLeaseRequest("lease-a")
	lease, err := acquireAdmissionLease(context.Background(), f, r, time.Now())
	if err != nil {
		t.Fatal("server clock offset rejected a valid lease")
	}
	defer lease.close()
	f.advance(time.Second)
	if err := lease.renew(context.Background(), true, time.Now()); err != nil {
		t.Fatal(err)
	}
	f.mu.Lock()
	row := f.rows[r.ID]
	row.lease.AbsoluteDeadline = row.lease.AbsoluteDeadline.Add(time.Hour)
	f.rows[r.ID] = row
	f.mu.Unlock()
	if err := lease.renew(context.Background(), true, time.Now()); err != errAdmissionUnavailable {
		t.Fatal("backend extended original absolute ceiling")
	}
}
