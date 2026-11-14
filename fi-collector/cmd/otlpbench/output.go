package main

import (
	"fmt"
	"os"
	"path/filepath"
)

// outputSet stages every member privately in its destination directory. Final
// names are linked exclusively only after every write, sync and close succeeds.
// A handled failure rolls back only links still pointing to our own inode.
// This is not a crash-atomic multi-file transaction (see METHODOLOGY.rst).
type outputSet struct {
	files     []*os.File
	paths     []string
	linked    []bool
	committed bool
	reserved  bool
	identity  []os.FileInfo
}

func stageOutputs(paths ...string) (*outputSet, error) {
	s := &outputSet{paths: paths, linked: make([]bool, len(paths))}
	// Advisory early refusal avoids doing work for known occupied paths. Link at
	// commit, not this check, is the no-clobber authority against concurrent writers.
	for _, p := range paths {
		if _, err := os.Lstat(p); err == nil {
			return nil, fmt.Errorf("create output (existing files are never overwritten): %s already exists", p)
		} else if !os.IsNotExist(err) {
			return nil, err
		}
	}
	for _, p := range paths {
		f, err := os.CreateTemp(filepath.Dir(p), ".otlpbench-output-*")
		if err != nil {
			s.cleanup()
			return nil, err
		}
		s.files = append(s.files, f)
	}
	return s, nil
}

// Executed load commands reserve final names before sending any traffic. This
// keeps concurrent writers from executing the same output-owned run twice and
// leaves the telemetry stream tail-able during execution. All reservations
// succeed before any content is written; failures release only our own files.
func reserveOutputs(paths ...string) (*outputSet, error) {
	s := &outputSet{paths: paths, linked: make([]bool, len(paths)), reserved: true}
	for _, p := range paths {
		f, err := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			s.cleanup()
			return nil, fmt.Errorf("create output (existing files are never overwritten): %w", err)
		}
		info, err := f.Stat()
		if err != nil {
			f.Close()
			s.cleanup()
			return nil, err
		}
		s.files = append(s.files, f)
		s.identity = append(s.identity, info)
	}
	return s, nil
}

func (s *outputSet) cleanup() {
	for i, f := range s.files {
		f.Close()
		if s.reserved {
			if !s.committed {
				current, e := os.Lstat(s.paths[i])
				if e == nil && os.SameFile(s.identity[i], current) {
					_ = os.Remove(s.paths[i])
				}
			}
			continue
		}
		if !s.committed && s.linked[i] {
			own, err := os.Stat(f.Name())
			current, e := os.Lstat(s.paths[i])
			if err == nil && e == nil && os.SameFile(own, current) {
				_ = os.Remove(s.paths[i])
			}
		}
		_ = os.Remove(f.Name())
	}
}

func (s *outputSet) commit() error {
	for _, f := range s.files {
		if err := f.Sync(); err != nil {
			return err
		}
		if err := f.Close(); err != nil {
			return err
		}
	}
	if s.reserved {
		s.committed = true
		return nil
	}
	for i, f := range s.files {
		// Hard-link creation is atomic and fails for existing files, including
		// dangling symlinks. Rename would overwrite a concurrent writer's result.
		if err := os.Link(f.Name(), s.paths[i]); err != nil {
			return fmt.Errorf("create output (existing files are never overwritten): %w", err)
		}
		s.linked[i] = true
	}
	s.committed = true
	return nil
}
