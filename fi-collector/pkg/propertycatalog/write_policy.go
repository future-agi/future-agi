package propertycatalog

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"
)

const WriteAdmissionFilename = "write-admission-v1.json"

// WriteAdmission is produced by installation admission, never by a writer.
// JSON fields are in Python sort_keys=True order. The digest excludes only
// topology_sha256. Endpoints must be direct serving nodes, not load balancers.
// Admission owns proving that keeper_identity_sha256 identifies ONE ensemble;
// equal-looking paths on independent Keepers are not that proof.
type WriteAdmission struct {
	Database             string        `json:"database"`
	Environment          string        `json:"environment"`
	Family               string        `json:"family"`
	Format               string        `json:"format"`
	InstallationSHA256   string        `json:"installation_sha256"`
	KeeperIdentitySHA256 string        `json:"keeper_identity_sha256"`
	Members              []WriteMember `json:"members"`
	TopologySHA256       string        `json:"topology_sha256"`
	Version              uint16        `json:"version"`
}

type WriteMember struct {
	DatabaseUUID string       `json:"database_uuid"`
	Hostname     string       `json:"hostname"`
	Name         string       `json:"name"`
	ServerUUID   string       `json:"server_uuid"`
	Tables       []WriteTable `json:"tables"`
	URL          string       `json:"url"`
}

type WriteTable struct {
	CreateSHA256 string   `json:"create_sha256"`
	Engine       string   `json:"engine"`
	KeeperPath   string   `json:"keeper_path"`
	Name         string   `json:"name"`
	ReplicaName  string   `json:"replica_name"`
	ReplicaNames []string `json:"replica_names"`
	UUID         string   `json:"uuid"`
}

var catalogEngineFamilies = map[string]string{
	"property_catalog_activation_control_events": "MergeTree",
	"property_catalog_activations":               "ReplacingMergeTree",
	"property_catalog_checkpoints":               "ReplacingMergeTree",
	"property_catalog_deliveries":                "MergeTree",
	"property_catalog_source_streams":            "ReplacingMergeTree",
	"property_definition_catalog":                "MergeTree",
	"span_attribute_value_catalog":               "AggregatingMergeTree",
}

// WritePolicy is immutable after validation. Missing admission is an error,
// including in development. N is the admitted membership, never active_replicas.
type WritePolicy struct{ admission WriteAdmission }

func ParseWriteAdmission(raw []byte) (*WritePolicy, error) {
	if len(raw) == 0 || len(raw) > 128<<10 || raw[len(raw)-1] != '\n' {
		return nil, errors.New("propertycatalog: write admission must be a bounded canonical JSON line")
	}
	var document WriteAdmission
	if err := decodeCanonicalLine(raw, &document); err != nil {
		return nil, err
	}
	return NewWritePolicy(document)
}

func NewWritePolicy(document WriteAdmission) (*WritePolicy, error) {
	encoded, err := json.Marshal(document)
	if err != nil || len(encoded)+1 > 128<<10 {
		return nil, errors.New("propertycatalog: admitted topology exceeds bounded descriptor size")
	}
	if document.Format != "futureagi.property-catalog-write-admission" || document.Version != 1 ||
		!safeCatalogDatabaseIdentifier(document.Database) || !isLowerSHA256(document.InstallationSHA256) ||
		!isLowerSHA256(document.TopologySHA256) {
		return nil, errors.New("propertycatalog: invalid write admission identity")
	}
	if document.Environment != DevelopmentEnvironment && document.Environment != ProductionEnvironment {
		return nil, errors.New("propertycatalog: invalid admission environment")
	}
	n := len(document.Members)
	switch document.Family {
	case "standalone":
		if n != 1 || document.KeeperIdentitySHA256 != "" || document.Environment == ProductionEnvironment {
			return nil, errors.New("propertycatalog: standalone requires one proven server; production still requires three replicas")
		}
	case "replicated":
		if n < 2 || !isLowerSHA256(document.KeeperIdentitySHA256) ||
			(document.Environment == ProductionEnvironment && n != 3) {
			return nil, errors.New("propertycatalog: replicated admission requires full membership (exactly three in production)")
		}
	default:
		return nil, errors.New("propertycatalog: unknown admitted engine family")
	}
	names, urls, hosts, servers := map[string]bool{}, map[string]bool{}, map[string]bool{}, map[string]bool{}
	paths := map[string]string{}
	for i, member := range document.Members {
		if !safeProofName(member.Name) || !safeProofName(member.Hostname) || names[member.Name] || urls[member.URL] || hosts[member.Hostname] ||
			(i > 0 && document.Members[i-1].Name >= member.Name) {
			return nil, errors.New("propertycatalog: admission members must be sorted distinct named servers")
		}
		if _, err := bareClickHouseOrigin(member.URL); err != nil {
			return nil, err
		}
		if err := validateCanonicalUUID("admitted database", member.DatabaseUUID); err != nil {
			return nil, err
		}
		if err := validateCanonicalUUID("admitted server", member.ServerUUID); err != nil {
			return nil, err
		}
		if servers[member.ServerUUID] {
			return nil, errors.New("propertycatalog: admission aliases one physical server")
		}
		servers[member.ServerUUID] = true
		names[member.Name], urls[member.URL], hosts[member.Hostname] = true, true, true
		if len(member.Tables) != len(catalogEngineFamilies) {
			return nil, errors.New("propertycatalog: admission requires exactly seven catalog tables")
		}
		for j, table := range member.Tables {
			engine, ok := catalogEngineFamilies[table.Name]
			if !ok || (j > 0 && member.Tables[j-1].Name >= table.Name) || !isLowerSHA256(table.CreateSHA256) {
				return nil, errors.New("propertycatalog: invalid admitted schema inventory")
			}
			if err := validateCanonicalUUID("admitted table", table.UUID); err != nil {
				return nil, err
			}
			if document.Family == "replicated" {
				engine = "Replicated" + engine
				if table.ReplicaName != member.Name || len(table.ReplicaNames) != n || !slices.IsSorted(table.ReplicaNames) ||
					!strings.HasPrefix(table.KeeperPath, "/") || strings.HasSuffix(table.KeeperPath, "/") || strings.Contains(table.KeeperPath, "//") ||
					len(table.KeeperPath) > 1024 || strings.ContainsAny(table.KeeperPath, "\x00\r\n'{}") {
					return nil, errors.New("propertycatalog: invalid admitted Keeper path or replica membership")
				}
				for k, replica := range table.ReplicaNames {
					if replica != document.Members[k].Name {
						return nil, errors.New("propertycatalog: table membership differs from serving membership")
					}
				}
				if path, found := paths[table.Name]; found && path != table.KeeperPath {
					return nil, errors.New("propertycatalog: replicas do not share a table Keeper path")
				}
				paths[table.Name] = table.KeeperPath
			} else if table.KeeperPath != "" || table.ReplicaName != "" || table.ReplicaNames == nil || len(table.ReplicaNames) != 0 {
				return nil, errors.New("propertycatalog: standalone must have explicit empty replica membership")
			}
			if table.Engine != engine {
				return nil, errors.New("propertycatalog: table engine differs from admitted family")
			}
		}
	}
	if document.Family == "replicated" && document.KeeperIdentitySHA256 != keeperMembershipDigest(document) {
		return nil, errors.New("propertycatalog: Keeper identity digest does not bind admitted servers and tables")
	}
	if admissionDigest(document) != document.TopologySHA256 {
		return nil, errors.New("propertycatalog: admission topology digest mismatch")
	}
	// Own all nested slices; caller mutation must not shrink the quorum.
	raw, _ := json.Marshal(document)
	var frozen WriteAdmission
	_ = json.Unmarshal(raw, &frozen)
	return &WritePolicy{admission: frozen}, nil
}

// This restart-stable digest identifies the expected binding, not an
// attestation result. Live session and is_active probes remain mandatory.
func keeperMembershipDigest(document WriteAdmission) string {
	members := make([]map[string]string, 0, len(document.Members))
	for _, member := range document.Members {
		members = append(members, map[string]string{"name": member.Name, "server_uuid": member.ServerUUID})
	}
	tables := make([]map[string]string, 0, len(catalogEngineFamilies))
	if len(document.Members) > 0 {
		for _, table := range document.Members[0].Tables {
			tables = append(tables, map[string]string{"name": table.Name, "keeper_path": table.KeeperPath})
		}
	}
	raw, _ := json.Marshal(map[string]any{"format": "futureagi.catalog-keeper-live-membership", "version": 1, "members": members, "tables": tables})
	return sha256Hex(raw)
}

func admissionDigest(document WriteAdmission) string {
	raw, _ := json.Marshal(document)
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(raw, &fields)
	delete(fields, "topology_sha256")
	raw, _ = json.Marshal(fields)
	return sha256Hex(raw)
}

func (p *WritePolicy) RequireDestination(environment, database, endpoint string) error {
	if p == nil || p.admission.Environment != environment || p.admission.Database != database {
		return errors.New("propertycatalog: write destination is not admitted")
	}
	for _, member := range p.admission.Members {
		if member.URL == endpoint {
			return nil
		}
	}
	return errors.New("propertycatalog: writer must target a directly admitted member")
}

// DirectWriteEndpoint routes a configured service ingress to a directly
// inspected member without pretending that one service hostname response
// attests membership. Admission has already resolved/probed the infrastructure's
// HTTP routes. The selected endpoint is subsequently frozen in each attempt;
// recovery never changes it or retries against another replica.
func (p *WritePolicy) DirectWriteEndpoint(environment, database string) (string, error) {
	if p == nil || p.admission.Environment != environment || p.admission.Database != database {
		return "", errors.New("propertycatalog: configured installation is not admitted")
	}
	return p.admission.Members[0].URL, nil
}

func (p *WritePolicy) InsertSettings(ctx context.Context) (map[string]string, error) {
	if p == nil || ctx == nil {
		return nil, errors.New("propertycatalog: INSERT requires admitted topology and a bounded context")
	}
	deadline, bounded := ctx.Deadline()
	remaining := time.Until(deadline)
	if !bounded || remaining < 100*time.Millisecond || ctx.Err() != nil {
		return nil, errors.New("propertycatalog: insufficient bounded INSERT/proof budget")
	}
	// Half the remaining wall is reserved for the completion barrier. Quorum
	// timeout remains ambiguous, even if every row can subsequently be SELECTed.
	quorum := 0
	if p.admission.Family == "replicated" {
		quorum = len(p.admission.Members)
	}
	return map[string]string{
		"async_insert": "0", "wait_end_of_query": "1", "insert_deduplicate": "1",
		"insert_quorum": strconv.Itoa(quorum), "insert_quorum_parallel": "1",
		"insert_quorum_timeout": strconv.FormatInt((remaining / 2).Milliseconds(), 10),
	}, nil
}

func safeProofName(value string) bool {
	if value == "" || len(value) > 255 {
		return false
	}
	for _, c := range value {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-' || c == '.') {
			return false
		}
	}
	return true
}

func bareClickHouseOrigin(value string) (*url.URL, error) {
	u, err := url.Parse(value)
	if err != nil || u == nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" ||
		u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("propertycatalog: ClickHouse endpoint must be a bare http(s) origin")
	}
	return u, nil
}

func sha256Hex(raw []byte) string { return fmt.Sprintf("%x", sha256.Sum256(raw)) }

func decodeCanonicalLine(raw []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := requireJSONEOF(decoder); err != nil {
		return err
	}
	canonical, err := json.Marshal(target)
	if err != nil || !bytes.Equal(append(canonical, '\n'), raw) {
		return errors.New("propertycatalog: noncanonical durable JSON")
	}
	return nil
}
