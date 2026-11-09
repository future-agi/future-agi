Test fixtures for omega_hub.

`entries/{first-agent,notes-plugin,engineering-team}` are copies of three Official Omega Hub entries,
taken with `git archive 881b05ba5` from the Omega repository (Apache-2.0), with `version` and a
hand-written `disclosure` added as the hub-cli lane's C1 will add them. The tests pack them into
canonical bundles (`services/bundles.pack_canonical`) and post check reports for them to the
internal checker endpoints themselves (SPEC §14.1 B1).
