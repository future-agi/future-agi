package tenant

import "github.com/futureagi/agentcc-gateway/internal/routebinding"

// tenantLifecycle is installed once; its immutable fields may be read without
// Store.mu. Readiness and all org entries remain protected by Store.mu + Gate.
type tenantLifecycle struct {
	owner   *Store
	gate    *routebinding.Gate
	initial routebinding.Stamp
}

func (s *Store) lifecycle() *tenantLifecycle {
	if s == nil {
		return nil
	}
	return s.native.Load()
}
func (s *Store) nativeReadyLocked(life *tenantLifecycle) bool {
	return s.self == s && life != nil && life.owner == s && s.native.Load() == life && s.nativeReady
}

// BindNativeLifecycle is bootstrap-only on a fresh NewStore, before the caller
// forwards it to any consumer or goroutine. Go aliases cannot be discovered;
// callers must not migrate an escaped store. It installs no aggregate authority
// and is intentionally not called by production startup or server.New.
func (s *Store) BindNativeLifecycle(g *routebinding.Gate) error {
	if s == nil || s.self != s {
		return ErrNativeLifecycle
	}
	err := g.Publish(func(w *routebinding.Write) error {
		stamp, err := w.Next()
		if err != nil {
			return ErrNativeLifecycle
		}
		return w.Use(g, func() error {
			s.mu.Lock()
			defer s.mu.Unlock()
			if s.native.Load() != nil || s.touched || len(s.configs) != 0 || len(s.observers) != 0 || s.onChange != nil {
				return ErrNativeLifecycle
			}
			s.nativeReady = true
			s.native.Store(&tenantLifecycle{owner: s, gate: g, initial: stamp})
			return nil
		})
	})
	if err != nil {
		return ErrNativeLifecycle
	}
	return nil
}

// Claim ordinary mode before preparing a legacy mutation. Once any ordinary
// mutation has begun, even an empty fetch/bulk, later binding is forbidden.
func (s *Store) mutationLifecycle() *tenantLifecycle {
	s.mu.Lock()
	defer s.mu.Unlock()
	life := s.native.Load()
	if life == nil {
		s.touched = true
	}
	return life
}

// publishNative plans under Use, reserves one nonreused batch stamp outside
// Use, then prepares every observer payload before committing anything. The
// exclusive Gate keeps canonical state unchanged across these two uses; final
// observer preparation includes registrations made between them.
func (s *Store) publishNative(life *tenantLifecycle, plan func() (*nativeMutation, error)) error {
	var mutation *nativeMutation
	var cb func(string)
	err := life.gate.Publish(func(w *routebinding.Write) error {
		err := w.Use(life.gate, func() error {
			s.mu.Lock()
			defer s.mu.Unlock()
			if !s.nativeReadyLocked(life) {
				return ErrNativeLifecycle
			}
			var err error
			mutation, err = plan()
			return err
		})
		if err != nil || mutation == nil {
			return err
		}
		var stamp routebinding.Stamp
		if mutation.needsStamp() {
			stamp, err = w.Next()
			if err != nil {
				_ = w.Use(life.gate, func() error { s.mu.Lock(); defer s.mu.Unlock(); s.nativeReady = false; return nil })
				return ErrNativeLifecycle
			}
		}
		return w.Use(life.gate, func() error {
			s.mu.Lock()
			defer s.mu.Unlock()
			if !s.nativeReadyLocked(life) {
				return ErrNativeLifecycle
			}
			notices, err := s.prepareNativeObserversLocked(mutation)
			if err != nil {
				return err
			}
			completed := false
			defer func() {
				if !completed {
					s.nativeReady = false
				}
			}()
			s.commitNativeLocked(mutation, stamp)
			cb = s.onChange
			for _, notice := range notices {
				notice.observer.invalidate(notice.org, notice.data)
			}
			completed = true
			return nil
		})
	})
	if err != nil {
		if err == ErrConfigGraph || err == ErrConfigPreparation {
			return err
		}
		return ErrNativeLifecycle
	}
	// Only historical changed-fetch notifications escape the fence/Store lock.
	if mutation != nil && cb != nil {
		completed := false
		defer func() {
			if !completed {
				// onChange also runs after publication, but outside the original fence.
				// Revoke under a new fence before propagating its panic; never roll back.
				_ = life.gate.Publish(func(w *routebinding.Write) error {
					return w.Use(life.gate, func() error { s.mu.Lock(); defer s.mu.Unlock(); s.nativeReady = false; return nil })
				})
			}
		}()
		for _, org := range mutation.changed {
			cb(org)
		}
		completed = true
	}
	return nil
}
