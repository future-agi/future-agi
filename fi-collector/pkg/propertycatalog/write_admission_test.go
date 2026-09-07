package propertycatalog

import (
	"encoding/json"
	"errors"
	"flag"
	"os"
	"path/filepath"
	"testing"

	"golang.org/x/sys/unix"
)

func TestWriteAdmissionSecureFactoryRejectsMissingForeignSymlinkFIFOWithoutHTTP(t *testing.T) {
	for _, kind := range []string{"missing", "foreign-installation", "symlink", "fifo", "writable", "valid"} {
		t.Run(kind, func(t *testing.T) {
			directory := t.TempDir()
			identityRaw := installationIdentityFixture(t)
			var identity installationIdentityDocument
			if err := json.Unmarshal(identityRaw, &identity); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(directory, InstallationIdentityFilename), identityRaw, 0600); err != nil {
				t.Fatal(err)
			}
			doc := testWriteAdmission(t, 1, false)
			doc.Database = identity.TargetDatabase
			doc.InstallationSHA256 = identity.IdentitySHA256
			if kind == "foreign-installation" {
				doc.InstallationSHA256 = testDigest("different install")
			}
			doc.TopologySHA256 = admissionDigest(doc)
			raw, _ := json.Marshal(doc)
			path := filepath.Join(directory, WriteAdmissionFilename)
			var err error
			switch kind {
			case "missing":
			case "symlink":
				err = os.Symlink(filepath.Join(directory, InstallationIdentityFilename), path)
			case "fifo":
				err = unix.Mkfifo(path, 0600)
			default:
				err = os.WriteFile(path, append(raw, '\n'), 0600)
			}
			if err != nil {
				t.Fatal(err)
			}
			if kind == "writable" {
				if err := os.Chmod(path, 0666); err != nil {
					t.Fatal(err)
				}
			}
			policy, err := LoadWritePolicy(directory, identity.Environment, identity.TargetDatabase, identity.OrderedTopic)
			if (err == nil) != (kind == "valid") {
				t.Fatalf("policy=%v error=%v", policy, err)
			}
			if errors.Is(err, ErrWriteAdmissionPending) != (kind == "missing") {
				t.Fatalf("only an absent descriptor may wait: kind=%s error=%v", kind, err)
			}
			if _, err := os.Stat(filepath.Join(directory, WriteAttemptDirectory)); !os.IsNotExist(err) {
				t.Fatal("read-only admission created journal")
			}
			if kind == "valid" {
				for _, topic := range []string{"", identity.OrderedTopic + ".foreign"} {
					if _, err := LoadWritePolicy(directory, identity.Environment, identity.TargetDatabase, topic); err == nil {
						t.Fatal("foreign ordered topic accepted")
					}
				}
			}
		})
	}
}

// Optional offline verification of retained real-producer evidence. Supply
// absolute runtime directories with go test -run this-test -args dir... . No
// connection, journal initialization or fixture mutation occurs here.
func TestWriteAdmissionProvidedProducerArtifacts(t *testing.T) {
	if len(flag.Args()) == 0 {
		t.Skip("pass retained producer runtime directories as positional test arguments for offline verification")
	}
	for _, directory := range flag.Args() {
		t.Run(filepath.Base(directory), func(t *testing.T) {
			if !filepath.IsAbs(directory) {
				t.Fatal("producer artifact directory must be absolute")
			}
			identity, err := LoadInstallationIdentity(filepath.Join(directory, InstallationIdentityFilename))
			if err != nil {
				t.Fatal(err)
			}
			before, err := os.ReadDir(directory)
			if err != nil {
				t.Fatal(err)
			}
			policy, err := LoadWritePolicy(directory, identity.Environment, identity.TargetDatabase, identity.OrderedTopic)
			if err != nil {
				t.Fatal(err)
			}
			after, err := os.ReadDir(directory)
			if err != nil || len(before) != len(after) {
				t.Fatal("offline codec check created state")
			}
			t.Logf("validated producer identity+canonical admission: family=%s members=%d topology_sha256=%s", policy.admission.Family, len(policy.admission.Members), policy.admission.TopologySHA256)
		})
	}
}

func TestWriteAdmissionStartupMissingFilesProgressWithoutCreatingState(t *testing.T) {
	directory := t.TempDir()
	identityRaw := installationIdentityFixture(t)
	var identity installationIdentityDocument
	if err := json.Unmarshal(identityRaw, &identity); err != nil {
		t.Fatal(err)
	}
	load := func() error {
		_, err := LoadWritePolicy(directory, identity.Environment, identity.TargetDatabase, identity.OrderedTopic)
		return err
	}
	if err := load(); !errors.Is(err, ErrWriteAdmissionPending) {
		t.Fatalf("both files absent: %v", err)
	}
	entries, err := os.ReadDir(directory)
	if err != nil || len(entries) != 0 {
		t.Fatal("waiting initialized durable state")
	}
	if err := os.WriteFile(filepath.Join(directory, InstallationIdentityFilename), identityRaw, 0600); err != nil {
		t.Fatal(err)
	}
	if err := load(); !errors.Is(err, ErrWriteAdmissionPending) {
		t.Fatalf("admission absent: %v", err)
	}
	doc := testWriteAdmission(t, 1, false)
	doc.Database, doc.InstallationSHA256 = identity.TargetDatabase, identity.IdentitySHA256
	doc.TopologySHA256 = admissionDigest(doc)
	raw, _ := json.Marshal(doc)
	if err := os.WriteFile(filepath.Join(directory, WriteAdmissionFilename), append(raw, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	if err := load(); err != nil {
		t.Fatalf("publication did not become ready: %v", err)
	}
	entries, err = os.ReadDir(directory)
	if err != nil || len(entries) != 2 {
		t.Fatal("admission reader created unexpected files")
	}
}

func TestWriteAdmissionMissingSiblingDoesNotHideExistingCorruption(t *testing.T) {
	for _, name := range []string{InstallationIdentityFilename, WriteAdmissionFilename} {
		for _, kind := range []string{"malformed", "empty", "symlink", "fifo", "foreign"} {
			t.Run(name+"/"+kind, func(t *testing.T) {
				directory := t.TempDir()
				path := filepath.Join(directory, name)
				var err error
				switch kind {
				case "malformed":
					err = os.WriteFile(path, []byte("{}\n"), 0600)
				case "empty":
					err = os.WriteFile(path, nil, 0600)
				case "symlink":
					err = os.Symlink(filepath.Join(directory, "absent"), path)
				case "fifo":
					err = unix.Mkfifo(path, 0600)
				case "foreign":
					if name == InstallationIdentityFilename {
						err = os.WriteFile(path, installationIdentityFixture(t), 0600)
					} else {
						doc := testWriteAdmission(t, 1, false)
						raw, _ := json.Marshal(doc)
						err = os.WriteFile(path, append(raw, '\n'), 0600)
					}
				}
				if err != nil {
					t.Fatal(err)
				}
				_, err = LoadWritePolicy(directory, DevelopmentEnvironment, "property_catalog_dev_other", "other.topic")
				if err == nil || errors.Is(err, ErrWriteAdmissionPending) {
					t.Fatalf("existing %s hidden by missing sibling: %v", kind, err)
				}
			})
		}
	}
	_, err := LoadWritePolicy(filepath.Join(t.TempDir(), "missing-mount"), DevelopmentEnvironment, "property_catalog_dev", "ordered.topic")
	if err == nil || errors.Is(err, ErrWriteAdmissionPending) {
		t.Fatalf("missing shared mount treated as pending publication: %v", err)
	}
}

func TestClickHouseSinkCannotConstructWithoutCompletionAuthority(t *testing.T) {
	cfg := ClickHouseSinkConfig{URL: "http://unused.invalid:8123", Database: "property_catalog_dev_sink_test", Environment: DevelopmentEnvironment, Username: "writer"}
	if err := ValidateClickHouseDestination(cfg); err != nil {
		t.Fatal(err)
	}
	if _, err := NewClickHouseSink(cfg); err == nil {
		t.Fatal("local destination validation granted write authority")
	}
	cfg.WritePolicy, _ = NewWritePolicy(testWriteAdmission(t, 1, false))
	if _, err := NewClickHouseSink(cfg); err == nil {
		t.Fatal("descriptor alone granted write authority")
	}
	cfg.WriteJournal, _ = OpenWriteAttemptJournal(t.TempDir())
	defer cfg.WriteJournal.Close()
	if _, err := NewClickHouseSink(cfg); err == nil {
		t.Fatal("missing real proof defaulted to success")
	}
	cfg.WriteProof = &explicitWriteProof{}
	cfg.URL = "https://service.invalid:8443"
	if _, err := NewClickHouseSink(cfg); err == nil {
		t.Fatal("HTTPS writer silently downgraded to admitted HTTP")
	}
	if _, err := NewHTTPWriteProof(cfg, cfg.WritePolicy); err == nil {
		t.Fatal("HTTPS proof reader silently downgraded to admitted HTTP")
	}
}
