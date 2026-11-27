//go:build windows

package logging

import (
	"errors"
	"syscall"
)

// wsaeconnrefused is the Winsock ECONNREFUSED errno (10061).
// Go's syscall.ECONNREFUSED on Windows uses a different constant value.
const wsaeconnrefused = syscall.Errno(10061)

func connectionRefused(err error) bool {
	return errors.Is(err, syscall.ECONNREFUSED) || errors.Is(err, wsaeconnrefused)
}
