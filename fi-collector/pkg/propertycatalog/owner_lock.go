package propertycatalog

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"golang.org/x/sys/unix"
)

const sequencerOwnerLockFile = "sequencer-owner-v1.lock"

// SequencerOwnerLock prevents two processes that can see the same durable
// state directory from allocating stream sequences concurrently. The fixed
// Kafka transactional ID supplies the cross-host stale-producer fence; this
// lock supplies the local/PVC fence before any receipt or sequence state is
// touched.
type SequencerOwnerLock struct {
	mu   sync.Mutex
	file *os.File
}

func AcquireSequencerOwnerLock(directory, ownerID string) (*SequencerOwnerLock, error) {
	if directory == "" || !filepath.IsAbs(directory) || !safeKafkaIdentity(ownerID) {
		return nil, errors.New("propertycatalog: owner lock requires an absolute directory and fixed safe identity")
	}
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return nil, fmt.Errorf("propertycatalog: prepare owner directory: %w", err)
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		return nil, fmt.Errorf("propertycatalog: secure owner directory: %w", err)
	}
	path := filepath.Join(directory, sequencerOwnerLockFile)
	fd, err := unix.Open(path, unix.O_CREAT|unix.O_RDWR|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0o600)
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: open owner lock: %w", err)
	}
	file := os.NewFile(uintptr(fd), path)
	if file == nil {
		_ = unix.Close(fd)
		return nil, errors.New("propertycatalog: construct owner lock file")
	}
	keep := false
	defer func() {
		if !keep {
			_ = file.Close()
		}
	}()
	if err := unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB); err != nil {
		return nil, fmt.Errorf("propertycatalog: singleton owner lock is already held: %w", err)
	}
	document, err := json.Marshal(struct {
		Format  string `json:"format"`
		Version uint16 `json:"version"`
		OwnerID string `json:"owner_id"`
	}{
		Format: "futureagi.property-catalog-sequencer-owner", Version: 1, OwnerID: ownerID,
	})
	if err != nil {
		return nil, err
	}
	document = append(document, '\n')
	if err := file.Truncate(0); err != nil {
		return nil, err
	}
	if _, err := file.Seek(0, 0); err != nil {
		return nil, err
	}
	if _, err := file.Write(document); err != nil {
		return nil, err
	}
	if err := file.Sync(); err != nil {
		return nil, err
	}
	if err := syncDirectory(directory); err != nil {
		return nil, err
	}
	keep = true
	return &SequencerOwnerLock{file: file}, nil
}

func (l *SequencerOwnerLock) Close() error {
	if l == nil {
		return nil
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.file == nil {
		return nil
	}
	file := l.file
	l.file = nil
	unlockErr := unix.Flock(int(file.Fd()), unix.LOCK_UN)
	return errors.Join(unlockErr, file.Close())
}
