package propertycatalog

// Skipped hot candidates are already committed to canonical spans. Keep a
// coalesced, workspace-local repair request until the lifecycle acknowledges a
// qualified authoritative scan. Completion compaction must not erase this work.
// Only the singleton sequencer writes requests; only the lifecycle writes acks.
import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"golang.org/x/sys/unix"
)

const candidateRepairFormat = "futureagi.property-catalog-candidate-repair"
const candidateRepairMaxBytes = 4096

// Alphabetical fields match the Python canonical JSON contract. An ack is an
// exact copy of the request it covers, not a mutable global 'repaired' flag.
type candidateRepairDocument struct {
	CandidateID    string `json:"candidate_id"`
	CandidateTopic string `json:"candidate_topic"`
	FirstSeenUS    int64  `json:"first_seen_us"`
	Format         string `json:"format"`
	Generation     uint64 `json:"generation"`
	LastSeenUS     int64  `json:"last_seen_us"`
	OrganizationID string `json:"organization_id"`
	Version        uint16 `json:"version"`
	WorkspaceID    string `json:"workspace_id"`
}

func candidateRepairPath(directory, organization, workspace string) string {
	return filepath.Join(directory, "repair-"+organization+"-"+workspace+".json")
}

func lockCandidateRepair(path string) (func(), error) {
	fd, err := unix.Open(path+".lock", unix.O_RDWR|unix.O_CREAT|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0o600)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), path+".lock")
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() {
		f.Close()
		return nil, errors.New("propertycatalog: candidate repair lock must be a regular file")
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		err = unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB)
		if err == nil {
			return func() { _ = unix.Flock(fd, unix.LOCK_UN); _ = f.Close() }, nil
		}
		if (!errors.Is(err, unix.EWOULDBLOCK) && !errors.Is(err, unix.EAGAIN)) || time.Now().After(deadline) {
			f.Close()
			return nil, fmt.Errorf("propertycatalog: acquire candidate repair lock: %w", err)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func readCandidateRepair(path string) ([]byte, error) {
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, &os.PathError{Op: "open", Path: path, Err: err}
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > candidateRepairMaxBytes {
		return nil, errors.New("propertycatalog: repair state must be a bounded regular file")
	}
	return io.ReadAll(io.LimitReader(f, candidateRepairMaxBytes+1))
}

func parseCandidateRepair(raw []byte, topic, organization, workspace string) (candidateRepairDocument, error) {
	var doc candidateRepairDocument
	if err := json.Unmarshal(raw, &doc); err != nil {
		return doc, err
	}
	canonical, err := json.Marshal(doc)
	if err != nil || !bytes.Equal(raw, append(canonical, '\n')) ||
		doc.Format != candidateRepairFormat || doc.Version != 1 || doc.Generation == 0 ||
		doc.CandidateTopic != topic || doc.OrganizationID != organization || doc.WorkspaceID != workspace ||
		doc.FirstSeenUS <= 0 || doc.LastSeenUS < doc.FirstSeenUS || !isLowerSHA256(doc.CandidateID) {
		return doc, errors.New("propertycatalog: invalid or cross-scope candidate repair state")
	}
	return doc, nil
}

func (s *CandidateReceiptStore) persistRepairRequest(receipt CandidateReceipt) error {
	snapshot := receipt.Candidate().Snapshot()
	first, err := parseCandidateTime("repair first_seen", snapshot.FirstSeen)
	if err != nil {
		return err
	}
	last, err := parseCandidateTime("repair last_seen", snapshot.LastSeen)
	if err != nil {
		return err
	}
	path := candidateRepairPath(s.cfg.Directory, snapshot.OrganizationID, snapshot.WorkspaceID)
	unlock, err := lockCandidateRepair(path)
	if err != nil {
		return err
	}
	defer unlock()
	doc := candidateRepairDocument{
		CandidateID: snapshot.CandidateID, CandidateTopic: s.cfg.Topic,
		FirstSeenUS: first.UnixMicro(), LastSeenUS: last.UnixMicro(),
		Format: candidateRepairFormat, Version: 1, Generation: 1,
		OrganizationID: snapshot.OrganizationID, WorkspaceID: snapshot.WorkspaceID,
	}
	raw, err := readCandidateRepair(path)
	if err == nil {
		previous, err := parseCandidateRepair(raw, s.cfg.Topic, snapshot.OrganizationID, snapshot.WorkspaceID)
		if err != nil {
			return err
		}
		// Replay after request fsync but before completion fsync is idempotent.
		if previous.CandidateID == snapshot.CandidateID {
			return s.syncDir(s.cfg.Directory)
		}
		if previous.Generation == ^uint64(0) {
			return errors.New("propertycatalog: repair generation exhausted")
		}
		doc.Generation = previous.Generation + 1
		ack, err := readCandidateRepair(path + ".ack")
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		if err == nil {
			ackDoc, err := parseCandidateRepair(ack, s.cfg.Topic, snapshot.OrganizationID, snapshot.WorkspaceID)
			if err != nil || ackDoc.Generation > previous.Generation ||
				(ackDoc.Generation == previous.Generation && ackDoc.CandidateID != previous.CandidateID) {
				return errors.New("propertycatalog: invalid candidate repair acknowledgement")
			}
		}
		claimed := false
		claim, err := readCandidateRepair(path + ".claim")
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		if err == nil {
			claimDoc, err := parseCandidateRepair(claim, s.cfg.Topic, snapshot.OrganizationID, snapshot.WorkspaceID)
			if err != nil || claimDoc.Generation > previous.Generation ||
				(claimDoc.Generation == previous.Generation && claimDoc.CandidateID != previous.CandidateID) {
				return errors.New("propertycatalog: invalid candidate repair claim")
			}
			claimed = claimDoc.Generation == previous.Generation && claimDoc.CandidateID == previous.CandidateID
		}
		// A claim durably retains the old interval while the lifecycle scans.
		// New arrivals start a separate pending interval even before that scan
		// finishes. Continuous traffic therefore cannot retain the old lower
		// bound forever and force repeated historical scans after its repair.
		if !claimed && !bytes.Equal(raw, ack) {
			doc.FirstSeenUS = min(doc.FirstSeenUS, previous.FirstSeenUS)
			doc.LastSeenUS = max(doc.LastSeenUS, previous.LastSeenUS)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	encoded, err := json.Marshal(doc)
	if err != nil {
		return err
	}
	if _, err := parseCandidateRepair(append(encoded, '\n'), s.cfg.Topic, snapshot.OrganizationID, snapshot.WorkspaceID); err != nil {
		return fmt.Errorf("propertycatalog: repair request invalid: %w", err)
	}
	return atomicReplace(path, ".candidate-repair-tmp-", append(encoded, '\n'), s.cfg.Directory, s.syncDir)
}
