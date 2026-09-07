package propertycatalog

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
)

func TestKeeperProofRejectsSplitTreeMissingForeignAndReconnectingSessions(t *testing.T) {
	for _, fault := range []string{"split-tree", "missing-active", "foreign-owner", "foreign-uuid", "session-expired", "session-reconnect", "active-recreated", "duplicate-row", "auxiliary-keeper"} {
		t.Run(fault, func(t *testing.T) {
			proof, server := proofHTTPFixture(t, 2)
			original := proof.reader.client.Transport
			sessionReads, activeReads := 0, 0
			proof.reader.client.Transport = roundTripFunc(func(request *http.Request) (*http.Response, error) {
				body, _ := io.ReadAll(request.Body)
				request.Body = io.NopCloser(bytes.NewReader(body))
				response, err := original.RoundTrip(request)
				if err != nil {
					return response, err
				}
				raw, _ := io.ReadAll(response.Body)
				response.Body.Close()
				rows := []map[string]any{}
				for _, line := range bytes.Split(bytes.TrimSuffix(raw, []byte{'\n'}), []byte{'\n'}) {
					var row map[string]any
					if json.Unmarshal(line, &row) != nil {
						t.Fatal("fake response invalid")
					}
					rows = append(rows, row)
				}
				statement := string(body)
				if statement == keeperSessionQuery {
					sessionReads++
					if fault == "session-expired" {
						rows[0]["is_expired"] = 1
					}
					if fault == "session-reconnect" && sessionReads > 2 {
						rows[0]["client_id"] = 900
					}
				}
				if strings.Contains(statement, "FROM system.zookeeper ") {
					activeReads++
					switch fault {
					case "missing-active":
						rows = rows[:len(rows)-1]
					case "foreign-owner":
						rows[0]["ephemeral_owner"] = 900
					case "foreign-uuid":
						rows[0]["value"] = "UUID_'99999999-9999-4999-8999-999999999999'"
					case "split-tree":
						if request.URL.Hostname() == server.doc.Members[1].Hostname {
							rows = rows[:7]
						}
					case "active-recreated":
						if activeReads > 2 {
							rows[0]["czxid"] = 1001
						}
					case "duplicate-row":
						rows[1] = rows[0]
					}
				}
				if statement == writeTopologyQuery && fault == "auxiliary-keeper" {
					rows[0]["keeper_name"] = "other"
				}
				output := make([]any, len(rows))
				for i, row := range rows {
					output[i] = row
				}
				return proofJSONResponse(t, output...), nil
			})
			if err := proof.Attest(boundedWriteTestContext(t), proof.policy); err == nil {
				t.Fatalf("%s admitted", fault)
			}
			if server.inserts != 0 {
				t.Fatal("attestation wrote data")
			}
		})
	}
}

func TestKeeperMembershipDigestHasRestartStablePythonCanonicalShape(t *testing.T) {
	doc := testWriteAdmission(t, 2, false)
	members := []map[string]string{}
	for _, m := range doc.Members {
		members = append(members, map[string]string{"name": m.Name, "server_uuid": m.ServerUUID})
	}
	tables := []map[string]string{}
	for _, table := range doc.Members[0].Tables {
		tables = append(tables, map[string]string{"keeper_path": table.KeeperPath, "name": table.Name})
	}
	raw, _ := json.Marshal(map[string]any{"format": "futureagi.catalog-keeper-live-membership", "members": members, "tables": tables, "version": 1})
	if keeperMembershipDigest(doc) != sha256Hex(raw) || doc.KeeperIdentitySHA256 != sha256Hex(raw) {
		t.Fatal("cross-language Keeper digest differs")
	}
	if bytes.Contains(raw, []byte("client_id")) {
		t.Fatal("session restart changes admission")
	}
	doc.KeeperIdentitySHA256 = testDigest("config-only hash")
	doc.TopologySHA256 = admissionDigest(doc)
	if _, err := NewWritePolicy(doc); err == nil {
		t.Fatal("config-only identity accepted")
	}
}

func TestKeeperMembershipDigestMatchesPythonUnicodeGolden(t *testing.T) {
	// Same fixture as test_property_catalog_keeper_membership.py, including
	// Unicode, HTML-sensitive bytes and JavaScript line/paragraph separators.
	doc := testWriteAdmission(t, 2, false)
	for i := range doc.Members {
		doc.Members[i].Name = fmt.Sprintf("replica%d", i+1)
		doc.Members[i].ServerUUID = fmt.Sprintf("00000000-0000-4000-8000-%012d", i+1)
		for j := range doc.Members[i].Tables {
			table := &doc.Members[i].Tables[j]
			table.KeeperPath = "/café<>&\u2028\u2029/1/" + table.Name
		}
	}
	if got := keeperMembershipDigest(doc); got != "697027b9425de9b699c39fa71dbcd2ba18112eb4fd95f543a48c47281ed7bf6e" {
		t.Fatalf("Python/Go Keeper digest mismatch: %s", got)
	}
}
