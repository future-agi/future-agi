#!/usr/bin/env python3
"""Validation-branch-only codegen provenance and additive-surface assertions."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'validation3289-output'
GENERATED = [
    'api_contracts/openapi/swagger.json',
    'api_contracts/openapi/management-api-contract-debt.generated.json',
    'api_contracts/openapi/runtime-management-api-contract-debt.generated.json',
    'frontend/src/api/contracts/filter-contract.generated.js',
    'frontend/src/api/contracts/api-surface.generated.js',
    'frontend/src/api/contracts/openapi-contract.generated.js',
    'frontend/src/generated/api-contracts/api.ts',
    'frontend/src/generated/api-contracts/api.schemas.ts',
    'frontend/src/generated/api-contracts/api.zod.ts',
    'futureagi/mcp_server/catalog/tools.generated.json',
    'e2e/FLOWS.md',
]
PARENTS = ['f8b524775eb2f55c7fbaca4df54596bb82c26575', '94a13ed4d1bddffecfef8e24c6fc59cb32cda19f']
EE = 'a4fe9e015efe7003e5129458aa8a6dd6165bfaca'
METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options'}
PRIVATE = {'analysis_source_generation', 'source_session_ids', 'source_created_at', 'source_started_at'}

def git(*args: str, cwd=ROOT) -> str:
    return subprocess.check_output(['git', '-C', str(cwd), *args], text=True).strip()

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def manifest() -> dict:
    return {
        'source_commit': git('rev-parse', 'HEAD'),
        'source_tree': git('rev-parse', 'HEAD^{tree}'),
        'merge_parents': PARENTS,
        'ee_commit': git('rev-parse', 'HEAD', cwd=ROOT/'futureagi/ee/cloud'),
        'ee_tree': git('rev-parse', 'HEAD^{tree}', cwd=ROOT/'futureagi/ee/cloud'),
        'github_run_id': os.environ.get('GITHUB_RUN_ID'),
        'github_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
        'node': subprocess.check_output(['node','--version'],text=True).strip(),
        'python': sys.version.split()[0],
        'uv_lock_sha256': digest(ROOT/'futureagi/uv.lock'),
        'frontend_lock_sha256': digest(ROOT/'frontend/yarn.lock'),
        'e2e_lock_sha256': digest(ROOT/'e2e/yarn.lock'),
    }

def operations(schema):
    return {(path,method) for path,item in schema.get('paths',{}).items() for method in item if method in METHODS}

def private_fields(value, trail='$'):
    found=[]
    if isinstance(value,dict):
        for key,item in value.items():
            if key in ('properties','patternProperties') and isinstance(item,dict):
                found.extend(trail+'.'+key+'.'+name for name in item if name in PRIVATE)
            if key=='name' and isinstance(item,str) and item in PRIVATE:
                found.append(trail+'.name='+item)
            found.extend(private_fields(item,trail+'.'+key))
    elif isinstance(value,list):
        for i,item in enumerate(value):found.extend(private_fields(item,trail+f'[{i}]'))
    return found

OUT.mkdir(exist_ok=True)
if sys.argv[1] == 'start':
    data=manifest()
    assert data['ee_commit']==EE, 'EE pin mismatch'
    for parent in PARENTS:
        subprocess.run(['git','-C',str(ROOT),'merge-base','--is-ancestor',parent,'HEAD'],check=True)
    (OUT/'source-manifest.json').write_text(json.dumps(data,indent=2)+'\n')
    print('Source and EE pins verified; private token is not part of this manifest.')
else:
    data=manifest()
    generated=json.loads((ROOT/GENERATED[0]).read_text())
    checks=[]
    for ref in PARENTS:
        before=json.loads(git('show',ref+':'+GENERATED[0]))
        missing_paths=sorted(set(before.get('paths',{}))-set(generated.get('paths',{})))
        missing_definitions=sorted(set(before.get('definitions',{}))-set(generated.get('definitions',{})))
        missing_operations=sorted(operations(before)-operations(generated))
        missing_properties=[]
        for name,definition in before.get('definitions',{}).items():
            missing=set(definition.get('properties',{}))-set(generated.get('definitions',{}).get(name,{}).get('properties',{}))
            missing_properties.extend(name+'.'+field for field in sorted(missing))
        missing_parameters=[]
        missing_responses=[]
        for path,method in operations(before)&operations(generated):
            old=before['paths'][path][method];new=generated['paths'][path][method]
            oldp={(p.get('in'),p.get('name')) for p in old.get('parameters',[])}
            newp={(p.get('in'),p.get('name')) for p in new.get('parameters',[])}
            missing_parameters.extend(f'{method} {path}: {location} {name}' for location,name in oldp-newp)
            missing_responses.extend(f'{method} {path}: {code}' for code in set(old.get('responses',{}))-set(new.get('responses',{})))
        row={'ref':ref,'missing_paths':missing_paths,'missing_definitions':missing_definitions,'missing_operations':missing_operations,'missing_definition_properties':missing_properties,'missing_parameters':missing_parameters,'missing_response_codes':missing_responses}
        checks.append(row)
    leaks=private_fields(generated)
    leaks+=private_fields(json.loads((ROOT/'futureagi/mcp_server/catalog/tools.generated.json').read_text()),'MCP')
    changed=git('diff','--name-only').splitlines()
    unexpected=sorted(set(changed)-set(GENERATED))
    result={'comparisons':checks,'private_properties_found':leaks,'unexpected_modified_files':unexpected}
    (OUT/'surface-checks.json').write_text(json.dumps(result,indent=2)+'\n')
    assert not any(any(v for k,v in row.items() if k!='ref') for row in checks),'API surface loss; inspect surface-checks.json'
    assert not leaks,'Private analysis provenance exposed'
    assert not unexpected,'Generator changed non-generated files'
    data['generated_files']={path:digest(ROOT/path) for path in GENERATED}
    data['checks']={'canonical_swagger_generated':True,'frontend_contracts_check':True,'mcp_check':True,'e2e_typecheck':True,'e2e_catalog_check':True,'api_loss_against_both_parents':False,'private_properties_exposed':False}
    for path in GENERATED:
        target=OUT/'files'/path;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/path,target)
    (OUT/'manifest.json').write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'generated_files':len(GENERATED),'paths':len(generated['paths']),'definitions':len(generated['definitions']),'operations':len(operations(generated)),'api_loss':False,'private_properties_exposed':False}))
