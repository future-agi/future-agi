Local Skills inventory
======================

Run ``bin/skills-inventory`` before issuing runtime credentials or uploading a
private collection. It reads files only: no network, execution, publication,
source changes, or automatic subset selection. Use Python 3.12+ with the product
Python dependencies (PyYAML); ``SKILLS_INVENTORY_PYTHON`` selects that interpreter.
The command uses isolated Python imports, never modules from an input folder.

Example::

    SKILLS_INVENTORY_PYTHON=/path/to/venv/bin/python bin/skills-inventory \
      --folder /private/library \
      --langfuse /private/versioned-exchange.json \
      --agents /private/pilot/AGENTS.md \
      --name isolated-pilot --description 'Isolated policy trial' \
      --source-origin local:my-library --source-project policies \
      --source-revision source-commit-or-immutable-revision > inventory.json

The exchange is the explicitly supported ``langfuse-skills-v1`` schema, not a
claim to accept every native Langfuse export. Its digests and immutable source
identities are checked locally. Source identities are client-reported, not proof
of rights. Local folders are inventoried in full, including rejected binaries,
symlinks, nested roots, host metadata and unassigned paths.

The JSON report lists every member, blocker, file hash and byte size. Review
``members``, ``unowned_or_rejected`` and ``compiled_closure``. When some members
are blocked and no subset is selected, ``proposed_supported_subset`` also shows
the compiled closure of every unblocked member. It is a proposal for your review;
nothing is excluded until you leave the blocked members out explicitly in the
retained-migration review. To compile a different subset, repeat
``--select <member-key>`` for each explicitly reviewed member and rerun.
Even then the report retains the entire inventory and lists every excluded root.
A fitting subset never turns ``whole_collection_supported`` true for an excluded
collection. ``--require-fit`` returns exit 2 if the selected closure is blocked;
otherwise a successfully completed inventory returns 0, even when it reports
incompatibility. Unreadable, changing or over-limit inputs return 1 with
``complete_inventory: false``, never a successful truncated inventory.

Compilation reuses the production compiler: 20 links, 200 files and 2 MiB include
AGENTS.md, all mounted child files and the generated descriptor. Mounted path
limits are checked too. The descriptor uses same-length preview UUIDs and exact
version-1 pins for new publications. This is a first-publication size projection,
not a resolvable release or delivery receipt. Later versions and selector changes
must be compiled and reviewed against real accepted locks in the product.

Add ``--packet /private/migration-packet.json`` to also write one private
migration packet: exactly the inventoried bytes of every source, each folder's
immutable source identities, and the proposed context (``--name``,
``--description`` and the ``--agents`` file). In the retained migration dialog,
``Choose inventory packet`` previews every source in it into one reviewed set,
and after the set is published the proposed agent context is assembled in the
same review: you check the exact pinned versions and compiled candidate, then
accept and publish it. The packet file must not already exist; it is written with
owner-only permissions, and the report's ``migration_packet`` gives its SHA-256.
The server re-derives every identity and digest; the packet is not trusted.
The packet holds file bodies, so keep it as private as the library itself.

For a local folder upload, supply all three local source options. The report's
``folder_upload_source_identities`` object can be pasted into the retained
migration dialog's ``Immutable source identities JSON`` field when using
``Choose private folder``. It matches browser ``webkitRelativePath`` and the
public parser's loose-file keys. Do not use these keys for a renamed ZIP upload.
Keep folder basenames and resource paths stable across revisions; change the
source revision when bytes change. Frontmatter skill names do not determine
source identity. Moving source paths requires explicit retained-identity
reconciliation, not automatic matching by destination name.

Local reports contain private filenames and source identity metadata. Keep them
private. They contain no file bodies or metadata scalar values. Publication,
purpose-bound credentials, verified host discovery, source-v2 conflict handling,
export, managed removal and revocation remain separate deliberate actions.
