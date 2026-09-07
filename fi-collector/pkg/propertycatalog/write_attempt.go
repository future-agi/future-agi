package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"

	"github.com/google/uuid"
	"golang.org/x/sys/unix"
)

const (
	WriteAttemptDirectory = "consumer-write-attempts"
	maxWriteAttemptBytes  = 24 << 20 // base64-encoded bounded INSERT plus metadata
	AttemptPrepared       = "prepared"
	AttemptSent           = "sent"
	AttemptAcknowledged   = "acknowledged"
	AttemptComplete       = "complete"
)

var ErrWriteUnresolved = errors.New("propertycatalog: exact INSERT attempt remains unresolved; no replay or Kafka commit")

// ExactWriteAttempt freezes the actual transmitted bytes, endpoint, topology,
// token and unique query ID before I/O. State never returns to prepared. There
// is deliberately no automatic resend or time-based expiry transition.
// The JSON line is canonical; record_sha256 covers every other field.
type ExactWriteAttempt struct {
	Body             []byte            `json:"body"`
	BodySHA256       string            `json:"body_sha256"`
	Endpoint         string            `json:"endpoint"`
	EnvelopeID       string            `json:"envelope_id"`
	Format           string            `json:"format"`
	QueryID          string            `json:"query_id"`
	RecordSHA256     string            `json:"record_sha256"`
	Settings         map[string]string `json:"settings"`
	SettlementSHA256 string            `json:"settlement_sha256"`
	State            string            `json:"state"`
	Table            string            `json:"table"`
	Token            string            `json:"token"`
	TopologySHA256   string            `json:"topology_sha256"`
	Version          uint16            `json:"version"`
}

// AttemptSettlement is evidence supplied by a reviewed proof adapter. A
// successful complete HTTP ACK is persisted separately by the sender. For an
// unknown outcome, a positive original-query completion or exact Keeper
// settlement witness can establish success. Absence, elapsed time, and rows
// visible without settlement are NEVER success or permission to retry.
type AttemptSettlement struct {
	Outcome        string // settled_success, settled_abort, unresolved
	QueryID        string
	BodySHA256     string
	TopologySHA256 string
	WitnessSHA256  string
}

// CatalogWriteProof is the explicit network/admission boundary. Implementations
// must inspect every admitted member, not a healthy subset. Cover must prove
// each exact definition/ledger row and semantic min/max coverage of aggregate
// values, not equality of physical row counts. Resolve is read-only: a positive
// settled_abort remains a repair requirement, not an INSERT replay instruction.
type CatalogWriteProof interface {
	Attest(context.Context, *WritePolicy) error
	Cover(context.Context, *WritePolicy, ExactWriteAttempt) error
	Resolve(context.Context, *WritePolicy, ExactWriteAttempt) (AttemptSettlement, error)
}

type WriteAttemptJournal struct{ directory *os.File }

// OpenWriteAttemptJournal opens a fixed child of a pre-existing durable shared
// installation directory. It neither creates a new installation nor repairs or
// deletes unknown files. The opened directory FD anchors all subsequent I/O.
// The mount must survive consumer restart/rebalance and be shared by every
// process able to commit the ordered topic; ephemeral local storage is invalid.
func OpenWriteAttemptJournal(installationDirectory string) (*WriteAttemptJournal, error) {
	if !filepath.IsAbs(installationDirectory) || filepath.Clean(installationDirectory) == "/" {
		return nil, errors.New("propertycatalog: journal requires an absolute installation directory")
	}
	parent, err := openPrivateDirectory(installationDirectory)
	if err != nil {
		return nil, err
	}
	defer parent.Close()
	if err := unix.Mkdirat(int(parent.Fd()), WriteAttemptDirectory, 0o700); err != nil && !errors.Is(err, unix.EEXIST) {
		return nil, err
	}
	if err := parent.Sync(); err != nil {
		return nil, err
	}
	fd, err := unix.Openat(int(parent.Fd()), WriteAttemptDirectory,
		unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	dir := os.NewFile(uintptr(fd), WriteAttemptDirectory)
	if err := requirePrivateFile(dir, true); err != nil {
		dir.Close()
		return nil, err
	}
	return &WriteAttemptJournal{directory: dir}, nil
}

func openPrivateDirectory(path string) (*os.File, error) {
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), path)
	if err := requirePrivateFile(f, true); err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}

func requirePrivateFile(file *os.File, directory bool) error {
	var st unix.Stat_t
	if err := unix.Fstat(int(file.Fd()), &st); err != nil {
		return err
	}
	kind := uint32(unix.S_IFREG)
	if directory {
		kind = unix.S_IFDIR
	}
	if uint32(st.Mode)&unix.S_IFMT != kind || st.Uid != uint32(os.Geteuid()) || st.Mode&0o022 != 0 ||
		(!directory && st.Nlink != 1) {
		return errors.New("propertycatalog: journal requires owned, non-shared-writable regular files/directories")
	}
	return nil
}

func (j *WriteAttemptJournal) Close() error {
	if j == nil || j.directory == nil {
		return nil
	}
	return j.directory.Close()
}

// AttemptLease holds an exclusive cross-process lock throughout prepare,
// network I/O, proof and durable completion. A competing consumer cannot use a
// receipt while its original writer might still be dispatching the request.
type AttemptLease struct {
	journal *WriteAttemptJournal
	lock    *os.File
	key     string
	token   string
}

func (j *WriteAttemptJournal) Lock(token string) (*AttemptLease, error) {
	if j == nil || j.directory == nil || !validAttemptToken(token) {
		return nil, errors.New("propertycatalog: invalid journal or exact attempt token")
	}
	key := sha256Hex([]byte(token))
	fd, err := unix.Openat(int(j.directory.Fd()), key+".lock",
		unix.O_CREAT|unix.O_RDWR|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0o600)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), key+".lock")
	if err := requirePrivateFile(f, false); err != nil {
		f.Close()
		return nil, err
	}
	if err := unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB); err != nil {
		f.Close()
		return nil, fmt.Errorf("propertycatalog: exact attempt already owned: %w", err)
	}
	return &AttemptLease{journal: j, lock: f, key: key, token: token}, nil
}

func (l *AttemptLease) Close() error {
	if l == nil || l.lock == nil {
		return nil
	}
	f := l.lock
	l.lock = nil
	return errors.Join(unix.Flock(int(f.Fd()), unix.LOCK_UN), f.Close())
}

func (l *AttemptLease) Load() (ExactWriteAttempt, error) {
	if l == nil || l.lock == nil {
		return ExactWriteAttempt{}, errors.New("propertycatalog: attempt read requires its lock")
	}
	fd, err := unix.Openat(int(l.journal.directory.Fd()), l.key+".json",
		unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return ExactWriteAttempt{}, err
	}
	f := os.NewFile(uintptr(fd), l.key+".json")
	defer f.Close()
	if err := requirePrivateFile(f, false); err != nil {
		return ExactWriteAttempt{}, err
	}
	info, err := f.Stat()
	if err != nil || info.Size() <= 0 || info.Size() > maxWriteAttemptBytes {
		return ExactWriteAttempt{}, errors.New("propertycatalog: attempt is not a bounded regular record")
	}
	raw, err := io.ReadAll(io.LimitReader(f, maxWriteAttemptBytes+1))
	if err != nil || len(raw) > maxWriteAttemptBytes {
		return ExactWriteAttempt{}, errors.New("propertycatalog: incomplete or oversized attempt read")
	}
	var attempt ExactWriteAttempt
	if err := decodeCanonicalLine(raw, &attempt); err != nil {
		return ExactWriteAttempt{}, err
	}
	if err := attempt.validate(); err != nil {
		return ExactWriteAttempt{}, err
	}
	if attempt.Token != l.token || attempt.RecordSHA256 != attemptDigest(attempt) {
		return ExactWriteAttempt{}, errors.New("propertycatalog: attempt identity or checksum mismatch")
	}
	return attempt, nil
}

func (l *AttemptLease) Prepare(intent ExactWriteAttempt) (ExactWriteAttempt, error) {
	if l == nil || l.lock == nil || intent.Token != l.token {
		return ExactWriteAttempt{}, errors.New("propertycatalog: attempt preparation requires exact lock identity")
	}
	existing, err := l.Load()
	if err == nil {
		if !sameAttemptIntent(existing, intent) {
			return ExactWriteAttempt{}, errors.New("propertycatalog: durable INSERT attempt conflicts with proposed bytes/destination")
		}
		return existing, nil
	}
	if !errors.Is(err, unix.ENOENT) {
		return ExactWriteAttempt{}, err
	}
	intent = cloneAttempt(intent)
	intent.Format, intent.Version = "futureagi.property-catalog-write-attempt", 1
	intent.QueryID, intent.State = uuid.NewString(), AttemptPrepared
	intent.BodySHA256 = sha256Hex(intent.Body)
	if err := intent.validate(); err != nil {
		return ExactWriteAttempt{}, err
	}
	if err := l.persist(intent); err != nil {
		return ExactWriteAttempt{}, err
	}
	return l.Load()
}

func (l *AttemptLease) Advance(expected ExactWriteAttempt, state string) (ExactWriteAttempt, error) {
	if state == AttemptAcknowledged {
		return ExactWriteAttempt{}, errors.New("propertycatalog: acknowledgement requires a positive settlement witness")
	}
	return l.advance(expected, state, "")
}

func (l *AttemptLease) Acknowledge(expected ExactWriteAttempt, witnessSHA256 string) (ExactWriteAttempt, error) {
	if !isLowerSHA256(witnessSHA256) {
		return ExactWriteAttempt{}, errors.New("propertycatalog: invalid settlement witness digest")
	}
	return l.advance(expected, AttemptAcknowledged, witnessSHA256)
}

func (l *AttemptLease) advance(expected ExactWriteAttempt, state, witnessSHA256 string) (ExactWriteAttempt, error) {
	current, err := l.Load()
	if err != nil {
		return ExactWriteAttempt{}, err
	}
	if current.RecordSHA256 != expected.RecordSHA256 || !sameAttemptIntent(current, expected) {
		return ExactWriteAttempt{}, errors.New("propertycatalog: attempt transition compare-and-swap failed")
	}
	allowed := current.State == AttemptPrepared && state == AttemptSent ||
		current.State == AttemptSent && state == AttemptAcknowledged ||
		current.State == AttemptAcknowledged && state == AttemptComplete
	if !allowed {
		return ExactWriteAttempt{}, errors.New("propertycatalog: unsafe attempt transition (no replay or proof bypass)")
	}
	current.State = state
	if state == AttemptAcknowledged {
		current.SettlementSHA256 = witnessSHA256
	}
	if err := l.persist(current); err != nil {
		return ExactWriteAttempt{}, err
	}
	return l.Load()
}

func (l *AttemptLease) persist(attempt ExactWriteAttempt) error {
	attempt.RecordSHA256 = attemptDigest(attempt)
	raw, err := json.Marshal(attempt)
	if err != nil || len(raw)+1 > maxWriteAttemptBytes {
		return errors.New("propertycatalog: cannot encode bounded attempt")
	}
	raw = append(raw, '\n')
	temporary := ".attempt-" + uuid.NewString() + ".tmp"
	directoryFD := int(l.journal.directory.Fd())
	fd, err := unix.Openat(directoryFD, temporary, unix.O_CREAT|unix.O_EXCL|unix.O_WRONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0o600)
	if err != nil {
		return err
	}
	f := os.NewFile(uintptr(fd), temporary)
	defer f.Close()
	// Only our exclusively-created temporary is removed on failure. Existing
	// records, corruption and interrupted temporaries are never auto-cleaned.
	defer unix.Unlinkat(directoryFD, temporary, 0)
	if n, err := f.Write(raw); err != nil || n != len(raw) {
		return errors.Join(io.ErrShortWrite, err)
	}
	if err := f.Sync(); err != nil {
		return err
	}
	if err := unix.Renameat(directoryFD, temporary, directoryFD, l.key+".json"); err != nil {
		return err
	}
	return l.journal.directory.Sync()
}

func (a ExactWriteAttempt) validate() error {
	if a.Format != "futureagi.property-catalog-write-attempt" || a.Version != 1 || !validAttemptToken(a.Token) ||
		!isLowerSHA256(a.EnvelopeID) || !isLowerSHA256(a.TopologySHA256) ||
		len(a.Body) == 0 || len(a.Body) > maxCatalogInsertBytes || a.BodySHA256 != sha256Hex(a.Body) {
		return errors.New("propertycatalog: invalid exact write attempt")
	}
	if len(a.Settings) != 6 || a.Settings["async_insert"] != "0" || a.Settings["wait_end_of_query"] != "1" ||
		a.Settings["insert_quorum_parallel"] != "1" || a.Settings["insert_deduplicate"] != "1" {
		return errors.New("propertycatalog: attempt lacks explicit synchronous/quorum settings")
	}
	quorum, quorumErr := strconv.ParseUint(a.Settings["insert_quorum"], 10, 64)
	timeoutMS, timeoutErr := strconv.ParseUint(a.Settings["insert_quorum_timeout"], 10, 64)
	if quorumErr != nil || quorum == 1 || strconv.FormatUint(quorum, 10) != a.Settings["insert_quorum"] ||
		timeoutErr != nil || timeoutMS == 0 || timeoutMS > uint64(MaxDeliveryTimeout.Milliseconds()) ||
		strconv.FormatUint(timeoutMS, 10) != a.Settings["insert_quorum_timeout"] {
		return errors.New("propertycatalog: invalid exact attempt quorum/timeout")
	}
	if a.Table != string(DefinitionTable) && a.Table != string(AttributeValueTable) && a.Table != "property_catalog_deliveries" {
		return errors.New("propertycatalog: attempt targets forbidden table")
	}
	if !strings.HasPrefix(a.Token, "property-catalog-v1:"+a.EnvelopeID+":") ||
		(strings.HasSuffix(a.Token, ":delivery") != (a.Table == "property_catalog_deliveries")) {
		return errors.New("propertycatalog: token does not bind the exact envelope/table")
	}
	if (a.State == AttemptAcknowledged || a.State == AttemptComplete) && !isLowerSHA256(a.SettlementSHA256) ||
		(a.State == AttemptPrepared || a.State == AttemptSent) && a.SettlementSHA256 != "" {
		return errors.New("propertycatalog: attempt state conflicts with settlement witness")
	}
	if _, err := bareClickHouseOrigin(a.Endpoint); err != nil {
		return err
	}
	if err := validateCanonicalUUID("attempt query", a.QueryID); err != nil {
		return err
	}
	switch a.State {
	case AttemptPrepared, AttemptSent, AttemptAcknowledged, AttemptComplete:
		return nil
	default:
		return errors.New("propertycatalog: unknown attempt state")
	}
}

func validAttemptToken(token string) bool {
	parts := strings.Split(token, ":")
	if len(parts) < 3 || parts[0] != "property-catalog-v1" || !isLowerSHA256(parts[1]) {
		return false
	}
	if len(parts) == 3 && parts[2] == "delivery" {
		return true
	}
	if len(parts) != 4 || parts[2] != "chunk" {
		return false
	}
	index, err := strconv.ParseUint(parts[3], 10, 16)
	return err == nil && index < MaxChunks && strconv.FormatUint(index, 10) == parts[3]
}

func sameAttemptIntent(a, b ExactWriteAttempt) bool {
	return a.Token == b.Token && a.EnvelopeID == b.EnvelopeID && a.TopologySHA256 == b.TopologySHA256 &&
		a.Endpoint == b.Endpoint && a.Table == b.Table && bytes.Equal(a.Body, b.Body) && reflect.DeepEqual(a.Settings, b.Settings)
}

func cloneAttempt(a ExactWriteAttempt) ExactWriteAttempt {
	a.Body = bytes.Clone(a.Body)
	settings := make(map[string]string, len(a.Settings))
	for name, value := range a.Settings {
		settings[name] = value
	}
	a.Settings = settings
	return a
}

func attemptDigest(attempt ExactWriteAttempt) string {
	raw, _ := json.Marshal(attempt)
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(raw, &fields)
	delete(fields, "record_sha256")
	raw, _ = json.Marshal(fields)
	return sha256Hex(raw)
}
