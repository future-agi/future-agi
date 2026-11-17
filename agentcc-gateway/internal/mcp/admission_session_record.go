package mcp

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"sync"
	"time"
)

// admissionRecord has a disjoint namespace and version: org-only legacy
// records and tokens can never be upgraded by a hosted resume.
type admissionRecord struct {
	Version                 int
	Scope                   ScopeKey
	Actor                   AdmissionActor
	Revision                MembershipRevision
	Authority               LiveAuthorizationRequest
	Target, Route, Protocol string
	LeaseRequest            LeaseRequest
	Lease                   SessionLease
}

func (r admissionRecord) matches(a LiveAuthorizationRequest, revision MembershipRevision) bool {
	return r.Version == 3 && r.Scope == a.Scope && r.Actor == a.Actor && r.Revision == revision && a.valid()
}

type admissionToken struct {
	Version int
	Scope   ScopeKey
	Nonce   string
}
type admissionSession struct {
	mu        sync.Mutex
	record    admissionRecord
	hash, key string
	lease     *admissionLease
	watch     *admissionWatch
}

func admissionRandomID() (string, error) {
	var b [24]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", errAdmissionUnavailable
	}
	return hex.EncodeToString(b[:]), nil
}
func admissionSessionKey(scope ScopeKey, hash string) string {
	// Hash framed scope components, not concatenated ambiguous identifiers.
	raw, _ := json.Marshal(scope)
	sum := sha256.Sum256(raw)
	return "mcps3:{" + hex.EncodeToString(sum[:]) + "}:" + hash
}
func (s *Server) createAdmissionSession(ctx context.Context, h *HostedAdmission, a LiveAuthorizationRequest, revision MembershipRevision, route, protocol string) (string, *admissionSession, error) {
	worker, err := s.trackAdmissionSession()
	if err != nil {
		return "", nil, err
	}
	keep := false
	defer func() {
		if !keep {
			worker.finish()
		}
	}()
	ctx, stop := worker.requestContext(ctx)
	defer stop()
	release, err := h.reserve(LeaseSession)
	if err != nil {
		return "", nil, err
	}
	defer func() {
		if !keep {
			release()
		}
	}()
	id, err := admissionRandomID()
	if err != nil {
		return "", nil, err
	}
	codec, err := s.sessions.codec()
	if err != nil {
		return "", nil, errAdmissionUnavailable
	}
	payload, _ := json.Marshal(admissionToken{3, a.Scope, id})
	token, err := codec.Seal(StatePurposeSession, payload, h.Limits.Absolute)
	if err != nil {
		return "", nil, errAdmissionUnavailable
	}
	hash := SessionHash(token)
	idle := min(h.Limits.Idle, s.sessions.ttl)
	r := LeaseRequest{ID: id, Scope: a.Scope, Actor: a.Actor, SessionHash: hash, Purpose: LeaseSession, Limits: h.leaseLimits(LeaseSession), TTL: min(3*time.Second, idle), Idle: idle, Absolute: h.Limits.Absolute}
	lease, err := acquireAdmissionLease(ctx, s.Deps().SessionLeases, r, time.Now())
	if err != nil {
		return "", nil, err
	}
	defer func() {
		if !keep {
			_ = lease.close()
		}
	}()
	rec := admissionRecord{Version: 3, Scope: a.Scope, Actor: a.Actor, Revision: revision, Authority: a, Target: a.Target, Route: route, Protocol: protocol, LeaseRequest: r, Lease: lease.value}
	sess := &admissionSession{record: rec, hash: hash, key: admissionSessionKey(a.Scope, hash), lease: lease}
	data, _ := json.Marshal(rec)
	storeCtx, cancel := context.WithTimeout(ctx, admissionLookupTimeout)
	defer cancel()
	if err := s.sessionStore().Save(storeCtx, sess.key, data, r.TTL); err != nil || storeCtx.Err() != nil {
		return "", nil, errAdmissionUnavailable
	}
	if ctx.Err() != nil {
		return "", nil, errAdmissionUnavailable
	}
	h.mu.Lock()
	err = worker.publish(func() {
		if h.sessions == nil {
			h.sessions = map[string]*admissionSession{}
		}
		h.sessions[hash] = sess
		s.startAdmissionSessionLocked(h, sess, release, worker)
	})
	h.mu.Unlock()
	if err != nil {
		return "", nil, err
	}
	keep = true
	return token, sess, nil
}

// startAdmissionSessionLocked owns one bounded worker until idle/absolute
// expiry, revocation, dependency loss or shutdown; passive renewal is not use.
func (s *Server) startAdmissionSessionLocked(h *HostedAdmission, sess *admissionSession, release func(), worker *admissionSessionWorker) {
	rec := sess.record
	a := rec.Authority
	a.Phase = PhaseContinuation
	sess.watch = newAdmissionWatch(worker.ctx, a, rec.Revision, func() LiveAuthorizer { return s.admissionAuthorizer(h) }, admissionRealTicker{time.NewTicker(admissionTick)}, func(ctx context.Context) error {
		return s.refreshAdmissionSession(ctx, sess, false)
	}, s.sessions.stopCh)
	go func() {
		defer worker.finish()
		<-sess.watch.exited
		h.mu.Lock()
		if h.sessions[sess.hash] == sess {
			delete(h.sessions, sess.hash)
		}
		h.mu.Unlock()
		s.sessions.hub.endSession(sess.hash)
		_ = sess.lease.close()
		release()
	}()
}
func (s *Server) refreshAdmissionSession(ctx context.Context, sess *admissionSession, activity bool) error {
	sess.mu.Lock()
	defer sess.mu.Unlock()
	if err := sess.lease.renew(ctx, activity, time.Now()); err != nil {
		return err
	}
	sess.lease.mu.Lock()
	sess.record.Lease = sess.lease.value
	ttl := time.Until(sess.lease.localExpiry)
	sess.lease.mu.Unlock()
	if ttl <= 0 {
		return errAdmissionDenied
	}
	data, _ := json.Marshal(sess.record)
	storeCtx, cancel := context.WithTimeout(ctx, admissionLookupTimeout)
	defer cancel()
	store, ok := s.sessionStore().(SessionRefresher)
	if !ok {
		return errAdmissionUnavailable
	}
	alive, err := store.Refresh(storeCtx, sess.key, sess.key+":events", data, ttl)
	if err != nil || storeCtx.Err() != nil {
		return errAdmissionUnavailable
	}
	if !alive {
		return errAdmissionDenied
	}
	return nil
}
func (s *Server) resumeAdmissionSession(ctx context.Context, h *HostedAdmission, r *http.Request, a LiveAuthorizationRequest, revision MembershipRevision) (*admissionSession, error) {
	worker, err := s.trackAdmissionSession()
	if err != nil {
		return nil, err
	}
	keep := false
	defer func() {
		if !keep {
			worker.finish()
		}
	}()
	ctx, stop := worker.requestContext(ctx)
	defer stop()
	rec, hash, err := s.loadAdmissionRecord(ctx, r, a)
	if err != nil {
		return nil, err
	}
	if !rec.matches(a, revision) {
		return nil, errAdmissionDenied
	}
	if !rec.Revision.present {
		return nil, errAdmissionRevisionUnavailable
	}
	key := admissionSessionKey(a.Scope, hash)
	h.mu.Lock()
	sess := h.sessions[hash]
	h.mu.Unlock()
	if sess == nil {
		// A different replica may resume only the same live shared lease;
		// Renew, never Acquire, prevents revival after expiry or process loss.
		release, err := h.reserve(LeaseSession)
		if err != nil {
			return nil, err
		}
		lease := &admissionLease{store: s.Deps().SessionLeases, request: rec.LeaseRequest, value: rec.Lease, localExpiry: time.Now().Add(admissionLookupTimeout)}
		sess = &admissionSession{record: rec, key: key, hash: hash, lease: lease}
		if err := s.refreshAdmissionSession(ctx, sess, true); err != nil {
			release()
			return nil, err
		}
		h.mu.Lock()
		if h.sessions == nil {
			h.sessions = map[string]*admissionSession{}
		}
		if existing := h.sessions[hash]; existing != nil {
			sess = existing
			h.mu.Unlock()
			release()
		} else {
			err = worker.publish(func() {
				h.sessions[hash] = sess
				s.startAdmissionSessionLocked(h, sess, release, worker)
			})
			h.mu.Unlock()
			if err != nil {
				// This is a borrowed shared lease, not a new acquisition: refusing
				// publication must not release another replica's live session.
				release()
				return nil, err
			}
			keep = true
		}
	}
	if sess.watch.ctx.Err() != nil {
		return nil, errAdmissionDenied
	}
	if err := s.refreshAdmissionSession(ctx, sess, true); err != nil {
		sess.watch.cancel()
		return nil, err
	}
	return sess, nil
}
func (s *Server) endAdmissionSession(ctx context.Context, sess *admissionSession) error {
	sess.watch.cancel()
	// Fence delivery locally before any fallible shared-store operation.
	s.sessions.hub.endSession(sess.hash)
	err := sess.lease.close()
	ctx, cancel := context.WithTimeout(ctx, admissionLookupTimeout)
	defer cancel()
	if e := s.sessionStore().Delete(ctx, sess.key); e != nil || ctx.Err() != nil {
		return errAdmissionUnavailable
	}
	return err
}
