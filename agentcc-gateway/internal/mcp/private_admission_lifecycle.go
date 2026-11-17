package mcp

import (
	"context"
	"sync"
)

// Registration includes acquisition and rollback, not only the watch loop.
// Closing seals registration before waiting; Done follows lease AND capacity
// cleanup. Neither waiting nor cancellation holds HostedAdmission.mu.
type admissionLifecycle struct {
	mu      sync.Mutex
	closing bool
	done    chan struct{}
	workers map[*admissionSessionWorker]struct{}
	wg      sync.WaitGroup
}

type admissionSessionWorker struct {
	lifecycle *admissionLifecycle
	ctx       context.Context
	cancel    context.CancelFunc
	once      sync.Once
}

func (s *Server) trackAdmissionSession() (*admissionSessionWorker, error) {
	l := &s.admissionLifecycle
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.closing {
		return nil, errAdmissionUnavailable
	}
	ctx, cancel := context.WithCancel(context.Background())
	w := &admissionSessionWorker{lifecycle: l, ctx: ctx, cancel: cancel}
	if l.workers == nil {
		l.workers = make(map[*admissionSessionWorker]struct{})
	}
	l.workers[w] = struct{}{}
	l.wg.Add(1)
	return w, nil
}

// Setup is request-cancellable; a published session outlives its request but
// remains owned by the server until its complete cleanup has finished.
func (w *admissionSessionWorker) requestContext(parent context.Context) (context.Context, func()) {
	ctx, cancel := context.WithCancel(parent)
	stop := context.AfterFunc(w.ctx, cancel)
	if w.ctx.Err() != nil {
		cancel()
	}
	return ctx, func() { stop(); cancel() }
}

// The caller holds HostedAdmission.mu. This callback only publishes local state
// and starts the watch; it must not perform IO or wait for another worker.
func (w *admissionSessionWorker) publish(start func()) error {
	w.lifecycle.mu.Lock()
	defer w.lifecycle.mu.Unlock()
	if w.lifecycle.closing || w.ctx.Err() != nil {
		return errAdmissionUnavailable
	}
	start()
	return nil
}

func (w *admissionSessionWorker) finish() {
	w.once.Do(func() {
		w.cancel()
		w.lifecycle.mu.Lock()
		delete(w.lifecycle.workers, w)
		w.lifecycle.mu.Unlock()
		w.lifecycle.wg.Done()
	})
}

// Close and Shutdown share one completion, including concurrent and repeated
// callers. Seal synchronously, so even an already-expired Shutdown context
// cannot leave a window in which new session workers register.
func (s *Server) beginClose() <-chan struct{} {
	l := &s.admissionLifecycle
	l.mu.Lock()
	if l.closing {
		done := l.done
		l.mu.Unlock()
		return done
	}
	l.closing = true
	l.done = make(chan struct{})
	done := l.done
	cancels := make([]context.CancelFunc, 0, len(l.workers))
	for w := range l.workers {
		cancels = append(cancels, w.cancel)
	}
	l.mu.Unlock()
	for _, cancel := range cancels {
		cancel()
	}
	go func() {
		s.closeResources()
		l.wg.Wait()
		close(done)
	}()
	return done
}
