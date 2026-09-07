package propertycatalog

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// ErrWriteAdmissionPending permits startup waiting ONLY when a fixed descriptor
// is absent under the already validated shared installation directory. Existing
// malformed/foreign/unsafe files and a missing mount never produce this marker.
var ErrWriteAdmissionPending = errors.New("propertycatalog: lifecycle installation/admission publication is pending")

// LoadWritePolicy consumes the lifecycle-owned real-probe output. It never
// initializes admission, derives standalone from an environment, or replaces
// existing identity. The parent directory must be the existing shared mount.
func LoadWritePolicy(directory, environment, database, orderedTopic string) (*WritePolicy, error) {
	if !filepath.IsAbs(directory) {
		return nil, errors.New("propertycatalog: admission requires absolute shared installation directory")
	}
	dir, err := openPrivateDirectory(directory)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	identityRaw, identityErr := readJournalRegular(int(dir.Fd()), InstallationIdentityFilename, maxInstallationIdentityBytes)
	if identityErr != nil && !errors.Is(identityErr, os.ErrNotExist) {
		return nil, identityErr
	}
	if identityErr == nil {
		identity, err := ParseInstallationIdentity(identityRaw)
		if err != nil {
			return nil, err
		}
		if identity.Environment != environment || identity.TargetDatabase != database || identity.OrderedTopic != orderedTopic {
			return nil, errors.New("propertycatalog: installation conflicts with consumer destination/topic")
		}
	}
	// Inspect an existing admission even when identity has not been published.
	// Otherwise corruption of that file could be hidden by indefinite waiting.
	raw, admissionErr := readJournalRegular(int(dir.Fd()), WriteAdmissionFilename, 128<<10)
	if admissionErr != nil && !errors.Is(admissionErr, os.ErrNotExist) {
		return nil, admissionErr
	}
	var policy *WritePolicy
	if admissionErr == nil {
		policy, err = ParseWriteAdmission(raw)
		if err != nil {
			return nil, err
		}
		if policy.admission.Environment != environment || policy.admission.Database != database {
			return nil, errors.New("propertycatalog: write admission conflicts with consumer destination")
		}
	}
	if identityErr != nil {
		return nil, errors.Join(ErrWriteAdmissionPending, fmt.Errorf("await %s: %w", InstallationIdentityFilename, identityErr))
	}
	if admissionErr != nil {
		return nil, errors.Join(ErrWriteAdmissionPending, fmt.Errorf("await %s: %w", WriteAdmissionFilename, admissionErr))
	}
	var document installationIdentityDocument
	if err := json.Unmarshal(identityRaw, &document); err != nil {
		return nil, err
	}
	if policy.admission.InstallationSHA256 != document.IdentitySHA256 || policy.admission.Environment != environment || policy.admission.Database != database {
		return nil, errors.New("propertycatalog: write admission does not bind persisted installation")
	}
	return policy, nil
}

// ConfigureDurableClickHouse establishes all mandatory local capabilities.
// Attest is still required at read/write time: successful file parsing alone
// does not prove a schema, a grant, or a shared Keeper ensemble.
func ConfigureDurableClickHouse(write, read ClickHouseSinkConfig) (*ClickHouseSink, *HTTPWriteProof, *WriteAttemptJournal, error) {
	if write.Username == read.Username || write.Database != read.Database || write.Environment != read.Environment {
		return nil, nil, nil, errors.New("propertycatalog: separate matching write/proof principals required")
	}
	policy, err := LoadWritePolicy(write.InstallationDirectory, write.Environment, write.Database, write.OrderedTopic)
	if err != nil {
		return nil, nil, nil, err
	}
	journal, err := OpenWriteAttemptJournal(write.InstallationDirectory)
	if err != nil {
		return nil, nil, nil, err
	}
	proof, err := NewHTTPWriteProof(read, policy)
	if err != nil {
		journal.Close()
		return nil, nil, nil, err
	}
	write.WritePolicy, write.WriteJournal, write.WriteProof = policy, journal, proof
	sink, err := NewClickHouseSink(write)
	if err != nil {
		journal.Close()
		return nil, nil, nil, err
	}
	proof.writer = sink
	return sink, proof, journal, nil
}
