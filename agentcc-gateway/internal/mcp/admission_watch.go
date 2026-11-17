package mcp

import (
	"context"
	"sync"
	"time"
)

// admissionTicker is a bounded, drop-on-lag tick source, never a watch queue.
type admissionTicker interface {
	Chan() <-chan time.Time
	Stop()
}
type admissionRealTicker struct{ *time.Ticker }

func (t admissionRealTicker) Chan() <-chan time.Time { return t.C }

type admissionWatch struct {
	ctx        context.Context
	cancel     context.CancelFunc
	exited     chan struct{}
	mu         sync.Mutex
	request    LiveAuthorizationRequest
	revision   MembershipRevision
	released   bool
	lastUnit   string
	authorizer func() LiveAuthorizer
	maintain   func(context.Context) error
	ticker     admissionTicker
	stop       <-chan struct{}
}

func newAdmissionWatch(parent context.Context, r LiveAuthorizationRequest, revision MembershipRevision, authorizer func() LiveAuthorizer, tick admissionTicker, maintain func(context.Context) error, stops ...<-chan struct{}) *admissionWatch {
	ctx, cancel := context.WithCancel(parent)
	w := &admissionWatch{ctx: ctx, cancel: cancel, exited: make(chan struct{}), request: r, revision: revision, authorizer: authorizer, maintain: maintain, ticker: tick}
	if len(stops) > 0 {
		w.stop = stops[0]
	}
	go w.loop()
	return w
}
func (w *admissionWatch) check() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	unit := w.lastUnit
	if unit == "" {
		unit = w.request.UnitID
	}
	return w.authorizeLocked(unit, false)
}

// Only the sink may advance delivery state. Periodic checks are fresh but do
// not consume a unit. A successful release check consumes it even if the later
// transport fails; the writer cannot retry or reset it into INITIAL/RELEASE.
func (w *admissionWatch) releaseUnit(unit string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.authorizeLocked(unit, true)
}
func (w *admissionWatch) authorizeLocked(unit string, release bool) error {
	if w.ctx.Err() != nil {
		return errAdmissionDenied
	}
	r := w.request
	r.UnitID = unit
	if r.Phase == PhaseInitial {
		r.Phase = PhaseRelease
		if w.released {
			r.Phase = PhaseContinuation
		}
	}
	if !w.released && unit != w.request.UnitID {
		w.cancel()
		return errAdmissionDenied
	}
	if _, err := authorizeLive(w.ctx, w.authorizer(), r, w.revision); err != nil {
		w.cancel()
		return err
	}
	if w.maintain != nil {
		if err := w.maintain(w.ctx); err != nil {
			w.cancel()
			return err
		}
	}
	if w.ctx.Err() != nil {
		return errAdmissionDenied
	}
	if release {
		w.released = true
		w.lastUnit = unit
	}
	return nil
}
func (w *admissionWatch) loop() {
	defer close(w.exited)
	defer w.ticker.Stop()
	for {
		select {
		case <-w.ctx.Done():
			return
		case <-w.stop:
			w.cancel()
			return
		case <-w.ticker.Chan():
			if w.check() != nil {
				return
			}
		}
	}
}
func (w *admissionWatch) close() { w.cancel(); <-w.exited }
