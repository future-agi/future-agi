package propertycatalog

import (
	"context"
	"errors"
	"reflect"
	"sort"
	"strconv"
)

const keeperSessionQuery = `SELECT hostName() AS hostname,toString(serverUUID()) AS server_uuid,
 name,client_id,is_expired FROM system.zookeeper_connection WHERE name='default'
 LIMIT 2 FORMAT JSONEachRow`

type keeperSession struct {
	Hostname   string `json:"hostname"`
	ServerUUID string `json:"server_uuid"`
	Name       string `json:"name"`
	ClientID   int64  `json:"client_id"`
	Expired    uint8  `json:"is_expired"`
}

type keeperActive struct {
	Path       string `json:"path"`
	Name       string `json:"name"`
	Value      string `json:"value"`
	Owner      int64  `json:"ephemeral_owner"`
	CreateZXID int64  `json:"czxid"`
	Version    int32  `json:"version"`
}

func (p *HTTPWriteProof) keeperSessions(ctx context.Context, policy *WritePolicy) ([]keeperSession, error) {
	sessions := make([]keeperSession, 0, len(policy.admission.Members))
	owners := map[int64]bool{}
	for _, member := range policy.admission.Members {
		rows, err := p.query(ctx, member.URL, keeperSessionQuery, nil, 4096)
		if err != nil {
			return nil, err
		}
		if len(rows) != 1 {
			return nil, errors.New("propertycatalog: no unique live default Keeper session")
		}
		var session keeperSession
		if err := decodeProofRow(rows[0], &session); err != nil {
			return nil, err
		}
		if session.Name != "default" || session.Hostname != member.Hostname || session.ServerUUID != member.ServerUUID ||
			session.Expired != 0 || session.ClientID <= 0 || owners[session.ClientID] {
			return nil, errors.New("propertycatalog: aliased/expired/foreign Keeper session")
		}
		owners[session.ClientID] = true
		sessions = append(sessions, session)
	}
	return sessions, nil
}

// attestKeeper binds every registered replica to the actual admitted server and
// its current session, as viewed through EVERY direct member. Matching Keeper
// paths, configured addresses or a descriptor digest by themselves are not proof.
// CH25.3 creates is_active as an ephemeral node with Field(serverUUID()).dump().
// Source: ReplicatedMergeTreeRestartingThread.cpp and FieldVisitorDump.cpp.
// Session probes bracket two identical complete observations to fail closed on
// reconnects, foreign trees, disappearance and replacement of active nodes.
func (p *HTTPWriteProof) attestKeeper(ctx context.Context, policy *WritePolicy) error {
	before, err := p.keeperSessions(ctx, policy)
	if err != nil {
		return err
	}
	var first []keeperActive
	for pass := 0; pass < 2; pass++ {
		for _, observer := range policy.admission.Members {
			observed := make([]keeperActive, 0, len(policy.admission.Members)*len(observer.Tables))
			paths := make([]any, 0, len(policy.admission.Members)*len(observer.Tables))
			expected := map[string]int{}
			for _, table := range observer.Tables {
				for index, member := range policy.admission.Members {
					path := table.KeeperPath + "/replicas/" + member.Name
					if _, exists := expected[path]; exists {
						return errors.New("propertycatalog: catalog tables alias one Keeper path")
					}
					expected[path] = index
					paths = append(paths, path)
				}
			}
			encodedPaths, err := proofLiteral(paths)
			if err != nil {
				return err
			}
			rows, err := p.query(ctx, observer.URL, `SELECT path,name,value,ephemeralOwner AS ephemeral_owner,czxid,version
 FROM system.zookeeper WHERE path IN {replica_paths:Array(String)} AND name='is_active'
 ORDER BY path,name LIMIT `+strconv.Itoa(len(paths)+1)+` FORMAT JSONEachRow`, map[string]string{"param_replica_paths": encodedPaths}, 128<<10)
			if err != nil {
				return err
			}
			if len(rows) != len(paths) {
				return errors.New("propertycatalog: admitted replicas lack complete Keeper active witnesses")
			}
			seen := map[string]bool{}
			for _, row := range rows {
				var active keeperActive
				if err := decodeProofRow(row, &active); err != nil {
					return err
				}
				index, ok := expected[active.Path]
				if !ok || seen[active.Path] || active.Name != "is_active" || active.Value != "UUID_'"+policy.admission.Members[index].ServerUUID+"'" ||
					active.Owner != before[index].ClientID || active.CreateZXID <= 0 || active.Version != 0 {
					return errors.New("propertycatalog: Keeper tree does not bind the actual serving server/session")
				}
				seen[active.Path] = true
				observed = append(observed, active)
			}
			sort.Slice(observed, func(i, j int) bool { return observed[i].Path < observed[j].Path })
			if first == nil {
				first = observed
			} else if !reflect.DeepEqual(first, observed) {
				return errors.New("propertycatalog: Keeper active witnesses differ across serving members/observations")
			}
		}
	}
	after, err := p.keeperSessions(ctx, policy)
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(before, after) {
		return errors.New("propertycatalog: Keeper session changed during identity proof")
	}
	return nil
}
