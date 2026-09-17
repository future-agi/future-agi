package propertycatalog

import (
	"bytes"
	"context"
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
	"time"
)

const (
	spoolFormat     = "futureagi.property-catalog-spool-envelope"
	spoolVersion    = uint16(1)
	spoolPrefix     = "property-envelope-"
	spoolSuffix     = ".json"
	spoolTempPrefix = ".property-tmp-"
)

type SpoolConfig struct {
	Directory string
	MaxFiles  int
	MaxBytes  int64
}

type EnvelopePublisher interface {
	Publish(context.Context, WireEnvelope) error
}

type Spool struct {
	mu       sync.Mutex
	replayMu sync.Mutex
	cfg      SpoolConfig
	files    int
	bytes    int64
	now      func() time.Time
	syncDir  func(string) error
}

type diskSpoolEnvelope struct {
	Format         string    `json:"format"`
	Version        uint16    `json:"version"`
	CreatedAt      time.Time `json:"created_at"`
	EnvelopeID     string    `json:"envelope_id"`
	Envelope       []byte    `json:"envelope"`
	EnvelopeSHA256 string    `json:"envelope_sha256"`
}

type pendingSpoolFile struct {
	name string
	path string
	size int64
}

type ReplayResult struct {
	Attempted int
	Delivered int
}

func NewSpool(cfg SpoolConfig) (*Spool, error) {
	if cfg.Directory == "" || !filepath.IsAbs(cfg.Directory) {
		return nil, errors.New("propertycatalog: spool directory must be absolute")
	}
	if cfg.MaxFiles <= 0 || cfg.MaxFiles > 1_000_000 || cfg.MaxBytes <= 0 {
		return nil, errors.New("propertycatalog: spool file/byte limits are invalid")
	}
	if err := os.MkdirAll(cfg.Directory, 0o700); err != nil {
		return nil, fmt.Errorf("propertycatalog: prepare spool: %w", err)
	}
	if err := os.Chmod(cfg.Directory, 0o700); err != nil {
		return nil, fmt.Errorf("propertycatalog: secure spool: %w", err)
	}
	spool := &Spool{cfg: cfg, now: time.Now, syncDir: syncDirectory}
	if err := spool.cleanupTemps(); err != nil {
		return nil, err
	}
	files, err := spool.enumerate()
	if err != nil {
		return nil, err
	}
	for _, file := range files {
		spool.bytes += file.size
	}
	spool.files = len(files)
	if spool.files > cfg.MaxFiles || spool.bytes > cfg.MaxBytes {
		return nil, errors.New("propertycatalog: existing spool exceeds configured cap")
	}
	return spool, nil
}

// Enqueue fsyncs an immutable envelope before returning. Re-enqueueing the
// same envelope ID is an exact no-op if and only if the durable bytes match.
func (s *Spool) Enqueue(envelope WireEnvelope) error {
	if s == nil {
		return errors.New("propertycatalog: nil spool")
	}
	raw, err := envelope.MarshalBinary()
	if err != nil {
		return err
	}
	snapshot := envelope.Snapshot()
	if snapshot.EnvelopeID == "" {
		return errors.New("propertycatalog: cannot spool invalid envelope")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	files, err := s.enumerate()
	if err != nil {
		return err
	}
	for _, file := range files {
		if strings.HasSuffix(file.name, "-"+snapshot.EnvelopeID+spoolSuffix) {
			existing, loadErr := s.load(file)
			if loadErr != nil {
				return loadErr
			}
			existingRaw, _ := existing.MarshalBinary()
			if !bytes.Equal(existingRaw, raw) {
				return errors.New("propertycatalog: spool envelope ID conflicts with durable bytes")
			}
			return nil
		}
	}
	createdAt := s.now().UTC()
	if createdAt.UnixNano() < 0 {
		return errors.New("propertycatalog: spool clock precedes Unix epoch")
	}
	digest := sha256.Sum256(raw)
	disk := diskSpoolEnvelope{
		Format: spoolFormat, Version: spoolVersion, CreatedAt: createdAt,
		EnvelopeID: snapshot.EnvelopeID, Envelope: bytes.Clone(raw),
		EnvelopeSHA256: hex.EncodeToString(digest[:]),
	}
	encoded, err := json.Marshal(disk)
	if err != nil {
		return fmt.Errorf("propertycatalog: encode spool envelope: %w", err)
	}
	encoded = append(encoded, '\n')
	if s.files >= s.cfg.MaxFiles || int64(len(encoded)) > s.cfg.MaxBytes-s.bytes {
		return errors.New("propertycatalog: spool capacity reached")
	}
	temporary, err := os.CreateTemp(s.cfg.Directory, spoolTempPrefix+"*")
	if err != nil {
		return fmt.Errorf("propertycatalog: create spool temporary: %w", err)
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
	if _, err := temporary.Write(encoded); err != nil {
		return fmt.Errorf("propertycatalog: write spool temporary: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		return fmt.Errorf("propertycatalog: sync spool temporary: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("propertycatalog: close spool temporary: %w", err)
	}
	name := fmt.Sprintf("%s%020d-%s%s", spoolPrefix, createdAt.UnixNano(), snapshot.EnvelopeID, spoolSuffix)
	path := filepath.Join(s.cfg.Directory, name)
	if err := os.Rename(temporaryPath, path); err != nil {
		return fmt.Errorf("propertycatalog: publish spool envelope: %w", err)
	}
	keep = false
	// Count the final name immediately. If directory fsync fails its durability
	// is ambiguous, so conservative in-process accounting must still include it.
	s.files++
	s.bytes += int64(len(encoded))
	if err := s.syncDir(s.cfg.Directory); err != nil {
		return fmt.Errorf("propertycatalog: sync published spool envelope: %w", err)
	}
	return nil
}

// Replay publishes oldest-first and deletes only after synchronous ACK. A
// corrupt or failed envelope and every later file remain intact.
func (s *Spool) Replay(ctx context.Context, publisher EnvelopePublisher) (ReplayResult, error) {
	if s == nil || publisher == nil {
		return ReplayResult{}, errors.New("propertycatalog: replay requires spool and publisher")
	}
	if ctx == nil {
		return ReplayResult{}, errors.New("propertycatalog: nil replay context")
	}
	s.replayMu.Lock()
	defer s.replayMu.Unlock()
	s.mu.Lock()
	files, err := s.enumerate()
	s.mu.Unlock()
	if err != nil {
		return ReplayResult{}, err
	}
	result := ReplayResult{}
	for _, file := range files {
		if err := ctx.Err(); err != nil {
			return result, err
		}
		result.Attempted++
		envelope, err := s.load(file)
		if err != nil {
			return result, err
		}
		if err := publisher.Publish(ctx, envelope); err != nil {
			return result, fmt.Errorf("propertycatalog: publish spooled %s: %w", envelope.EnvelopeID(), err)
		}
		s.mu.Lock()
		if err := os.Remove(file.path); err != nil {
			s.mu.Unlock()
			return result, fmt.Errorf("propertycatalog: remove acknowledged spool envelope: %w", err)
		}
		if err := s.syncDir(s.cfg.Directory); err != nil {
			s.mu.Unlock()
			return result, fmt.Errorf("propertycatalog: sync acknowledged spool removal: %w", err)
		}
		s.files--
		s.bytes -= file.size
		if s.files < 0 || s.bytes < 0 {
			s.mu.Unlock()
			return result, errors.New("propertycatalog: spool accounting underflow")
		}
		s.mu.Unlock()
		result.Delivered++
	}
	return result, nil
}

// PendingEnvelopes returns validated immutable envelopes in replay order. It
// exists so the producer can reconstruct every per-stream chain after a crash;
// callers cannot mutate spool contents through the returned values.
func (s *Spool) PendingEnvelopes() ([]WireEnvelope, error) {
	if s == nil {
		return nil, errors.New("propertycatalog: nil spool")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	files, err := s.enumerate()
	if err != nil {
		return nil, err
	}
	result := make([]WireEnvelope, 0, len(files))
	for _, file := range files {
		envelope, err := s.load(file)
		if err != nil {
			return nil, err
		}
		result = append(result, envelope)
	}
	return result, nil
}

func (s *Spool) load(file pendingSpoolFile) (WireEnvelope, error) {
	info, err := os.Lstat(file.path)
	if err != nil {
		return WireEnvelope{}, err
	}
	if !info.Mode().IsRegular() || info.Size() <= 1 || info.Size() > int64(MaxRecordBytes*2) {
		return WireEnvelope{}, errors.New("propertycatalog: invalid spool file shape/size")
	}
	raw, err := os.ReadFile(file.path)
	if err != nil {
		return WireEnvelope{}, err
	}
	if raw[len(raw)-1] != '\n' || bytes.Contains(raw[:len(raw)-1], []byte{'\n'}) {
		return WireEnvelope{}, errors.New("propertycatalog: spool file is not one canonical JSON line")
	}
	body := raw[:len(raw)-1]
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	var disk diskSpoolEnvelope
	if err := decoder.Decode(&disk); err != nil {
		return WireEnvelope{}, fmt.Errorf("propertycatalog: decode spool envelope: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return WireEnvelope{}, err
	}
	canonical, err := json.Marshal(disk)
	if err != nil || !bytes.Equal(canonical, body) {
		return WireEnvelope{}, errors.New("propertycatalog: spool envelope is not canonical JSON")
	}
	if disk.Format != spoolFormat || disk.Version != spoolVersion || !isLowerSHA256(disk.EnvelopeID) ||
		!isLowerSHA256(disk.EnvelopeSHA256) {
		return WireEnvelope{}, errors.New("propertycatalog: invalid spool envelope metadata")
	}
	digest := sha256.Sum256(disk.Envelope)
	if hex.EncodeToString(digest[:]) != disk.EnvelopeSHA256 {
		return WireEnvelope{}, errors.New("propertycatalog: spool envelope checksum mismatch")
	}
	envelope, err := ParseWireEnvelope(disk.Envelope)
	if err != nil {
		return WireEnvelope{}, err
	}
	if envelope.EnvelopeID() != disk.EnvelopeID {
		return WireEnvelope{}, errors.New("propertycatalog: spool/envelope identity mismatch")
	}
	return envelope, nil
}

func (s *Spool) enumerate() ([]pendingSpoolFile, error) {
	entries, err := os.ReadDir(s.cfg.Directory)
	if err != nil {
		return nil, err
	}
	files := make([]pendingSpoolFile, 0, min(len(entries), s.cfg.MaxFiles))
	for _, entry := range entries {
		if !strings.HasPrefix(entry.Name(), spoolPrefix) || !strings.HasSuffix(entry.Name(), spoolSuffix) {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return nil, err
		}
		if !info.Mode().IsRegular() {
			continue
		}
		files = append(files, pendingSpoolFile{
			name: entry.Name(), path: filepath.Join(s.cfg.Directory, entry.Name()), size: info.Size(),
		})
		if len(files) > s.cfg.MaxFiles {
			return nil, errors.New("propertycatalog: spool file count exceeds cap")
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].name < files[j].name })
	return files, nil
}

func (s *Spool) cleanupTemps() error {
	entries, err := os.ReadDir(s.cfg.Directory)
	if err != nil {
		return err
	}
	removed := false
	for _, entry := range entries {
		if !ownedTempName(entry.Name()) {
			continue
		}
		path := filepath.Join(s.cfg.Directory, entry.Name())
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

func ownedTempName(name string) bool {
	suffix := strings.TrimPrefix(name, spoolTempPrefix)
	if suffix == name || len(suffix) == 0 || len(suffix) > 10 {
		return false
	}
	_, err := strconv.ParseUint(suffix, 10, 32)
	return err == nil
}

func syncDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}
