import { randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveBackendContainer } from './simulate-seed';

export interface KnowledgeSourceDocument {
  filename: string;
  mimeType: string;
  buffer: Buffer;
}

export interface KnowledgeSourceSeed {
  kbId: string;
  fileIds: string[];
}

const SEED_SCRIPT = String.raw`
import base64
import io
import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    payload = json.load(open(os.environ["SEED_PAYLOAD"], encoding="utf-8"))
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name=payload["name"],
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
    )
    client = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    file_ids = []
    for document in payload["documents"]:
        content = base64.b64decode(document["content"])
        file = Files.no_workspace_objects.create(
            name=document["filename"],
            uploaded_url="e2e://captured",
            status=StatusType.COMPLETED.value,
            metadata=json.dumps({"size": len(content)}),
            updated_by="T20 E2E",
        )
        kb.files.add(file)
        suffix = document["filename"].rsplit(".", 1)[-1].lower()
        client.put_object(
            bucket_name=UPLOAD_BUCKET_NAME,
            object_name=f"knowledge-base/{kb.id}/{file.id}.{suffix}",
            data=io.BytesIO(content),
            length=len(content),
            content_type=document["mimeType"],
        )
        file_ids.append(str(file.id))
    print(json.dumps({"kbId": str(kb.id), "fileIds": file_ids}))


main()
`;

export function seedKnowledgeBaseForSources(opts: {
  organizationId: string;
  workspaceId: string;
  name: string;
  documents: KnowledgeSourceDocument[];
}): KnowledgeSourceSeed {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
  const dir = mkdtempSync(join(tmpdir(), 'e2e-t20-kb-'));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  const payloadPath = join(dir, `payload-${suffix}.json`);
  writeFileSync(scriptPath, SEED_SCRIPT);
  writeFileSync(payloadPath, JSON.stringify({
    name: opts.name,
    documents: opts.documents.map(document => ({
      filename: document.filename,
      mimeType: document.mimeType,
      content: document.buffer.toString('base64'),
    })),
  }));
  try {
    execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/seed-${suffix}.py`]);
    execFileSync('docker', ['cp', payloadPath, `${container}:/tmp/payload-${suffix}.json`]);
    const stdout = execFileSync('docker', [
      'exec',
      '-e', `SEED_ORG_ID=${opts.organizationId}`,
      '-e', `SEED_WORKSPACE_ID=${opts.workspaceId}`,
      '-e', `SEED_PAYLOAD=/tmp/payload-${suffix}.json`,
      container,
      'sh',
      '-c',
      `cd /app/backend && PYTHONPATH=/app/backend python /tmp/seed-${suffix}.py`,
    ], { encoding: 'utf-8' });
    return JSON.parse(stdout.trim().split('\n').pop() ?? '') as KnowledgeSourceSeed;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
