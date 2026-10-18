package lifecycle

import "errors"

// ErrCrash simulates process death: the worker leaves its lease to expire.
var ErrCrash = errors.New("video injected crash")

type Hooks interface{ Hit(string) error }
type HookFunc func(string) error

func (f HookFunc) Hit(p string) error { return f(p) }
func (s *Service) hit(p string) error {
	if s.hooks != nil {
		return s.hooks.Hit(p)
	}
	return nil
}
