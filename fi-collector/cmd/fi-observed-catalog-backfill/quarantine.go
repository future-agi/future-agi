package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"

	"golang.org/x/sys/unix"
)

type quarantinedSpan struct {
	Project string      `json:"project_id"`
	Seen    string      `json:"start_time"`
	Key     physicalKey `json:"identity"`
	Reason  string      `json:"reason"`
}

type pageReport struct {
	PolicyExclusions uint64
	Quarantined      []quarantinedSpan
}

// Write ahead of publishing/checkpointing. Retries may repeat an identity, but
// can never advance past an excluded row without a durable operator receipt.
// Only source identities are stored, never attributes or foreign organization IDs.
func recordQuarantine(path string, rows []quarantinedSpan) error {
	if len(rows) == 0 {
		return nil
	}
	fd, err := unix.Open(path, unix.O_WRONLY|unix.O_APPEND|unix.O_CREAT|unix.O_NOFOLLOW|unix.O_CLOEXEC|unix.O_NONBLOCK, 0600)
	if err != nil {
		return err
	}
	file := os.NewFile(uintptr(fd), path)
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return errors.New("quarantine receipt must be a private regular file")
	}
	encoder := json.NewEncoder(file)
	for _, row := range rows {
		if err := encoder.Encode(row); err != nil {
			return err
		}
	}
	if err := file.Sync(); err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}
