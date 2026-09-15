package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"golang.org/x/sys/unix"
)

// Fits a maximum canonical legacy value and key escaped again inside JSON.
// Save and resume must enforce the same bound, before replacing progress.
const maxCheckpointBytes = 256 << 10

// This is local progress, not a catalog version or activation record. Advancing
// it proves Kafka acknowledgement only, not consumer completion or replica lag.
type checkpoint struct {
	Binding  string       `json:"binding"`
	Hour     time.Time    `json:"hour"`
	After    physicalKey  `json:"after"`
	Pages    uint64       `json:"published_pages"`
	Rows     uint64       `json:"source_rows"`
	Complete bool         `json:"scan_complete"`
	Legacy   legacyCursor `json:"legacy_after,omitempty"`
}

func lockCheckpoint(path string) (*os.File, error) {
	if path == "" {
		return nil, errors.New("--checkpoint is required with --apply")
	}
	fd, err := unix.Open(path+".lock", unix.O_CREAT|unix.O_RDWR|unix.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, fmt.Errorf("open checkpoint lock: %w", err)
	}
	file := os.NewFile(uintptr(fd), path+".lock")
	if err := unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB); err != nil {
		file.Close()
		return nil, errors.New("another backfill owns this checkpoint")
	}
	return file, nil
}

func loadCheckpoint(path, binding string, start time.Time) (checkpoint, error) {
	initial := checkpoint{Binding: binding, Hour: start.Truncate(time.Hour)}
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return initial, nil
	}
	if err != nil {
		return checkpoint{}, err
	}
	if !info.Mode().IsRegular() || info.Size() > maxCheckpointBytes {
		return checkpoint{}, errors.New("checkpoint must be a small regular file")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return checkpoint{}, err
	}
	var result checkpoint
	if err := json.Unmarshal(data, &result); err != nil || result.Binding != binding || result.Hour.IsZero() {
		return checkpoint{}, errors.New("checkpoint is invalid or belongs to a different scan/destination")
	}
	return result, nil
}

func saveCheckpoint(path string, value checkpoint) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if len(data) > maxCheckpointBytes {
		return errors.New("checkpoint exceeds size limit")
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".observed-backfill-*")
	if err != nil {
		return err
	}
	temporary := file.Name()
	defer os.Remove(temporary)
	if _, err := file.Write(data); err != nil {
		file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporary, path); err != nil {
		return err
	}
	dir, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}
