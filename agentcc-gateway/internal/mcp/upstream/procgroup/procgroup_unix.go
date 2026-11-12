//go:build unix

// Package procgroup starts a stdio upstream's child in a process group of its
// own and signals that whole group. It is split by
// build tag so the gateway cross-builds for platforms without process groups.
package procgroup

import (
	"errors"
	"syscall"
)

// Supported reports whether this platform has process groups.
const Supported = true

// Attr makes the child the leader of a new process group.
func Attr() *syscall.SysProcAttr { return &syscall.SysProcAttr{Setpgid: true} }

// Signal sends sig to the group led by pid. A group that has already gone is
// not an error.
func Signal(pid int, sig syscall.Signal) error {
	if err := syscall.Kill(-pid, sig); err != nil && !errors.Is(err, syscall.ESRCH) {
		return err
	}
	return nil
}
