package mcp

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

type admissionReleaseBarrier struct {
	*admissionFakeLeases
	entered chan struct{}
	allow   chan struct{}
	once    sync.Once
}

func (f *admissionReleaseBarrier) Release(ctx context.Context, id string) error {
	f.once.Do(func() { close(f.entered) })
	select {
	case <-f.allow:
		return f.admissionFakeLeases.Release(ctx, id)
	case <-ctx.Done():
		return ctx.Err()
	}
}
func admissionShutdownFixture(t *testing.T) (*Server, *HostedAdmission, *admissionReleaseBarrier, *admissionSession, func()) {
	t.Helper()
	s, h, base, a := admissionTestServer(t)
	f := &admissionReleaseBarrier{admissionFakeLeases: base, entered: make(chan struct{}), allow: make(chan struct{})}
	s.SetDeps(Deps{HostedAdmission: h, SessionLeases: f, LiveAuthorizer: a})
	var once sync.Once
	unblock := func() { once.Do(func() { close(f.allow) }) }
	t.Cleanup(unblock)
	_, sess, err := s.createAdmissionSession(context.Background(), h, admissionTestRequest(), admissionAllow().MembershipRevision, "", "2025-03-26")
	if err != nil {
		t.Fatal("shutdown session setup failed")
	}
	return s, h, f, sess, unblock
}
func admissionAssertCleanShutdown(t *testing.T, h *HostedAdmission, f *admissionFakeLeases) {
	t.Helper()
	h.mu.Lock()
	count, sessions := h.counts[LeaseSession], len(h.sessions)
	h.mu.Unlock()
	f.mu.Lock()
	rows := len(f.rows)
	f.mu.Unlock()
	if count != 0 || sessions != 0 || rows != 0 {
		t.Fatal("shutdown returned before lease and local capacity cleanup")
	}
}
func admissionAssertClosePending(t *testing.T, done <-chan struct{}) {
	t.Helper()
	select {
	case <-done:
		t.Fatal("Close returned while shared lease Release was blocked")
	case <-time.After(50 * time.Millisecond):
	}
}

func TestAdmissionPublicCloseJoinsFullLeaseCleanup(t *testing.T) {
	s, h, f, sess, unblock := admissionShutdownFixture(t)
	first, second := make(chan struct{}), make(chan struct{})
	go func() { s.Close(); close(first) }()
	admissionAwait(t, f.entered)
	admissionAwait(t, sess.watch.exited)
	go func() { s.Close(); close(second) }()
	admissionAssertClosePending(t, first)
	admissionAssertClosePending(t, second)
	// Joining must not hold the admission mutex needed by the cleanup worker.
	unlocked := make(chan struct{})
	go func() { h.mu.Lock(); h.mu.Unlock(); close(unlocked) }()
	select {
	case <-unlocked:
	case <-time.After(100 * time.Millisecond):
		t.Fatal("shutdown joined while holding HostedAdmission.mu")
	}
	h.mu.Lock()
	count := h.counts[LeaseSession]
	h.mu.Unlock()
	if count != 1 {
		t.Fatal("capacity was released before lease cleanup completed")
	}
	if _, _, err := s.createAdmissionSession(context.Background(), h, admissionTestRequest(), admissionAllow().MembershipRevision, "", "2025-03-26"); err == nil {
		t.Fatal("closing server accepted another hosted worker")
	}
	unblock()
	admissionAwait(t, first)
	admissionAwait(t, second)
	admissionAssertCleanShutdown(t, h, f.admissionFakeLeases)
	s.Close()
	if err := s.Shutdown(context.Background()); err != nil {
		t.Fatal("repeated shutdown failed")
	}
}

func TestAdmissionPublicShutdownDeadlineKeepsCleanupJoinable(t *testing.T) {
	s, h, f, _, unblock := admissionShutdownFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- s.Shutdown(ctx) }()
	admissionAwait(t, f.entered)
	select {
	case err := <-done:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("Shutdown reported completion before full lease cleanup")
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("Shutdown ignored its caller deadline")
	}
	joined := make(chan struct{})
	go func() { s.Close(); close(joined) }()
	admissionAssertClosePending(t, joined)
	unblock()
	admissionAwait(t, joined)
	admissionAssertCleanShutdown(t, h, f.admissionFakeLeases)
}

type admissionAcquireBarrier struct {
	*admissionReleaseBarrier
	acquiring chan struct{}
}

func (f *admissionAcquireBarrier) Acquire(ctx context.Context, r LeaseRequest) (SessionLease, error) {
	lease, err := f.admissionFakeLeases.Acquire(ctx, r)
	close(f.acquiring)
	<-ctx.Done() // Model acquisition whose acknowledgement is lost on cancellation.
	return lease, err
}

func TestAdmissionCloseCancelsAndJoinsInFlightSessionCreation(t *testing.T) {
	s, h, base, a := admissionTestServer(t)
	releases := &admissionReleaseBarrier{admissionFakeLeases: base, entered: make(chan struct{}), allow: make(chan struct{})}
	f := &admissionAcquireBarrier{admissionReleaseBarrier: releases, acquiring: make(chan struct{})}
	var once sync.Once
	unblock := func() { once.Do(func() { close(f.allow) }) }
	t.Cleanup(unblock)
	s.SetDeps(Deps{HostedAdmission: h, SessionLeases: f, LiveAuthorizer: a})
	created := make(chan error, 1)
	go func() {
		_, _, err := s.createAdmissionSession(context.Background(), h, admissionTestRequest(), admissionAllow().MembershipRevision, "", "2025-03-26")
		created <- err
	}()
	admissionAwait(t, f.acquiring)
	closed := make(chan struct{})
	go func() { s.Close(); close(closed) }()
	select {
	case <-f.entered:
	case <-closed:
		t.Fatal("Close ignored an acquiring hosted session and its reservation")
	case <-time.After(500 * time.Millisecond):
		t.Fatal("Close did not cancel in-flight session acquisition")
	}
	admissionAssertClosePending(t, closed)
	unblock()
	select {
	case err := <-created:
		if err == nil {
			t.Fatal("session was published after shutdown began")
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled session creation did not finish")
	}
	admissionAwait(t, closed)
	admissionAssertCleanShutdown(t, h, base)
}
