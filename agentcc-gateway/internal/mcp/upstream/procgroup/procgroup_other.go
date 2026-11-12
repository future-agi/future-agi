//go:build !unix

package procgroup

import (
	"errors"
	"os"
	"syscall"
)

// Supported reports whether this platform has process groups.
const Supported = false

// Attr is the platform default: the child gets no group of its own.
func Attr() *syscall.SysProcAttr { return &syscall.SysProcAttr{} }

// Signal stops the process pid itself, the closest this platform comes to
// signalling a group: os.Process.Kill for SIGKILL, and Signal for anything
// else, which some platforms refuse. A process that has already gone is not an
// error.
func Signal(pid int, sig syscall.Signal) error {
	p, err := os.FindProcess(pid)
	if err != nil {
		return nil
	}
	if sig == syscall.SIGKILL {
		err = p.Kill()
	} else {
		err = p.Signal(sig)
	}
	if err != nil && !errors.Is(err, os.ErrProcessDone) {
		return err
	}
	return nil
}
