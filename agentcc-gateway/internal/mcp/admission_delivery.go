package mcp

import (
	"context"
	"net/http"
	"sync"
	"time"
)

type admissionOperation struct {
	watch       *admissionWatch
	lease       *admissionLease
	release     func()
	cancel      context.CancelFunc
	stopSession func() bool
}

func (s *Server) beginAdmissionOperation(ctx context.Context, h *HostedAdmission, a LiveAuthorizationRequest, revision MembershipRevision, purpose LeasePurpose, sess *admissionSession) (*admissionOperation, error) {
	release, err := h.reserve(purpose)
	if err != nil {
		return nil, err
	}
	id, err := admissionRandomID()
	if err != nil {
		release()
		return nil, err
	}
	hash := id
	if sess != nil {
		hash = sess.hash
	}
	lifetime := h.Limits.Absolute
	if purpose == LeaseCall {
		lifetime = min(lifetime, 30*time.Second)
	}
	r := LeaseRequest{ID: id, Scope: a.Scope, Actor: a.Actor, SessionHash: hash, Purpose: purpose, Limits: h.leaseLimits(purpose), TTL: min(3*time.Second, lifetime), Idle: lifetime, Absolute: lifetime}
	lease, err := acquireAdmissionLease(ctx, s.Deps().SessionLeases, r, time.Now())
	if err != nil {
		release()
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, lifetime)
	op := &admissionOperation{lease: lease, release: release, cancel: cancel}
	if sess != nil {
		op.stopSession = context.AfterFunc(sess.watch.ctx, cancel)
	}
	op.watch = newAdmissionWatch(ctx, a, revision, func() LiveAuthorizer { return s.admissionAuthorizer(h) }, admissionRealTicker{time.NewTicker(admissionTick)}, func(ctx context.Context) error { return lease.renew(ctx, false, time.Now()) })
	return op, nil
}
func (o *admissionOperation) close() {
	o.cancel()
	o.watch.close()
	if o.stopSession != nil {
		o.stopSession()
	}
	_ = o.lease.close()
	o.release()
}

// Not an http.ResponseWriter: raw Write/Header/Flush cannot escape unit gating.
// One ticket is outstanding at a time. Claim, RELEASE authorization and consume
// are serialized; transport failure is terminal, never a retry of a first unit.
// Watch cancellation uses a separate lock and can interrupt a blocked write.
type admissionWriter struct {
	w         http.ResponseWriter
	watch     *admissionWatch
	mu        sync.Mutex
	deadline  *admissionWriteDeadline
	finalize  bool
	err       error
	pending   *AdmissionUnit
	issued    bool
	closed    bool
	wrote     bool
	status    int
	media     string
	header    http.Header
	stop      func() bool
	stopped   chan struct{}
	closeOnce sync.Once
}

func newAdmissionWriter(w http.ResponseWriter, r *http.Request, watch *admissionWatch) (*admissionWriter, error) {
	return newAdmissionWriterWithDeadline(w, r, watch, nil)
}
func newAdmissionWriterWithDeadline(w http.ResponseWriter, r *http.Request, watch *admissionWatch, deadline *admissionWriteDeadline) (*admissionWriter, error) {
	aw := &admissionWriter{w: w, watch: watch, stopped: make(chan struct{}), header: make(http.Header), deadline: deadline, finalize: deadline == nil}
	if aw.deadline == nil {
		aw.deadline = &admissionWriteDeadline{writer: w}
	}
	// Probe support without starting a timer during computation or idle time.
	if err := aw.SetWriteDeadline(time.Time{}); err != nil {
		return nil, errAdmissionUnavailable
	}
	aw.stop = context.AfterFunc(watch.ctx, func() {
		defer close(aw.stopped)
		_ = aw.SetWriteDeadline(time.Now())
		if r.Body != nil {
			_ = r.Body.Close()
		}
	})
	return aw, nil
}
func (w *admissionWriter) SetWriteDeadline(t time.Time) error {
	return w.deadline.set(w.watch.ctx, t)
}
func (w *admissionWriter) fail(err error) error {
	if w.err == nil {
		w.err = err
	}
	w.pending = nil
	w.watch.cancel()
	return w.err
}
func (w *admissionWriter) BeginUnit() (AdmissionUnit, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.err != nil {
		return AdmissionUnit{}, w.err
	}
	if w.closed || w.watch.ctx.Err() != nil {
		return AdmissionUnit{}, errAdmissionDenied
	}
	if w.pending != nil {
		return AdmissionUnit{}, errAdmissionDenied
	}
	binding := w.watch.request
	if w.issued {
		id, err := admissionRandomID()
		if err != nil {
			return AdmissionUnit{}, w.fail(err)
		}
		binding.UnitID = id
		binding.Phase = PhaseContinuation
	}
	ticket := AdmissionUnit{owner: w, binding: binding}
	w.pending = &ticket
	w.issued = true
	return ticket, nil
}
func (w *admissionWriter) ReleaseUnit(ticket AdmissionUnit, unit HostedUnit) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.err != nil {
		return w.err
	}
	if w.closed || w.pending == nil || *w.pending != ticket || ticket.owner != w {
		return w.fail(errAdmissionDenied)
	}
	// Snapshot a finite logical body BEFORE authorization. The runtime must not
	// mutate its slice concurrently. No authorization is inferred from JSON or
	// the transport's number of writes. A zero-byte response still consumes one.
	if len(unit.Body) > admissionUnitMaxBytes || unit.Status < 200 || unit.Status > 599 || (unit.Status == 204 || unit.Status == 304) && len(unit.Body) != 0 {
		return w.fail(errAdmissionDenied)
	}
	if unit.MediaType != "" && unit.MediaType != "application/json" && unit.MediaType != "text/event-stream" {
		return w.fail(errAdmissionDenied)
	}
	if w.wrote && (unit.Status != w.status || unit.MediaType != w.media) {
		return w.fail(errAdmissionDenied)
	}
	body := append([]byte(nil), unit.Body...)
	w.pending = nil // Any error, short write or failed flush burns this claim.
	if err := w.watch.releaseUnit(ticket.binding.UnitID); err != nil {
		return w.fail(err)
	}
	if err := w.SetWriteDeadline(time.Now().Add(admissionWriteTimeout)); err != nil {
		return w.fail(errAdmissionUnavailable)
	}
	if w.watch.ctx.Err() != nil {
		return w.fail(errAdmissionDenied)
	}
	if !w.wrote {
		for k, values := range w.header {
			w.w.Header()[k] = append([]string(nil), values...)
		}
		if unit.MediaType != "" {
			w.w.Header().Set("Content-Type", unit.MediaType)
		}
		w.status, w.media, w.wrote = unit.Status, unit.MediaType, true
		w.w.WriteHeader(unit.Status)
	}
	if len(body) != 0 {
		n, err := w.w.Write(body)
		if err != nil || n != len(body) {
			return w.fail(errAdmissionUnavailable)
		}
	}
	// Flush belongs to THIS unit, not another authorization/continuation. It
	// completes under the same two-second deadline. No public ungated Flush.
	if err := http.NewResponseController(w.w).Flush(); err != nil {
		return w.fail(errAdmissionUnavailable)
	}
	if err := w.SetWriteDeadline(time.Time{}); err != nil {
		return w.fail(errAdmissionUnavailable)
	}
	if w.watch.ctx.Err() != nil {
		return w.fail(errAdmissionDenied)
	}
	return nil
}
func (w *admissionWriter) failed() bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.err != nil || w.watch.ctx.Err() != nil
}
func (w *admissionWriter) close() {
	w.closeOnce.Do(func() {
		w.mu.Lock()
		w.closed = true
		if w.pending != nil {
			w.fail(errAdmissionDenied)
		}
		w.mu.Unlock()
		if !w.stop() {
			<-w.stopped
		}
		// Only the response owner finalizes net/http. A nested event sink leaves
		// the shared stream idle; neither can undo a terminal forced deadline.
		var deadline time.Time
		if w.finalize {
			deadline = time.Now().Add(admissionWriteTimeout)
		}
		if w.failed() {
			deadline = time.Now()
		}
		_ = w.SetWriteDeadline(deadline)
	})
}
func (s *Server) admissionBodyDeadline(w http.ResponseWriter, r *http.Request) (func(), error) {
	if !s.hostedAdmissionRequired() {
		return func() {}, nil
	}
	rc := http.NewResponseController(w)
	if err := rc.SetReadDeadline(time.Now().Add(admissionWriteTimeout)); err != nil {
		return nil, errAdmissionUnavailable
	}
	return func() { _ = rc.SetReadDeadline(time.Time{}) }, nil
}
