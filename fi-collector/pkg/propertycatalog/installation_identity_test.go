package propertycatalog

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

// Shared with Python tests: these bytes came from InstallationIdentity.encode,
// not a Go encoder. The descriptor reader must preserve their exact contract.
func installationIdentityFixture(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile("testdata/runtime_identity_v1.json")
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func alteredInstallationIdentity(t *testing.T, mutate func(map[string]any)) []byte {
	t.Helper()
	var document map[string]any
	if err := json.Unmarshal(installationIdentityFixture(t), &document); err != nil {
		t.Fatal(err)
	}
	delete(document, "identity_sha256")
	mutate(document)
	unsigned, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	document["identity_sha256"] = fmt.Sprintf("%x", sha256.Sum256(unsigned))
	raw, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	return append(raw, '\n')
}

func TestInstallationIdentityReadsPythonFixtureWithoutMutation(t *testing.T) {
	raw := installationIdentityFixture(t)
	directory := t.TempDir()
	path, err := InstallationIdentityPath(filepath.Join(directory, "revision-fence.json"))
	if err != nil {
		t.Fatal(err)
	}
	if path != filepath.Join(directory, "runtime-identity-v1.json") {
		t.Fatalf("unexpected identity path: %s", path)
	}
	if err := os.WriteFile(path, raw, 0o400); err != nil {
		t.Fatal(err)
	}
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	want := InstallationIdentity{
		Environment: "development", TargetDatabase: "property_catalog_dev",
		CandidateTopic: "futureagi.dev.property-catalog.candidates.v1",
		OrderedTopic:   "futureagi.dev.property-catalog.ordered.v1",
		CatalogEpoch:   3, ProjectionVersion: 1,
		ProducerStreamID: "44444444-4444-4444-8444-444444444444",
		ProjectionFormat: "futureagi.property-catalog-values.v1",
	}
	for range 2 {
		got, err := LoadInstallationIdentity(path)
		if err != nil || got != want {
			t.Fatalf("identity=%+v err=%v", got, err)
		}
	}
	after, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	actual, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(actual, raw) || !os.SameFile(before, after) ||
		before.Mode() != after.Mode() || !before.ModTime().Equal(after.ModTime()) {
		t.Fatalf("reader mutated identity: err=%v", err)
	}
	entries, err := os.ReadDir(filepath.Dir(path))
	if err != nil || len(entries) != 1 {
		t.Fatalf("reader created state: entries=%v err=%v", entries, err)
	}
}

func TestInstallationIdentityRejectsNoncanonicalJSONAndDigest(t *testing.T) {
	raw := installationIdentityFixture(t)
	for name, data := range map[string][]byte{
		"empty":                 nil,
		"no newline":            bytes.TrimSuffix(raw, []byte{'\n'}),
		"extra newline":         append(bytes.Clone(raw), '\n'),
		"CRLF":                  bytes.ReplaceAll(raw, []byte{'\n'}, []byte{'\r', '\n'}),
		"leading whitespace":    append([]byte{' '}, raw...),
		"internal whitespace":   bytes.Replace(raw, []byte(`:3`), []byte(`: 3`), 1),
		"trailing whitespace":   bytes.Replace(raw, []byte("}\n"), []byte("} \n"), 1),
		"second document":       append(bytes.Clone(raw), []byte("{}\n")...),
		"array":                 []byte("[]\n"),
		"null":                  []byte("null\n"),
		"invalid JSON":          []byte("{\n"),
		"out of order":          bytes.Replace(raw, []byte(`"catalog_epoch":3,"environment":"development"`), []byte(`"environment":"development","catalog_epoch":3`), 1),
		"duplicate identical":   bytes.Replace(raw, []byte(`"catalog_epoch":3`), []byte(`"catalog_epoch":3,"catalog_epoch":3`), 1),
		"duplicate conflicting": bytes.Replace(raw, []byte(`"catalog_epoch":3`), []byte(`"catalog_epoch":2,"catalog_epoch":3`), 1),
		"case insensitive key":  bytes.Replace(raw, []byte(`"catalog_epoch"`), []byte(`"CATALOG_EPOCH"`), 1),
		"escaped key":           bytes.Replace(raw, []byte(`"catalog_epoch"`), []byte(`"\u0063atalog_epoch"`), 1),
		"escaped value":         bytes.Replace(raw, []byte(`"development"`), []byte(`"\u0064evelopment"`), 1),
		"tampered digest":       bytes.Replace(raw, []byte(`"catalog_epoch":3`), []byte(`"catalog_epoch":4`), 1),
		"uppercase digest":      bytes.Replace(raw, []byte("cb60535afc2ee830c889ef108ebd8134d2db4a9f0d6b18ad15f0c12fdee5e97a"), []byte("CB60535AFC2EE830C889EF108EBD8134D2DB4A9F0D6B18AD15F0C12FDEE5E97A"), 1),
		"digest null":           bytes.Replace(raw, []byte(`"cb60535afc2ee830c889ef108ebd8134d2db4a9f0d6b18ad15f0c12fdee5e97a"`), []byte(`null`), 1),
		"over size bound":       append(bytes.Repeat([]byte{' '}, maxInstallationIdentityBytes), raw...),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := ParseInstallationIdentity(data); err == nil || errors.Is(err, ErrInstallationIdentityMissing) {
				t.Fatalf("invalid identity err=%v", err)
			}
		})
	}
}

func TestInstallationIdentityRejectsCorrectlySignedInvalidFields(t *testing.T) {
	for _, test := range []struct {
		field string
		value any
	}{
		{"format", "unknown"}, {"version", 2}, {"version", true}, {"version", "1"}, {"version", json.Number("1.0")},
		{"environment", "test"}, {"environment", "Development"}, {"environment", "development "},
		{"projection_format", "futureagi.property-catalog-values.v2"},
		{"catalog_epoch", 0}, {"catalog_epoch", -1}, {"catalog_epoch", 65536}, {"catalog_epoch", true},
		{"catalog_epoch", "3"}, {"catalog_epoch", json.Number("3e0")}, {"catalog_epoch", nil},
		{"projection_version", 0}, {"projection_version", 65536}, {"projection_version", 1.5},
		{"target_database", "default"}, {"target_database", "system"}, {"target_database", "information_schema"},
		{"target_database", "Property_catalog"}, {"target_database", "catalog-db"}, {"target_database", "catalog\n"},
		{"target_database", strings.Repeat("a", 129)}, {"target_database", "catalóg"},
		{"candidate_topic", "."}, {"candidate_topic", ".."}, {"candidate_topic", ""},
		{"candidate_topic", "futureagi.dev.property-catalog.ordered.v1"},
		{"candidate_topic", "a/b"}, {"candidate_topic", strings.Repeat("a", 250)},
		{"ordered_topic", ".."}, {"ordered_topic", "x\n"}, {"ordered_topic", "☃"},
		{"producer_stream_id", "00000000-0000-0000-0000-000000000000"},
		{"producer_stream_id", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
		{"producer_stream_id", "44444444444444448444444444444444"},
		{"producer_stream_id", "{44444444-4444-4444-8444-444444444444}"},
		{"producer_stream_id", "not-a-uuid"},
		{"unknown_field", "signed but unsupported"},
	} {
		t.Run(fmt.Sprintf("%s=%v", test.field, test.value), func(t *testing.T) {
			raw := alteredInstallationIdentity(t, func(doc map[string]any) { doc[test.field] = test.value })
			if _, err := ParseInstallationIdentity(raw); err == nil {
				t.Fatal("invalid signed identity was accepted")
			}
		})
	}
	for _, field := range []string{
		"format", "version", "environment", "target_database", "candidate_topic", "ordered_topic",
		"catalog_epoch", "projection_version", "producer_stream_id", "projection_format",
	} {
		t.Run("missing "+field, func(t *testing.T) {
			raw := alteredInstallationIdentity(t, func(doc map[string]any) { delete(doc, field) })
			if _, err := ParseInstallationIdentity(raw); err == nil {
				t.Fatal("incomplete signed identity was accepted")
			}
		})
	}
}

func TestInstallationIdentityAcceptsPythonContractBounds(t *testing.T) {
	raw := alteredInstallationIdentity(t, func(doc map[string]any) {
		doc["catalog_epoch"], doc["projection_version"] = 65535, 65535
		doc["environment"] = "production"
		doc["target_database"] = "a" + strings.Repeat("_", 127)
		doc["candidate_topic"] = strings.Repeat("a", 249)
		doc["ordered_topic"] = "A.b-1_2"
	})
	if _, err := ParseInstallationIdentity(raw); err != nil {
		t.Fatal(err)
	}
}

func TestInstallationIdentityFileFailuresNeverCreateOrRepairState(t *testing.T) {
	for name, setup := range map[string]func(string) error{
		"missing": func(string) error { return nil },
		"corrupt": func(path string) error { return os.WriteFile(path, []byte("broken\n"), 0o600) },
		"empty":   func(path string) error { return os.WriteFile(path, nil, 0o600) },
		"oversize": func(path string) error {
			return os.WriteFile(path, bytes.Repeat([]byte{'x'}, maxInstallationIdentityBytes+1), 0o600)
		},
		"directory": func(path string) error { return os.Mkdir(path, 0o700) },
		"FIFO":      func(path string) error { return unix.Mkfifo(path, 0o600) },
		"symlink": func(path string) error {
			if err := os.WriteFile(path+".target", installationIdentityFixture(t), 0o600); err != nil {
				return err
			}
			return os.Symlink(path+".target", path)
		},
		"dangling symlink": func(path string) error { return os.Symlink(path+".absent", path) },
	} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), InstallationIdentityFilename)
			if err := setup(path); err != nil {
				t.Fatal(err)
			}
			before, _ := os.Lstat(path)
			var beforeBytes []byte
			if before != nil && before.Mode().IsRegular() {
				beforeBytes, _ = os.ReadFile(path)
			}
			_, err := LoadInstallationIdentity(path)
			if err == nil || errors.Is(err, ErrInstallationIdentityMissing) != (name == "missing") {
				t.Fatalf("file failure err=%v", err)
			}
			after, statErr := os.Lstat(path)
			if before == nil {
				if !errors.Is(statErr, os.ErrNotExist) {
					t.Fatalf("missing identity was created: %v", statErr)
				}
			} else if statErr != nil || !os.SameFile(before, after) || before.Mode() != after.Mode() ||
				before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
				t.Fatalf("invalid identity was modified: %v", statErr)
			} else if before.Mode().IsRegular() {
				afterBytes, readErr := os.ReadFile(path)
				if readErr != nil || !bytes.Equal(beforeBytes, afterBytes) {
					t.Fatalf("invalid identity contents changed: %v", readErr)
				}
			}
		})
	}
}

func TestInstallationIdentityRequiresFixedAbsolutePathAndExistingDirectory(t *testing.T) {
	for _, path := range []string{"", "relative/revision-fence.json", filepath.Join(t.TempDir(), "absent", "revision-fence.json")} {
		if _, err := InstallationIdentityPath(path); err == nil || errors.Is(err, ErrInstallationIdentityMissing) {
			t.Fatalf("invalid fence path %q err=%v", path, err)
		}
	}
	for _, path := range []string{InstallationIdentityFilename, filepath.Join(t.TempDir(), "other.json"), filepath.Join(t.TempDir(), "absent", InstallationIdentityFilename)} {
		if _, err := LoadInstallationIdentity(path); err == nil || errors.Is(err, ErrInstallationIdentityMissing) {
			t.Fatalf("invalid identity path %q err=%v", path, err)
		}
	}
}

func TestInstallationIdentityPermissionErrorIsNotMissing(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root can read a mode-000 file")
	}
	path := filepath.Join(t.TempDir(), InstallationIdentityFilename)
	if err := os.WriteFile(path, installationIdentityFixture(t), 0o000); err != nil {
		t.Fatal(err)
	}
	_, err := LoadInstallationIdentity(path)
	if !errors.Is(err, os.ErrPermission) || errors.Is(err, ErrInstallationIdentityMissing) {
		t.Fatalf("permission denied was not preserved: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0 {
		t.Fatalf("reader changed descriptor permissions: %v", err)
	}
}

func TestInstallationIdentityRequiresExactDestination(t *testing.T) {
	identity, err := ParseInstallationIdentity(installationIdentityFixture(t))
	if err != nil {
		t.Fatal(err)
	}
	if err := identity.RequireDestination(identity.Environment, identity.TargetDatabase, identity.CandidateTopic, identity.OrderedTopic); err != nil {
		t.Fatal(err)
	}
	for field, args := range map[string][4]string{
		"environment":     {"production", identity.TargetDatabase, identity.CandidateTopic, identity.OrderedTopic},
		"target_database": {identity.Environment, "other_catalog", identity.CandidateTopic, identity.OrderedTopic},
		"candidate_topic": {identity.Environment, identity.TargetDatabase, "other.candidates", identity.OrderedTopic},
		"ordered_topic":   {identity.Environment, identity.TargetDatabase, identity.CandidateTopic, "other.ordered"},
	} {
		if err := identity.RequireDestination(args[0], args[1], args[2], args[3]); err == nil || !strings.Contains(err.Error(), field) {
			t.Fatalf("destination mismatch %s err=%v", field, err)
		}
	}
}
