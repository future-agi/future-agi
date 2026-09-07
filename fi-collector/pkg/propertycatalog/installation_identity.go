package propertycatalog

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"

	"golang.org/x/sys/unix"
)

const (
	InstallationIdentityFilename = "runtime-identity-v1.json"
	installationIdentityFormat   = "futureagi.property-catalog-runtime-identity"
	installationIdentityVersion  = 1
	installationProjectionFormat = "futureagi.property-catalog-values.v1"
	maxInstallationIdentityBytes = 4096
)

// ErrInstallationIdentityMissing means the shared directory exists but its
// descriptor has not been published. Only this error permits startup retry;
// Go must never initialize, repair, or replace the lifecycle-owned identity.
var ErrInstallationIdentityMissing = errors.New("propertycatalog: persisted installation identity is missing")

var installationDatabasePattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,127}$`)
var installationTopicPattern = regexp.MustCompile(`^[A-Za-z0-9._-]{1,249}$`)

// InstallationIdentity binds an ordered stream to its persisted destination.
// The Python lifecycle initializes it only after inspecting database evidence.
type InstallationIdentity struct {
	Environment       string
	TargetDatabase    string
	CandidateTopic    string
	OrderedTopic      string
	CatalogEpoch      uint16
	ProjectionVersion uint16
	ProducerStreamID  string
	ProjectionFormat  string
}

// Field order is Python json.dumps(sort_keys=True) order. All accepted strings
// are ASCII, so Go's compact JSON encoding equals Python's ensure_ascii=True
// encoding. Re-encoding also rejects duplicate, omitted, null, case-insensitive,
// escaped, or out-of-order fields instead of accepting json.Decoder's coercions.
type installationIdentityDocument struct {
	CandidateTopic    string `json:"candidate_topic"`
	CatalogEpoch      uint16 `json:"catalog_epoch"`
	Environment       string `json:"environment"`
	Format            string `json:"format"`
	IdentitySHA256    string `json:"identity_sha256"`
	OrderedTopic      string `json:"ordered_topic"`
	ProducerStreamID  string `json:"producer_stream_id"`
	ProjectionFormat  string `json:"projection_format"`
	ProjectionVersion uint16 `json:"projection_version"`
	TargetDatabase    string `json:"target_database"`
	Version           uint16 `json:"version"`
}

// InstallationIdentityPath resolves the fixed descriptor beside the revision
// fence. Like the Python identity_path helper, it requires an existing absolute
// fence directory; neither the fence nor the descriptor is created here.
func InstallationIdentityPath(revisionFenceFile string) (string, error) {
	if !filepath.IsAbs(revisionFenceFile) {
		return "", errors.New("propertycatalog: identity requires an absolute revision fence path")
	}
	directory := filepath.Dir(revisionFenceFile)
	info, err := os.Stat(directory)
	if err != nil {
		return "", fmt.Errorf("propertycatalog: inspect installation identity directory: %w", err)
	}
	if !info.IsDir() {
		return "", errors.New("propertycatalog: installation identity parent must be a directory")
	}
	return filepath.Join(directory, InstallationIdentityFilename), nil
}

// LoadInstallationIdentity reads a bounded regular file without following a
// descriptor symlink. O_NONBLOCK ensures a corrupt FIFO cannot hang startup;
// fstat and the bounded read refer to the opened descriptor, avoiding an
// lstat/open race. This function never writes durable state.
func LoadInstallationIdentity(path string) (InstallationIdentity, error) {
	if !filepath.IsAbs(path) || filepath.Base(path) != InstallationIdentityFilename {
		return InstallationIdentity{}, errors.New("propertycatalog: identity path must use the fixed absolute filename")
	}
	if _, err := InstallationIdentityPath(path); err != nil {
		return InstallationIdentity{}, err
	}
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		readErr := fmt.Errorf("propertycatalog: open installation identity: %w", &os.PathError{Op: "open", Path: path, Err: err})
		if errors.Is(err, os.ErrNotExist) {
			return InstallationIdentity{}, errors.Join(ErrInstallationIdentityMissing, readErr)
		}
		return InstallationIdentity{}, readErr
	}
	source := os.NewFile(uintptr(fd), path)
	defer source.Close()
	info, err := source.Stat()
	if err != nil {
		return InstallationIdentity{}, fmt.Errorf("propertycatalog: inspect installation identity: %w", err)
	}
	if !info.Mode().IsRegular() || info.Size() > maxInstallationIdentityBytes {
		return InstallationIdentity{}, errors.New("propertycatalog: installation identity must be a bounded regular file")
	}
	raw, err := io.ReadAll(io.LimitReader(source, maxInstallationIdentityBytes+1))
	if err != nil {
		return InstallationIdentity{}, fmt.Errorf("propertycatalog: read installation identity: %w", err)
	}
	return ParseInstallationIdentity(raw)
}

// ParseInstallationIdentity accepts exactly the canonical JSON line and SHA256
// contract in Python property_catalog/installation_identity.py. It deliberately
// exposes no identity generator or persistence API.
func ParseInstallationIdentity(raw []byte) (InstallationIdentity, error) {
	if len(raw) == 0 || len(raw) > maxInstallationIdentityBytes || raw[len(raw)-1] != '\n' {
		return InstallationIdentity{}, errors.New("propertycatalog: identity must be a bounded canonical JSON line")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var document installationIdentityDocument
	if err := decoder.Decode(&document); err != nil {
		return InstallationIdentity{}, fmt.Errorf("propertycatalog: decode installation identity: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return InstallationIdentity{}, err
	}
	if document.Format != installationIdentityFormat || document.Version != installationIdentityVersion {
		return InstallationIdentity{}, errors.New("propertycatalog: identity format/version is unsupported")
	}
	identity := InstallationIdentity{
		Environment: document.Environment, TargetDatabase: document.TargetDatabase,
		CandidateTopic: document.CandidateTopic, OrderedTopic: document.OrderedTopic,
		CatalogEpoch: document.CatalogEpoch, ProjectionVersion: document.ProjectionVersion,
		ProducerStreamID: document.ProducerStreamID, ProjectionFormat: document.ProjectionFormat,
	}
	if err := identity.validate(); err != nil {
		return InstallationIdentity{}, err
	}
	if !isLowerSHA256(document.IdentitySHA256) {
		return InstallationIdentity{}, errors.New("propertycatalog: identity digest must be a lowercase SHA256")
	}
	canonical, err := json.Marshal(document)
	if err != nil || !bytes.Equal(append(canonical, '\n'), raw) {
		return InstallationIdentity{}, errors.New("propertycatalog: installation identity JSON is not canonical")
	}
	// Maps encode with lexicographically sorted keys, exactly as Python's
	// _canonical(unsigned). The digest field is excluded, not set to empty.
	unsigned, err := json.Marshal(map[string]any{
		"candidate_topic": document.CandidateTopic, "catalog_epoch": document.CatalogEpoch,
		"environment": document.Environment, "format": document.Format,
		"ordered_topic": document.OrderedTopic, "producer_stream_id": document.ProducerStreamID,
		"projection_format": document.ProjectionFormat, "projection_version": document.ProjectionVersion,
		"target_database": document.TargetDatabase, "version": document.Version,
	})
	if err != nil {
		return InstallationIdentity{}, err
	}
	if fmt.Sprintf("%x", sha256.Sum256(unsigned)) != document.IdentitySHA256 {
		return InstallationIdentity{}, errors.New("propertycatalog: installation identity digest mismatch")
	}
	return identity, nil
}

func (identity InstallationIdentity) validate() error {
	if identity.Environment != DevelopmentEnvironment && identity.Environment != ProductionEnvironment {
		return errors.New("propertycatalog: identity environment is unsupported")
	}
	if !installationDatabasePattern.MatchString(identity.TargetDatabase) ||
		identity.TargetDatabase == "default" || identity.TargetDatabase == "system" || identity.TargetDatabase == "information_schema" {
		return errors.New("propertycatalog: identity target database must be an isolated catalog")
	}
	for _, topic := range []string{identity.CandidateTopic, identity.OrderedTopic} {
		if !installationTopicPattern.MatchString(topic) || topic == "." || topic == ".." {
			return errors.New("propertycatalog: identity Kafka topic is invalid")
		}
	}
	if identity.CandidateTopic == identity.OrderedTopic {
		return errors.New("propertycatalog: identity requires distinct Kafka topics")
	}
	if identity.CatalogEpoch == 0 || identity.ProjectionVersion == 0 {
		return errors.New("propertycatalog: identity epoch/projection must be positive UInt16 values")
	}
	if identity.ProjectionFormat != installationProjectionFormat {
		return errors.New("propertycatalog: identity projection format is unsupported")
	}
	return validateCanonicalUUID("identity producer stream", identity.ProducerStreamID)
}

func (identity InstallationIdentity) RequireDestination(environment, targetDatabase, candidateTopic, orderedTopic string) error {
	for _, field := range []struct{ name, actual, expected string }{
		{"environment", identity.Environment, environment},
		{"target_database", identity.TargetDatabase, targetDatabase},
		{"candidate_topic", identity.CandidateTopic, candidateTopic},
		{"ordered_topic", identity.OrderedTopic, orderedTopic},
	} {
		if field.actual != field.expected {
			return fmt.Errorf("propertycatalog: persisted catalog identity conflicts with %s", field.name)
		}
	}
	return nil
}
