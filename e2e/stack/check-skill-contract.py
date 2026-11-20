"""Verify the checked-out backend against the pinned SDK's reviewed contract source."""

import hashlib
import json
import sys
from pathlib import Path


SKILLS = '/model-hub/skills/'


def projection(api, catalog):
    paths = {path: item for path, item in api['paths'].items() if path.startswith(SKILLS)}
    definitions = {}

    def visit(value):
        if isinstance(value, dict):
            if '$ref' in value:
                prefix = '#/definitions/'
                ref = value['$ref']
                if not ref.startswith(prefix):
                    raise ValueError('Unsupported reference in Skills contract')
                name = ref[len(prefix):]
                if name not in definitions:
                    definitions[name] = api['definitions'][name]
                    visit(definitions[name])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(paths)
    return {
        'globals': {k: v for k, v in api.items() if k not in ('info', 'paths', 'definitions', 'tags')},
        'paths': paths, 'definitions': definitions,
        'tools': sorted((t for t in catalog['tools'] if t['request']['path'].startswith(SKILLS)), key=lambda t: t['name']),
        'tool_names': sorted(t['name'] for t in catalog['tools']),
    }


def fingerprint(selected):
    return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check(root, sdk):
    lock = json.loads((root / 'e2e/stack/skill-pair.lock.json').read_text())['contract']
    contracts = sdk / 'sdkgen/contracts'
    source = json.loads((contracts / 'SOURCE.json').read_text())
    if source != lock['source']:
        raise ValueError('SDK contract provenance differs from the reviewed source')
    swagger = json.loads((contracts / 'swagger.json').read_text())
    tools = json.loads((contracts / 'mcp-tools.json').read_text())
    raw = (root / 'api_contracts/openapi/swagger.json').read_bytes()
    catalog = json.loads((root / 'futureagi/mcp_server/catalog/tools.generated.json').read_text())
    if (swagger['x-source-sha256'] != source['openapi_sha256'] or tools['x-source-sha256'] != source['mcp_sha256']
            or swagger['x-skills-only-baseline-sha256'] != source['non_skills_slice_sha256']
            or catalog['source_contract_sha256'] != hashlib.sha256(raw).hexdigest()):
        raise ValueError('Contract snapshot source mismatch')
    selected = projection(json.loads(raw), catalog)
    if fingerprint(selected) != lock['skills_projection_sha256']:
        raise ValueError('Backend Skills contract changed; refresh the SDK and reviewed projection together')
    # Check what the SDK actually ships, not only its source metadata. Non-Skills
    # routes come from the SDK's separate released baseline, not this sync.
    shipped = projection(swagger, {'tools': []})
    for path, methods in shipped['paths'].items():
        if any(selected['paths'][path][method] != operation for method, operation in methods.items()):
            raise ValueError('SDK operation differs from the tested backend')
    if not shipped['paths'] or any(selected['definitions'][k] != v for k, v in shipped['definitions'].items()):
        raise ValueError('SDK Skills models differ from the tested backend')
    if tools['tools'] != [{'name': name} for name in selected['tool_names']]:
        raise ValueError('SDK tool inventory does not match the tested catalog')


if __name__ == '__main__':
    if sys.argv[1:2] == ['--source-fingerprint']:
        # Refresh only from the original backend input files authenticated by the new SDK pin.
        sdk, root = map(Path, sys.argv[2:])
        source = json.loads((sdk / 'sdkgen/contracts/SOURCE.json').read_text())
        api = (root / 'api_contracts/openapi/swagger.json').read_bytes()
        catalog = (root / 'futureagi/mcp_server/catalog/tools.generated.json').read_bytes()
        if hashlib.sha256(api).hexdigest() != source['openapi_sha256'] or hashlib.sha256(catalog).hexdigest() != source['mcp_sha256']:
            raise ValueError('Use the original SDK contract source, not a later checkout')
        print(fingerprint(projection(json.loads(api), json.loads(catalog))))
    else:
        check(Path(sys.argv[1]), Path(sys.argv[2]))
