package tenant

import (
	"math"

	"github.com/futureagi/agentcc-gateway/internal/routebinding"
)

type nativeReplacement struct {
	entry *storedConfig
	fresh bool
}
type nativeMutation struct {
	kind      string
	edits     map[string]nativeReplacement
	hashes    map[string][32]byte
	notify    []string
	changed   []string // legacy changed-fetch callbacks ONLY
	fetchedAt uint64
}

func (m *nativeMutation) needsStamp() bool {
	for _, e := range m.edits {
		if e.fresh {
			return true
		}
	}
	return false
}
func newNativeMutation(kind string) *nativeMutation {
	return &nativeMutation{kind: kind, edits: make(map[string]nativeReplacement), hashes: make(map[string][32]byte)}
}
func replacement(image *nativeImage) *storedConfig {
	return &storedConfig{present: true, image: image, legacy: &legacyIdentity{image: image}}
}
func (s *Store) preflightPushLocked() error {
	if s.gen == math.MaxUint64 {
		s.nativeReady = false
		return ErrNativeLifecycle
	}
	return nil
}
func (s *Store) setNative(life *tenantLifecycle, org string, cfg *OrgConfig) error {
	drafts, err := prepareNativeBatch(map[string]*OrgConfig{org: cfg}, true)
	if err != nil {
		return err
	}
	return s.publishNative(life, func() (*nativeMutation, error) {
		if err := s.preflightPushLocked(); err != nil {
			return nil, err
		}
		m := newNativeMutation("set")
		d := drafts[org]
		m.edits[org] = nativeReplacement{entry: replacement(d.image), fresh: true}
		m.hashes[org] = d.hash
		m.notify = []string{org}
		return m, nil
	})
}
func (s *Store) deleteNative(life *tenantLifecycle, org string) error {
	return s.publishNative(life, func() (*nativeMutation, error) {
		if err := s.preflightPushLocked(); err != nil {
			return nil, err
		}
		m := newNativeMutation("delete")
		m.edits[org] = nativeReplacement{entry: &storedConfig{}, fresh: true}
		m.notify = []string{org}
		return m, nil
	})
}
func (s *Store) loadNative(life *tenantLifecycle, configs map[string]*OrgConfig) error {
	// LoadBulk historically does not compute, clear or rebuild configHash.
	drafts, err := prepareNativeBatch(configs, false)
	if err != nil {
		return err
	}
	return s.publishNative(life, func() (*nativeMutation, error) {
		if err := s.preflightPushLocked(); err != nil {
			return nil, err
		}
		m := newNativeMutation("bulk")
		for org, old := range s.configs {
			if old.present {
				m.edits[org] = nativeReplacement{entry: &storedConfig{}, fresh: true}
				m.notify = append(m.notify, org)
			}
		}
		for org, d := range drafts {
			if _, seen := m.edits[org]; !seen {
				m.notify = append(m.notify, org)
			}
			m.edits[org] = nativeReplacement{entry: replacement(d.image), fresh: true}
		}
		return m, nil
	})
}

// The caller has prepared all fallible work and holds Write.Use and Store.mu.
func (s *Store) commitNativeLocked(m *nativeMutation, stamp routebinding.Stamp) {
	for org, edit := range m.edits {
		if edit.fresh {
			edit.entry.stamp = stamp
		}
		s.configs[org] = edit.entry
		if m.kind != "bulk" {
			if hash, ok := m.hashes[org]; ok {
				s.configHash[org] = hash
			} else if !edit.entry.present {
				delete(s.configHash, org)
			}
		}
	}
	for _, org := range m.notify {
		delete(s.redactors, org)
	}
	switch m.kind {
	case "set", "delete":
		s.gen++
		for org := range m.edits {
			s.pushedGen[org] = s.gen
		}
	case "bulk":
		clear(s.redactors)
		s.gen++
		s.loadedGen = s.gen
	case "fetch":
		for org, g := range s.pushedGen {
			if g <= m.fetchedAt {
				delete(s.pushedGen, org)
			}
		}
	}
}
