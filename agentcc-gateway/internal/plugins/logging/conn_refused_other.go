//go:build !windows

package logging

import (
	"errors"
	"syscall"
)

func connectionRefused(err error) bool {
	return errors.Is(err, syscall.ECONNREFUSED)
}
