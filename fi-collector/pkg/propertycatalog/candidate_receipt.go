package propertycatalog

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
)

const (
	candidateReceiptFormat        = "futureagi.property-catalog-candidate-receipt"
	candidateReceiptVersion       = uint16(1)
	candidateReceiptPrefix        = "candidate-receipt-"
	candidateReceiptSuffix        = ".json"
	candidateReceiptTempPrefix    = ".candidate-receipt-tmp-"
	candidateCompletionFormat     = "futureagi.property-catalog-candidate-completions"
	candidateCompletionVersion    = uint16(1)
	candidateCompletionFile       = "candidate-completions-v1.json"
	candidateCompletionTempPrefix = ".candidate-completions-tmp-"

	DefaultCandidateReceiptFiles = 10_000
	DefaultCandidateReceiptBytes = int64(512 << 20)
	DefaultCandidateRecentIDs    = 10_000

	maxCandidateReceiptFiles = 1_000_000
	maxCandidateReceiptBytes = int64(1 << 40)
	maxCandidateRecentIDs    = 1_000_000
	maxCandidatePartitions   = 256
)

var ErrCandidateHistoryCompacted = errors.New("propertycatalog: candidate receipt history was compacted")

type CandidateReceiptStoreConfig struct {
	Directory       string
	Topic           string
	MaxPendingFiles int
	MaxPendingBytes int64
	MaxRecentIDs    int
}

type candidateReceiptDisk struct {
	Format      string `json:"format"`
	Version     uint16 `json:"version"`
	Topic       string `json:"topic"`
	Partition   int32  `json:"partition"`
	Offset      int64  `json:"offset"`
	LeaderEpoch int32  `json:"leader_epoch"`
	CandidateID string `json:"candidate_id"`
	Key         []byte `json:"key"`
	Value       []byte `json:"value"`
	KeySHA256   string `json:"key_sha256"`
	ValueSHA256 string `json:"value_sha256"`
}

type candidateCompletion struct {
	Partition   int32                          `json:"partition"`
	Offset      int64                          `json:"offset"`
	CandidateID string                         `json:"candidate_id"`
	KeySHA256   string                         `json:"key_sha256"`
	ValueSHA256 string                         `json:"value_sha256"`
	Disposition candidateCompletionDisposition `json:"disposition"`
	SkipReason  CandidateNotAdmittedReason     `json:"skip_reason"`
}

type candidateCompletionDisposition string

const (
	candidateCompletionSequenced   candidateCompletionDisposition = "sequenced"
	candidateCompletionNotAdmitted candidateCompletionDisposition = "not_admitted"
)

type candidateCompaction struct {
	Partition        int32 `json:"partition"`
	CompactedThrough int64 `json:"compacted_through"`
}

type candidateCompletionDocument struct {
	Format       string                `json:"format"`
	Version      uint16                `json:"version"`
	SkippedTotal uint64                `json:"skipped_total"`
	Completions  []candidateCompletion `json:"completions"`
	Compactions  []candidateCompaction `json:"compactions"`
}

type candidateCoordinate struct {
	partition int32
	offset    int64
}

// CandidateReceipt is immutable work already accepted by local fsync. Its
// Kafka offset may be committed without risking candidate loss.
type CandidateReceipt struct {
	disk      candidateReceiptDisk
	candidate WireCandidate
	path      string
	size      int64
}

func (r CandidateReceipt) Candidate() WireCandidate { return r.candidate }

func (r CandidateReceipt) Coordinate() (int32, int64) {
	return r.disk.Partition, r.disk.Offset
}

type CandidateReceiptStore struct {
	mu           sync.Mutex
	cfg          CandidateReceiptStoreConfig
	pendingFiles int
	pendingBytes int64
	completions  map[candidateCoordinate]candidateCompletion
	byCandidate  map[string]candidateCompletion
	compacted    map[int32]int64
	skippedTotal uint64
	syncDir      func(string) error
}

func NewCandidateReceiptStore(cfg CandidateReceiptStoreConfig) (*CandidateReceiptStore, error) {
	if cfg.MaxPendingFiles == 0 {
		cfg.MaxPendingFiles = DefaultCandidateReceiptFiles
	}
	if cfg.MaxPendingBytes == 0 {
		cfg.MaxPendingBytes = DefaultCandidateReceiptBytes
	}
	if cfg.MaxRecentIDs == 0 {
		cfg.MaxRecentIDs = DefaultCandidateRecentIDs
	}
	if cfg.Directory == "" || !filepath.IsAbs(cfg.Directory) {
		return nil, errors.New("propertycatalog: candidate receipt directory must be absolute")
	}
	if err := validateTopic(cfg.Topic); err != nil {
		return nil, err
	}
	if cfg.MaxPendingFiles < 1 || cfg.MaxPendingFiles > maxCandidateReceiptFiles ||
		cfg.MaxPendingBytes < 1 || cfg.MaxPendingBytes > maxCandidateReceiptBytes ||
		cfg.MaxRecentIDs < 1 || cfg.MaxRecentIDs > maxCandidateRecentIDs {
		return nil, errors.New("propertycatalog: candidate receipt bounds are invalid")
	}
	if err := os.MkdirAll(cfg.Directory, 0o700); err != nil {
		return nil, fmt.Errorf("propertycatalog: prepare candidate receipts: %w", err)
	}
	if err := os.Chmod(cfg.Directory, 0o700); err != nil {
		return nil, fmt.Errorf("propertycatalog: secure candidate receipts: %w", err)
	}
	store := &CandidateReceiptStore{
		cfg: cfg, completions: make(map[candidateCoordinate]candidateCompletion),
		byCandidate: make(map[string]candidateCompletion), compacted: make(map[int32]int64),
		syncDir: syncDirectory,
	}
	if err := store.cleanupTemps(); err != nil {
		return nil, err
	}
	if err := store.loadCompletions(); err != nil {
		return nil, err
	}
	files, err := store.enumeratePending()
	if err != nil {
		return nil, err
	}
	for _, file := range files {
		store.pendingFiles++
		store.pendingBytes += file.size
	}
	if store.pendingFiles > cfg.MaxPendingFiles || store.pendingBytes > cfg.MaxPendingBytes {
		return nil, errors.New("propertycatalog: existing candidate receipts exceed configured caps")
	}
	return store, nil
}

// Receive fsyncs the exact Kafka record before returning. completed is true
// only when recent durable evidence already proves this candidate was handed
// to the ordered spool; the caller may then commit and skip sequencing.
func (s *CandidateReceiptStore) Receive(record catalogkafka.Record) (receipt CandidateReceipt, completed bool, err error) {
	if s == nil {
		return CandidateReceipt{}, false, errors.New("propertycatalog: nil candidate receipt store")
	}
	if record.Topic != s.cfg.Topic || record.Partition < 0 || record.Offset < 0 {
		return CandidateReceipt{}, false, fmt.Errorf("%w: invalid candidate topic/partition/offset", ErrPoisonRecord)
	}
	candidate, err := ParseWireCandidate(record.Value)
	if err != nil {
		return CandidateReceipt{}, false, fmt.Errorf("%w: %v", ErrPoisonRecord, err)
	}
	wantKey, err := CandidateKafkaKey(candidate)
	if err != nil || !bytes.Equal(wantKey, record.Key) {
		return CandidateReceipt{}, false, fmt.Errorf("%w: candidate record key mismatch", ErrPoisonRecord)
	}
	keyDigest := sha256.Sum256(record.Key)
	valueDigest := sha256.Sum256(record.Value)
	disk := candidateReceiptDisk{
		Format: candidateReceiptFormat, Version: candidateReceiptVersion,
		Topic: record.Topic, Partition: record.Partition, Offset: record.Offset,
		LeaderEpoch: record.LeaderEpoch, CandidateID: candidate.Snapshot().CandidateID,
		Key: bytes.Clone(record.Key), Value: bytes.Clone(record.Value),
		KeySHA256: hex.EncodeToString(keyDigest[:]), ValueSHA256: hex.EncodeToString(valueDigest[:]),
	}
	if err := validateCandidateReceiptDisk(disk, s.cfg.Topic); err != nil {
		return CandidateReceipt{}, false, err
	}
	coordinate := candidateCoordinate{record.Partition, record.Offset}

	s.mu.Lock()
	defer s.mu.Unlock()
	if floor, exists := s.compacted[record.Partition]; exists && record.Offset <= floor {
		return CandidateReceipt{}, false, fmt.Errorf(
			"%w: partition %d offset %d is at or below %d",
			ErrCandidateHistoryCompacted, record.Partition, record.Offset, floor,
		)
	}
	if known, exists := s.completions[coordinate]; exists {
		if !sameCandidateCompletionIdentity(known, completionFromDisk(disk)) {
			return CandidateReceipt{}, false, fmt.Errorf("%w: completed candidate coordinate conflicts", ErrPoisonRecord)
		}
		return CandidateReceipt{disk: disk, candidate: candidate}, true, nil
	}
	if known, exists := s.byCandidate[disk.CandidateID]; exists {
		if known.KeySHA256 != disk.KeySHA256 || known.ValueSHA256 != disk.ValueSHA256 {
			return CandidateReceipt{}, false, fmt.Errorf("%w: candidate identity conflicts with recent bytes", ErrPoisonRecord)
		}
		receipt := CandidateReceipt{disk: disk, candidate: candidate}
		if err := s.completeLocked(receipt, known.Disposition, known.SkipReason); err != nil {
			return CandidateReceipt{}, false, err
		}
		return receipt, true, nil
	}
	path := filepath.Join(s.cfg.Directory, candidateReceiptName(record.Partition, record.Offset))
	if info, statErr := os.Lstat(path); statErr == nil {
		loaded, loadErr := s.loadPending(path, info.Size())
		if loadErr != nil {
			return CandidateReceipt{}, false, loadErr
		}
		if !sameCandidateReceiptDisk(loaded.disk, disk) {
			return CandidateReceipt{}, false, fmt.Errorf("%w: pending candidate coordinate conflicts", ErrPoisonRecord)
		}
		return loaded, false, nil
	} else if !errors.Is(statErr, os.ErrNotExist) {
		return CandidateReceipt{}, false, statErr
	}
	encoded, err := json.Marshal(disk)
	if err != nil {
		return CandidateReceipt{}, false, err
	}
	encoded = append(encoded, '\n')
	if s.pendingFiles >= s.cfg.MaxPendingFiles || int64(len(encoded)) > s.cfg.MaxPendingBytes-s.pendingBytes {
		return CandidateReceipt{}, false, errors.New("propertycatalog: candidate receipt capacity reached")
	}
	if err := atomicWriteNew(path, candidateReceiptTempPrefix, encoded, s.cfg.Directory, s.syncDir); err != nil {
		return CandidateReceipt{}, false, err
	}
	s.pendingFiles++
	s.pendingBytes += int64(len(encoded))
	return CandidateReceipt{disk: disk, candidate: candidate, path: path, size: int64(len(encoded))}, false, nil
}

func (s *CandidateReceiptStore) Pending() ([]CandidateReceipt, error) {
	if s == nil {
		return nil, errors.New("propertycatalog: nil candidate receipt store")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	files, err := s.enumeratePending()
	if err != nil {
		return nil, err
	}
	result := make([]CandidateReceipt, 0, len(files))
	for _, file := range files {
		receipt, err := s.loadPending(file.path, file.size)
		if err != nil {
			return nil, err
		}
		result = append(result, receipt)
	}
	return result, nil
}

func (s *CandidateReceiptStore) Completed(receipt CandidateReceipt) (bool, error) {
	if s == nil {
		return false, errors.New("propertycatalog: nil candidate receipt store")
	}
	if err := validateCandidateReceiptDisk(receipt.disk, s.cfg.Topic); err != nil {
		return false, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	coordinate := candidateCoordinate{receipt.disk.Partition, receipt.disk.Offset}
	if known, exists := s.completions[coordinate]; exists {
		if !sameCandidateCompletionIdentity(known, completionFromDisk(receipt.disk)) {
			return false, fmt.Errorf("%w: completed candidate coordinate conflicts", ErrPoisonRecord)
		}
		return true, nil
	}
	known, exists := s.byCandidate[receipt.disk.CandidateID]
	if !exists {
		return false, nil
	}
	if known.KeySHA256 != receipt.disk.KeySHA256 || known.ValueSHA256 != receipt.disk.ValueSHA256 {
		return false, fmt.Errorf("%w: candidate identity conflicts with completion state", ErrPoisonRecord)
	}
	return true, nil
}

// Complete persists bounded recent dedupe evidence before removing the
// pending receipt. A crash at either side of that boundary is recoverable.
func (s *CandidateReceiptStore) Complete(receipt CandidateReceipt) error {
	if s == nil {
		return errors.New("propertycatalog: nil candidate receipt store")
	}
	if err := validateCandidateReceiptDisk(receipt.disk, s.cfg.Topic); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.completeLocked(receipt, candidateCompletionSequenced, "")
}

// CompleteNotAdmitted durably records the exact rollout-boundary reason before
// removing the pending receipt. The cumulative counter survives completion
// compaction, making skipped canonical work observable across restarts.
func (s *CandidateReceiptStore) CompleteNotAdmitted(
	receipt CandidateReceipt, reason CandidateNotAdmittedReason,
) error {
	if s == nil {
		return errors.New("propertycatalog: nil candidate receipt store")
	}
	if !validCandidateNotAdmittedReason(reason) {
		return errors.New("propertycatalog: invalid candidate non-admission reason")
	}
	if err := validateCandidateReceiptDisk(receipt.disk, s.cfg.Topic); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.completeLocked(receipt, candidateCompletionNotAdmitted, reason)
}

func (s *CandidateReceiptStore) SkippedTotal() uint64 {
	if s == nil {
		return 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.skippedTotal
}

func (s *CandidateReceiptStore) completeLocked(
	receipt CandidateReceipt,
	disposition candidateCompletionDisposition,
	skipReason CandidateNotAdmittedReason,
) error {
	entry := completionFromDisk(receipt.disk)
	entry.Disposition = disposition
	entry.SkipReason = skipReason
	if err := validateCandidateCompletion(entry); err != nil {
		return err
	}
	coordinate := candidateCoordinate{entry.Partition, entry.Offset}
	if known, exists := s.completions[coordinate]; exists {
		if !sameCandidateCompletionIdentity(known, entry) {
			return fmt.Errorf("%w: completed candidate coordinate conflicts", ErrPoisonRecord)
		}
		// Completion is immutable. A retry after completion persistence but
		// before receipt removal inherits the already-durable disposition rather
		// than reclassifying a skipped candidate as sequenced.
		return s.removeCompletedReceipt(receipt, known)
	}
	newCandidate := true
	if known, exists := s.byCandidate[entry.CandidateID]; exists {
		if known.KeySHA256 != entry.KeySHA256 || known.ValueSHA256 != entry.ValueSHA256 {
			return fmt.Errorf("%w: candidate identity conflicts with completion state", ErrPoisonRecord)
		}
		// A duplicate at a new coordinate inherits the original durable
		// disposition. It cannot be reclassified or increment the skip total.
		entry.Disposition = known.Disposition
		entry.SkipReason = known.SkipReason
		newCandidate = false
	}
	updated := make(map[candidateCoordinate]candidateCompletion, len(s.completions)+1)
	for key, value := range s.completions {
		updated[key] = value
	}
	updated[coordinate] = entry
	compacted := make(map[int32]int64, len(s.compacted))
	for partition, floor := range s.compacted {
		compacted[partition] = floor
	}
	entries := sortedCompletions(updated)
	for len(entries) > s.cfg.MaxRecentIDs {
		removed := entries[0]
		entries = entries[1:]
		delete(updated, candidateCoordinate{removed.Partition, removed.Offset})
		if floor, exists := compacted[removed.Partition]; !exists || removed.Offset > floor {
			compacted[removed.Partition] = removed.Offset
		}
	}
	if len(compacted) > maxCandidatePartitions {
		return errors.New("propertycatalog: candidate completion state exceeds partition bound")
	}
	skippedTotal := s.skippedTotal
	if newCandidate && entry.Disposition == candidateCompletionNotAdmitted {
		if skippedTotal == ^uint64(0) {
			return errors.New("propertycatalog: candidate skipped counter is exhausted")
		}
		skippedTotal++
	}
	if err := s.persistCompletions(entries, compacted, skippedTotal); err != nil {
		return err
	}
	s.completions = updated
	s.compacted = compacted
	s.skippedTotal = skippedTotal
	s.rebuildCandidateIndex()
	return s.removeCompletedReceipt(receipt, entry)
}

func (s *CandidateReceiptStore) removeCompletedReceipt(
	receipt CandidateReceipt, entry candidateCompletion,
) error {
	path := receipt.path
	if path == "" {
		path = filepath.Join(s.cfg.Directory, candidateReceiptName(entry.Partition, entry.Offset))
	}
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("propertycatalog: candidate receipt path is not a regular file")
	}
	if err := os.Remove(path); err != nil {
		return err
	}
	s.pendingFiles--
	s.pendingBytes -= info.Size()
	if s.pendingFiles < 0 || s.pendingBytes < 0 {
		return errors.New("propertycatalog: candidate receipt accounting underflow")
	}
	if err := s.syncDir(s.cfg.Directory); err != nil {
		return err
	}
	return nil
}

type candidatePendingFile struct {
	path      string
	size      int64
	partition int32
	offset    int64
}

func (s *CandidateReceiptStore) enumeratePending() ([]candidatePendingFile, error) {
	entries, err := os.ReadDir(s.cfg.Directory)
	if err != nil {
		return nil, err
	}
	files := make([]candidatePendingFile, 0, min(len(entries), s.cfg.MaxPendingFiles))
	for _, entry := range entries {
		partition, offset, ok := parseCandidateReceiptName(entry.Name())
		if !ok {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return nil, err
		}
		if !info.Mode().IsRegular() || info.Size() < 2 || info.Size() > int64(MaxCandidateRecordBytes*2) {
			return nil, errors.New("propertycatalog: candidate receipt has invalid shape/size")
		}
		files = append(files, candidatePendingFile{
			path: filepath.Join(s.cfg.Directory, entry.Name()), size: info.Size(),
			partition: partition, offset: offset,
		})
		if len(files) > s.cfg.MaxPendingFiles {
			return nil, errors.New("propertycatalog: candidate receipt file count exceeds cap")
		}
	}
	sort.Slice(files, func(i, j int) bool {
		if files[i].partition != files[j].partition {
			return files[i].partition < files[j].partition
		}
		return files[i].offset < files[j].offset
	})
	return files, nil
}

func (s *CandidateReceiptStore) loadPending(path string, size int64) (CandidateReceipt, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return CandidateReceipt{}, err
	}
	if !info.Mode().IsRegular() || info.Size() != size || size < 2 || size > int64(MaxCandidateRecordBytes*2) {
		return CandidateReceipt{}, errors.New("propertycatalog: candidate receipt has invalid shape/size")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return CandidateReceipt{}, err
	}
	if raw[len(raw)-1] != '\n' || bytes.Contains(raw[:len(raw)-1], []byte{'\n'}) {
		return CandidateReceipt{}, errors.New("propertycatalog: candidate receipt is not one canonical JSON line")
	}
	body := raw[:len(raw)-1]
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	var disk candidateReceiptDisk
	if err := decoder.Decode(&disk); err != nil {
		return CandidateReceipt{}, err
	}
	if err := requireJSONEOF(decoder); err != nil {
		return CandidateReceipt{}, err
	}
	canonical, err := json.Marshal(disk)
	if err != nil || !bytes.Equal(canonical, body) {
		return CandidateReceipt{}, errors.New("propertycatalog: candidate receipt is not canonical JSON")
	}
	if err := validateCandidateReceiptDisk(disk, s.cfg.Topic); err != nil {
		return CandidateReceipt{}, err
	}
	candidate, err := ParseWireCandidate(disk.Value)
	if err != nil {
		return CandidateReceipt{}, err
	}
	wantKey, _ := CandidateKafkaKey(candidate)
	if candidate.Snapshot().CandidateID != disk.CandidateID || !bytes.Equal(wantKey, disk.Key) {
		return CandidateReceipt{}, errors.New("propertycatalog: candidate receipt content identity mismatch")
	}
	return CandidateReceipt{disk: disk, candidate: candidate, path: path, size: size}, nil
}

func validateCandidateReceiptDisk(disk candidateReceiptDisk, topic string) error {
	if disk.Format != candidateReceiptFormat || disk.Version != candidateReceiptVersion ||
		disk.Topic != topic || disk.Partition < 0 || disk.Offset < 0 ||
		!isLowerSHA256(disk.CandidateID) || !isLowerSHA256(disk.KeySHA256) ||
		!isLowerSHA256(disk.ValueSHA256) || len(disk.Value) > MaxCandidateRecordBytes {
		return errors.New("propertycatalog: candidate receipt metadata is invalid")
	}
	keyDigest := sha256.Sum256(disk.Key)
	valueDigest := sha256.Sum256(disk.Value)
	if hex.EncodeToString(keyDigest[:]) != disk.KeySHA256 ||
		hex.EncodeToString(valueDigest[:]) != disk.ValueSHA256 {
		return errors.New("propertycatalog: candidate receipt checksum mismatch")
	}
	return nil
}

func sameCandidateReceiptDisk(left, right candidateReceiptDisk) bool {
	return left.Format == right.Format && left.Version == right.Version && left.Topic == right.Topic &&
		left.Partition == right.Partition && left.Offset == right.Offset && left.LeaderEpoch == right.LeaderEpoch &&
		left.CandidateID == right.CandidateID && left.KeySHA256 == right.KeySHA256 &&
		left.ValueSHA256 == right.ValueSHA256 && bytes.Equal(left.Key, right.Key) && bytes.Equal(left.Value, right.Value)
}

func completionFromDisk(disk candidateReceiptDisk) candidateCompletion {
	return candidateCompletion{
		Partition: disk.Partition, Offset: disk.Offset, CandidateID: disk.CandidateID,
		KeySHA256: disk.KeySHA256, ValueSHA256: disk.ValueSHA256,
		Disposition: candidateCompletionSequenced,
	}
}

func sameCandidateCompletionIdentity(left, right candidateCompletion) bool {
	return left.Partition == right.Partition && left.Offset == right.Offset &&
		left.CandidateID == right.CandidateID && left.KeySHA256 == right.KeySHA256 &&
		left.ValueSHA256 == right.ValueSHA256
}

func validCandidateNotAdmittedReason(reason CandidateNotAdmittedReason) bool {
	switch reason {
	case CandidateNoCurrentBuildFence, CandidateWorkspaceNotInRollout,
		CandidateOutsideBuildSourceScope:
		return true
	default:
		return false
	}
}

func validateCandidateCompletion(entry candidateCompletion) error {
	if entry.Partition < 0 || entry.Offset < 0 || !isLowerSHA256(entry.CandidateID) ||
		!isLowerSHA256(entry.KeySHA256) || !isLowerSHA256(entry.ValueSHA256) {
		return errors.New("propertycatalog: candidate completion entry is invalid")
	}
	switch entry.Disposition {
	case candidateCompletionSequenced:
		if entry.SkipReason != "" {
			return errors.New("propertycatalog: sequenced candidate completion has a skip reason")
		}
	case candidateCompletionNotAdmitted:
		if !validCandidateNotAdmittedReason(entry.SkipReason) {
			return errors.New("propertycatalog: non-admitted candidate completion has an invalid reason")
		}
	default:
		return errors.New("propertycatalog: candidate completion disposition is invalid")
	}
	return nil
}

func sortedCompletions(values map[candidateCoordinate]candidateCompletion) []candidateCompletion {
	entries := make([]candidateCompletion, 0, len(values))
	for _, entry := range values {
		entries = append(entries, entry)
	}
	sort.Slice(entries, func(i, j int) bool {
		if entries[i].Partition != entries[j].Partition {
			return entries[i].Partition < entries[j].Partition
		}
		return entries[i].Offset < entries[j].Offset
	})
	return entries
}

func (s *CandidateReceiptStore) loadCompletions() error {
	path := filepath.Join(s.cfg.Directory, candidateCompletionFile)
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if len(raw) < 2 || raw[len(raw)-1] != '\n' || bytes.Contains(raw[:len(raw)-1], []byte{'\n'}) {
		return errors.New("propertycatalog: candidate completion state is not one canonical JSON line")
	}
	body := raw[:len(raw)-1]
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	var document candidateCompletionDocument
	if err := decoder.Decode(&document); err != nil {
		return err
	}
	if err := requireJSONEOF(decoder); err != nil {
		return err
	}
	canonical, err := json.Marshal(document)
	if err != nil || !bytes.Equal(canonical, body) || document.Format != candidateCompletionFormat ||
		document.Version != candidateCompletionVersion || len(document.Completions) > s.cfg.MaxRecentIDs ||
		len(document.Compactions) > maxCandidatePartitions {
		return errors.New("propertycatalog: candidate completion state is invalid or non-canonical")
	}
	previous := candidateCoordinate{partition: -1, offset: -1}
	var retainedSkipped uint64
	for index, entry := range document.Completions {
		if err := validateCandidateCompletion(entry); err != nil {
			return err
		}
		if entry.Disposition == candidateCompletionNotAdmitted {
			retainedSkipped++
		}
		coordinate := candidateCoordinate{entry.Partition, entry.Offset}
		if index > 0 && (coordinate.partition < previous.partition ||
			(coordinate.partition == previous.partition && coordinate.offset <= previous.offset)) {
			return errors.New("propertycatalog: candidate completion entries are unsorted or duplicated")
		}
		if prior, exists := s.byCandidate[entry.CandidateID]; exists &&
			(prior.KeySHA256 != entry.KeySHA256 || prior.ValueSHA256 != entry.ValueSHA256) {
			return errors.New("propertycatalog: candidate completion identity conflicts")
		}
		s.completions[coordinate] = entry
		s.byCandidate[entry.CandidateID] = entry
		previous = coordinate
	}
	if document.SkippedTotal < retainedSkipped {
		return errors.New("propertycatalog: candidate skipped total is below retained evidence")
	}
	s.skippedTotal = document.SkippedTotal
	lastPartition := int32(-1)
	for _, entry := range document.Compactions {
		if entry.Partition < 0 || entry.CompactedThrough < 0 || entry.Partition <= lastPartition {
			return errors.New("propertycatalog: candidate compaction entries are invalid or unsorted")
		}
		s.compacted[entry.Partition] = entry.CompactedThrough
		lastPartition = entry.Partition
	}
	return nil
}

func (s *CandidateReceiptStore) persistCompletions(
	entries []candidateCompletion, compacted map[int32]int64, skippedTotal uint64,
) error {
	compactions := make([]candidateCompaction, 0, len(compacted))
	for partition, floor := range compacted {
		compactions = append(compactions, candidateCompaction{Partition: partition, CompactedThrough: floor})
	}
	sort.Slice(compactions, func(i, j int) bool { return compactions[i].Partition < compactions[j].Partition })
	raw, err := json.Marshal(candidateCompletionDocument{
		Format: candidateCompletionFormat, Version: candidateCompletionVersion,
		SkippedTotal: skippedTotal, Completions: entries, Compactions: compactions,
	})
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	if len(raw) > maxProducerStateBytes*4 {
		return errors.New("propertycatalog: candidate completion state exceeds byte limit")
	}
	path := filepath.Join(s.cfg.Directory, candidateCompletionFile)
	return atomicReplace(path, candidateCompletionTempPrefix, raw, s.cfg.Directory, s.syncDir)
}

func (s *CandidateReceiptStore) rebuildCandidateIndex() {
	s.byCandidate = make(map[string]candidateCompletion, len(s.completions))
	for _, entry := range s.completions {
		s.byCandidate[entry.CandidateID] = entry
	}
}

func (s *CandidateReceiptStore) cleanupTemps() error {
	entries, err := os.ReadDir(s.cfg.Directory)
	if err != nil {
		return err
	}
	removed := false
	for _, entry := range entries {
		name := entry.Name()
		if !strings.HasPrefix(name, candidateReceiptTempPrefix) &&
			!strings.HasPrefix(name, candidateCompletionTempPrefix) {
			continue
		}
		path := filepath.Join(s.cfg.Directory, name)
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			continue
		}
		if err := os.Remove(path); err != nil {
			return err
		}
		removed = true
	}
	if removed {
		return s.syncDir(s.cfg.Directory)
	}
	return nil
}

func candidateReceiptName(partition int32, offset int64) string {
	return fmt.Sprintf("%s%010d-%020d%s", candidateReceiptPrefix, partition, offset, candidateReceiptSuffix)
}

func parseCandidateReceiptName(name string) (int32, int64, bool) {
	if !strings.HasPrefix(name, candidateReceiptPrefix) || !strings.HasSuffix(name, candidateReceiptSuffix) {
		return 0, 0, false
	}
	body := strings.TrimSuffix(strings.TrimPrefix(name, candidateReceiptPrefix), candidateReceiptSuffix)
	parts := strings.Split(body, "-")
	if len(parts) != 2 || len(parts[0]) != 10 || len(parts[1]) != 20 {
		return 0, 0, false
	}
	partition, err := strconv.ParseInt(parts[0], 10, 32)
	if err != nil || partition < 0 {
		return 0, 0, false
	}
	offset, err := strconv.ParseInt(parts[1], 10, 64)
	if err != nil || offset < 0 {
		return 0, 0, false
	}
	return int32(partition), offset, true
}

func atomicWriteNew(path, prefix string, raw []byte, directory string, syncDir func(string) error) error {
	if _, err := os.Lstat(path); err == nil {
		return os.ErrExist
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return atomicPublish(path, prefix, raw, directory, syncDir, false)
}

func atomicReplace(path, prefix string, raw []byte, directory string, syncDir func(string) error) error {
	return atomicPublish(path, prefix, raw, directory, syncDir, true)
}

func atomicPublish(
	path, prefix string, raw []byte, directory string, syncDir func(string) error, replace bool,
) error {
	temporary, err := os.CreateTemp(directory, prefix+"*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	keep := true
	defer func() {
		_ = temporary.Close()
		if keep {
			_ = os.Remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return err
	}
	if _, err := temporary.Write(raw); err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if !replace {
		if _, err := os.Lstat(path); err == nil {
			return os.ErrExist
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return err
	}
	keep = false
	return syncDir(directory)
}
