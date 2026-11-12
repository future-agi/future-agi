package tenant

import (
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/routebinding"
)

var (
	ErrConfigGraph       = errors.New("tenant config graph unsupported")
	ErrConfigPreparation = errors.New("tenant config preparation failed")
	ErrNativeLifecycle   = errors.New("tenant config publication refused")
)

// NativeView is an opaque Store-issued root/variant association. Value copies
// intentionally retain the same immutable association. It is not an admission,
// policy decision, live FA observation or aggregate owner-readiness assertion.
// Consumers must enforce exact Option A administrative equivalence; neither
// inherited metadata nor a candidate view establishes that equivalence.
type NativeView struct {
	store     *Store
	lifecycle *tenantLifecycle
	org       string
	image     *nativeImage
	stamp     routebinding.Stamp
	present   bool
	variant   string
}

// Data returns detached historical DATA only, including after the view is stale.
// Native use always requires a fresh token-bounded check of the original view.
func (v NativeView) Data() *OrgConfig {
	if v.store == nil || v.lifecycle == nil || !v.stamp.Valid() {
		return nil
	}
	return legacyData(v.image, nil, v.variant)
}

func (s *Store) NativeSnapshot(read *routebinding.Read, org string) (NativeView, error) {
	life := s.lifecycle()
	if life == nil {
		return NativeView{}, ErrNativeLifecycle
	}
	var out NativeView
	err := read.Use(life.gate, func() error {
		s.mu.RLock()
		defer s.mu.RUnlock()
		if !s.nativeReadyLocked(life) {
			return ErrNativeLifecycle
		}
		out = NativeView{store: s, lifecycle: life, org: org, stamp: life.initial, variant: PolicyVariantStable}
		if entry := s.configs[org]; entry != nil {
			out.image = entry.image
			out.stamp = entry.stamp
			out.present = entry.present
		}
		return nil
	})
	if err != nil {
		return NativeView{}, ErrNativeLifecycle
	}
	return out, nil
}

func (s *Store) NativeVariant(read *routebinding.Read, org string, base NativeView, variant string) (NativeView, error) {
	life := s.lifecycle()
	if life == nil {
		return NativeView{}, ErrNativeLifecycle
	}
	var out NativeView
	err := read.Use(life.gate, func() error {
		s.mu.RLock()
		defer s.mu.RUnlock()
		if s.checkNativeLocked(life, org, base) != nil || base.variant != PolicyVariantStable {
			return ErrNativeLifecycle
		}
		if variant != PolicyVariantStable {
			if _, ok := base.image.variant(variant); !ok {
				return ErrNativeLifecycle
			}
		}
		out = base
		out.variant = variant
		return nil
	})
	if err != nil {
		return NativeView{}, ErrNativeLifecycle
	}
	return out, nil
}
func (s *Store) CheckNativeView(read *routebinding.Read, org string, view NativeView) error {
	life := s.lifecycle()
	if life == nil {
		return ErrNativeLifecycle
	}
	err := read.Use(life.gate, func() error { s.mu.RLock(); defer s.mu.RUnlock(); return s.checkNativeLocked(life, org, view) })
	if err != nil {
		return ErrNativeLifecycle
	}
	return nil
}
func (s *Store) CheckNativeViewForPublish(write *routebinding.Write, org string, view NativeView) error {
	life := s.lifecycle()
	if life == nil {
		return ErrNativeLifecycle
	}
	err := write.Use(life.gate, func() error { s.mu.RLock(); defer s.mu.RUnlock(); return s.checkNativeLocked(life, org, view) })
	if err != nil {
		return ErrNativeLifecycle
	}
	return nil
}
func (s *Store) checkNativeLocked(life *tenantLifecycle, org string, v NativeView) error {
	if !s.nativeReadyLocked(life) || v.store != s || v.lifecycle != life || v.org != org || !v.stamp.Valid() {
		return ErrNativeLifecycle
	}
	image, present, stamp := (*nativeImage)(nil), false, life.initial
	if entry := s.configs[org]; entry != nil {
		image, present, stamp = entry.image, entry.present, entry.stamp
	}
	if v.image != image || v.present != present || !v.stamp.Equal(stamp) {
		return ErrNativeLifecycle
	}
	if v.variant != PolicyVariantStable {
		if _, ok := image.variant(v.variant); !ok {
			return ErrNativeLifecycle
		}
	}
	return nil
}
