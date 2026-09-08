package catalogwriter

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	spoolFormat     = "futureagi.span-attribute-catalog-job"
	spoolVersion    = 2
	spoolPrefix     = "catalog-job-"
	spoolSuffix     = ".json"
	spoolTempPrefix = ".catalog-tmp-"
	quarantineDir   = "catalog-quarantine"
)

type spool struct {
	dir       string
	syncDirFn func(string) error
}

type pendingFile struct {
	name string
	path string
	size int64
}

type diskJob struct {
	KeyRows      []keyRow    `json:"key_rows"`
	ValueRows    []valueRow  `json:"value_rows"`
	EncodedBytes int         `json:"encoded_bytes"`
	Metadata     JobMetadata `json:"metadata"`
}

type envelopePayload struct {
	ID        string    `json:"id"`
	CreatedAt time.Time `json:"created_at"`
	Job       diskJob   `json:"job"`
}

type diskEnvelope struct {
	Format        string          `json:"format"`
	Version       int             `json:"version"`
	Payload       envelopePayload `json:"payload"`
	PayloadSHA256 string          `json:"payload_sha256"`
}

type loadedEnvelope struct {
	ID        string
	CreatedAt time.Time
	Job       Job
}

func (s spool) prepare() error {
	if err := os.MkdirAll(s.dir, 0o700); err != nil {
		return fmt.Errorf("catalogwriter: prepare spool: %w", err)
	}
	if err := os.Chmod(s.dir, 0o700); err != nil {
		return fmt.Errorf("catalogwriter: secure spool: %w", err)
	}
	if err := s.cleanupStaleTemps(); err != nil {
		return err
	}
	return nil
}

// cleanupStaleTemps removes only the exact regular-file shape emitted by
// os.CreateTemp below: spoolTempPrefix followed by a 1-10 digit uint32 suffix.
// It deliberately uses Lstat and skips symlinks, directories, and similarly
// prefixed operator files. Crash leftovers are outside the published-envelope
// accounting, so removing and directory-fsyncing them at startup closes the
// total-spool-cap hole without risking an arbitrary target through a symlink.
func (s spool) cleanupStaleTemps() error {
	entries, err := os.ReadDir(s.dir)
	if err != nil {
		return fmt.Errorf("catalogwriter: enumerate stale spool temps: %w", err)
	}
	removed := false
	for _, entry := range entries {
		name := entry.Name()
		if !isOwnedTempName(name) {
			continue
		}
		path := filepath.Join(s.dir, name)
		info, err := os.Lstat(path)
		if err != nil {
			return fmt.Errorf("catalogwriter: inspect stale spool temp %s: %w", name, err)
		}
		if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			continue
		}
		if err := os.Remove(path); err != nil {
			return fmt.Errorf("catalogwriter: remove stale spool temp %s: %w", name, err)
		}
		removed = true
	}
	if removed {
		if err := s.syncDirectory(); err != nil {
			return fmt.Errorf("catalogwriter: sync stale spool cleanup: %w", err)
		}
	}
	return nil
}

func isOwnedTempName(name string) bool {
	suffix := strings.TrimPrefix(name, spoolTempPrefix)
	if suffix == name || len(suffix) == 0 || len(suffix) > 10 {
		return false
	}
	for index := 0; index < len(suffix); index++ {
		if suffix[index] < '0' || suffix[index] > '9' {
			return false
		}
	}
	_, err := strconv.ParseUint(suffix, 10, 32)
	return err == nil
}

func (s spool) save(job Job, remainingBytes int64) (pendingFile, error) {
	id, err := randomEnvelopeID()
	if err != nil {
		return pendingFile{}, fmt.Errorf("catalogwriter: generate spool id: %w", err)
	}
	createdAt := time.Now().UTC()
	payload := envelopePayload{
		ID: id, CreatedAt: createdAt,
		Job: diskJob{
			KeyRows: job.keyRows, ValueRows: job.valueRows,
			EncodedBytes: job.encodedBytes, Metadata: job.Metadata(),
		},
	}
	payloadJSON, err := json.Marshal(payload)
	if err != nil {
		return pendingFile{}, fmt.Errorf("catalogwriter: encode spool payload: %w", err)
	}
	digest := sha256.Sum256(payloadJSON)
	envelope := diskEnvelope{
		Format: spoolFormat, Version: spoolVersion, Payload: payload,
		PayloadSHA256: hex.EncodeToString(digest[:]),
	}
	envelopeJSON, err := json.Marshal(envelope)
	if err != nil {
		return pendingFile{}, fmt.Errorf("catalogwriter: encode spool envelope: %w", err)
	}
	envelopeJSON = append(envelopeJSON, '\n')
	envelopeBytes := int64(len(envelopeJSON))
	if envelopeBytes > remainingBytes {
		return pendingFile{}, fmt.Errorf(
			"catalogwriter: spool byte limit reached: new %d available %d",
			envelopeBytes, remainingBytes,
		)
	}

	temporary, err := os.CreateTemp(s.dir, spoolTempPrefix+"*")
	if err != nil {
		return pendingFile{}, fmt.Errorf("catalogwriter: create spool temp: %w", err)
	}
	temporaryPath := temporary.Name()
	keepTemporary := true
	defer func() {
		_ = temporary.Close()
		if keepTemporary {
			_ = os.Remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return pendingFile{}, fmt.Errorf("catalogwriter: chmod spool temp: %w", err)
	}
	if _, err := temporary.Write(envelopeJSON); err != nil {
		return pendingFile{}, fmt.Errorf("catalogwriter: write spool temp: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		return pendingFile{}, fmt.Errorf("catalogwriter: sync spool temp: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return pendingFile{}, fmt.Errorf("catalogwriter: close spool temp: %w", err)
	}

	name := fmt.Sprintf("%s%020d-%s%s", spoolPrefix, createdAt.UnixNano(), id, spoolSuffix)
	path := filepath.Join(s.dir, name)
	if err := os.Rename(temporaryPath, path); err != nil {
		return pendingFile{}, fmt.Errorf("catalogwriter: publish spool envelope: %w", err)
	}
	keepTemporary = false
	if err := s.syncDirectory(); err != nil {
		// The final name may exist even if directory fsync fails. Return the
		// pending reference so callers can discover it on restart; never unlink
		// a possibly durable job merely because durability could not be proven.
		return pendingFile{name: name, path: path, size: envelopeBytes}, fmt.Errorf("catalogwriter: sync published spool: %w", err)
	}
	return pendingFile{name: name, path: path, size: envelopeBytes}, nil
}

// usage reconstructs O(1) runtime admission counters exactly once at startup.
// Enumeration is bounded by maxFiles and ignores atomic-write temp files.
func (s spool) usage(maxFiles int) (int64, int, error) {
	files, err := s.enumerate(maxFiles)
	if err != nil {
		return 0, 0, err
	}
	quarantined, err := s.enumerateQuarantine(maxFiles - len(files))
	if err != nil {
		return 0, 0, err
	}
	files = append(files, quarantined...)
	var total int64
	for _, file := range files {
		if file.size > 0 && total > int64(^uint64(0)>>1)-file.size {
			return 0, 0, errors.New("catalogwriter: spool usage overflow")
		}
		total += file.size
	}
	return total, len(files), nil
}

func (s spool) enumerateQuarantine(maxFiles int) ([]pendingFile, error) {
	dir := s.quarantineDirectory()
	info, err := os.Lstat(dir)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("catalogwriter: inspect quarantine directory: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return nil, errors.New("catalogwriter: quarantine path is not a directory")
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, fmt.Errorf("catalogwriter: enumerate quarantine: %w", err)
	}
	files := make([]pendingFile, 0, min(len(entries), max(maxFiles, 0)))
	for _, entry := range entries {
		name := entry.Name()
		if !strings.HasPrefix(name, spoolPrefix) || !strings.HasSuffix(name, spoolSuffix) {
			continue
		}
		entryInfo, err := entry.Info()
		if err != nil {
			return nil, fmt.Errorf("catalogwriter: inspect quarantine entry %s: %w", name, err)
		}
		if !entryInfo.Mode().IsRegular() {
			continue
		}
		files = append(files, pendingFile{
			name: name, path: filepath.Join(dir, name), size: entryInfo.Size(),
		})
		if len(files) > maxFiles {
			return nil, fmt.Errorf(
				"catalogwriter: spool file limit exceeded: quarantine exceeds remaining capacity %d",
				maxFiles,
			)
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].name < files[j].name })
	return files, nil
}

func (s spool) enumerate(maxFiles int) ([]pendingFile, error) {
	entries, err := os.ReadDir(s.dir)
	if err != nil {
		return nil, fmt.Errorf("catalogwriter: enumerate spool: %w", err)
	}
	files := make([]pendingFile, 0, min(len(entries), maxFiles))
	for _, entry := range entries {
		name := entry.Name()
		if !strings.HasPrefix(name, spoolPrefix) || !strings.HasSuffix(name, spoolSuffix) {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return nil, fmt.Errorf("catalogwriter: inspect spool entry %s: %w", name, err)
		}
		if !info.Mode().IsRegular() {
			continue
		}
		files = append(files, pendingFile{name: name, path: filepath.Join(s.dir, name), size: info.Size()})
		if len(files) > maxFiles {
			return nil, fmt.Errorf("catalogwriter: spool file limit exceeded: found more than %d", maxFiles)
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].name < files[j].name })
	return files, nil
}

func (s spool) load(file pendingFile, maxBytes int64) (loadedEnvelope, error) {
	info, err := os.Lstat(file.path)
	if err != nil {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: stat envelope %s: %w", file.name, err)
	}
	if !info.Mode().IsRegular() {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: envelope %s is not a regular file", file.name)
	}
	if info.Size() <= 0 || info.Size() > maxBytes {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: envelope %s size %d exceeds bound %d", file.name, info.Size(), maxBytes)
	}
	raw, err := os.ReadFile(file.path)
	if err != nil {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: read envelope %s: %w", file.name, err)
	}
	var envelope diskEnvelope
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&envelope); err != nil {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: decode envelope %s: %w", file.name, err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		if err == nil {
			err = errors.New("unexpected trailing JSON value")
		}
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: decode envelope %s: %w", file.name, err)
	}
	if envelope.Format != spoolFormat || envelope.Version != spoolVersion {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: unsupported envelope %s format/version", file.name)
	}
	if !validEnvelopeID(envelope.Payload.ID) || envelope.Payload.CreatedAt.IsZero() {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: invalid envelope %s identity", file.name)
	}
	if !strings.HasSuffix(strings.TrimSuffix(file.name, spoolSuffix), "-"+envelope.Payload.ID) {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: envelope %s id/name mismatch", file.name)
	}
	payloadJSON, err := json.Marshal(envelope.Payload)
	if err != nil {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: re-encode envelope %s payload: %w", file.name, err)
	}
	digest := sha256.Sum256(payloadJSON)
	if envelope.PayloadSHA256 != hex.EncodeToString(digest[:]) {
		return loadedEnvelope{}, fmt.Errorf("catalogwriter: envelope %s checksum mismatch", file.name)
	}
	return loadedEnvelope{
		ID: envelope.Payload.ID, CreatedAt: envelope.Payload.CreatedAt.UTC(),
		Job: Job{
			keyRows: envelope.Payload.Job.KeyRows, valueRows: envelope.Payload.Job.ValueRows,
			encodedBytes: envelope.Payload.Job.EncodedBytes,
			metadata:     cloneJobMetadata(envelope.Payload.Job.Metadata),
		},
	}, nil
}

func (s spool) remove(file pendingFile) (bool, error) {
	if err := os.Remove(file.path); err != nil {
		return false, err
	}
	return true, s.syncDirectory()
}

// quarantine atomically moves one published envelope into a dedicated
// directory on the same filesystem. The caller deliberately keeps its bytes
// and file in spool accounting so permanent failures remain bounded.
func (s spool) quarantine(file pendingFile) (bool, error) {
	dir, err := s.prepareQuarantineDirectory()
	if err != nil {
		return false, err
	}
	destination := filepath.Join(dir, file.name)
	if _, err := os.Lstat(destination); err == nil {
		return false, fmt.Errorf("catalogwriter: quarantine envelope %s already exists", file.name)
	} else if !errors.Is(err, os.ErrNotExist) {
		return false, fmt.Errorf("catalogwriter: inspect quarantine envelope %s: %w", file.name, err)
	}
	if err := os.Rename(file.path, destination); err != nil {
		return false, fmt.Errorf("catalogwriter: quarantine envelope %s: %w", file.name, err)
	}
	var syncErr error
	if err := s.syncPath(dir); err != nil {
		syncErr = errors.Join(syncErr, fmt.Errorf("sync quarantine directory: %w", err))
	}
	if err := s.syncDirectory(); err != nil {
		syncErr = errors.Join(syncErr, fmt.Errorf("sync spool directory: %w", err))
	}
	return true, syncErr
}

func (s spool) prepareQuarantineDirectory() (string, error) {
	dir := s.quarantineDirectory()
	if err := os.Mkdir(dir, 0o700); err != nil && !errors.Is(err, os.ErrExist) {
		return "", fmt.Errorf("catalogwriter: create quarantine directory: %w", err)
	}
	info, err := os.Lstat(dir)
	if err != nil {
		return "", fmt.Errorf("catalogwriter: inspect quarantine directory: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return "", errors.New("catalogwriter: quarantine path is not a directory")
	}
	if err := os.Chmod(dir, 0o700); err != nil {
		return "", fmt.Errorf("catalogwriter: secure quarantine directory: %w", err)
	}
	return dir, nil
}

func (s spool) quarantineDirectory() string { return filepath.Join(s.dir, quarantineDir) }

func (s spool) syncDirectory() error {
	return s.syncPath(s.dir)
}

func (s spool) syncPath(path string) error {
	if s.syncDirFn != nil {
		return s.syncDirFn(path)
	}
	return syncDirectory(path)
}

func randomEnvelopeID() (string, error) {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(value[:]), nil
}

func validEnvelopeID(value string) bool {
	if len(value) != 32 {
		return false
	}
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == 16
}

func syncDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer directory.Close()
	if err := directory.Sync(); err != nil && !errors.Is(err, os.ErrInvalid) {
		return err
	}
	return nil
}
