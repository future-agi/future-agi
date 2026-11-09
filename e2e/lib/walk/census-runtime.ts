// Runtime adapter kept separate from the pure digest/reader self-tests.
import { readFileSync } from 'node:fs';
import { inspectManagedMock } from '../managed-mock';
import { gatewayProfile, newOrgDefaultDeclared } from '../profile';
import { sourceState } from './receipt';
import { censusDigest, type CensusBinding, type CensusBrowser } from './census-result';

/** Independent current-stack binding, not values copied out of the result.
 * E2E_WALK_IMAGES is the existing walk-images.mjs running attestation. Check its
 * head and content-addressed IDs against a fresh live inspection on EVERY read. */
export function currentCensusBinding(browser: CensusBrowser): CensusBinding {
  const runId = process.env.E2E_CENSUS_RUN_ID;
  if (!runId) throw new Error('W00_RESULT_RUN_REQUIRED');
  const source = sourceState();
  if (!source.realHead || source.dirty !== 0 || source.productIdentical !== true) throw new Error('W00_RESULT_SOURCE_UNATTESTED');
  if (!process.env.E2E_WALK_IMAGES) throw new Error('W00_RESULT_IMAGES_REQUIRED');
  const images = JSON.parse(readFileSync(process.env.E2E_WALK_IMAGES, 'utf8'));
  const inspection = inspectManagedMock();
  if (inspection.profile !== gatewayProfile() || images.head !== source.realHead || !Array.isArray(images.images) ||
      !images.images.length || images.images.some((image: any) =>
        !/^sha256:[a-f0-9]{64}$/.test(image.imageId) ||
        !inspection.services.some(s => s.service === image.service && s.image === image.imageId))) {
    throw new Error('W00_RESULT_IMAGES_MISMATCH');
  }
  return { run: censusDigest(runId), profile: gatewayProfile(), browser, newOrgDefault: newOrgDefaultDeclared(),
    stack: censusDigest({ source, images, mountedConfig: `sha256:${inspection.gatewaySha}`,
      mock: inspection.mockSha, inspectedSources: inspection.sourceHashes,
      instance: { context: inspection.context, project: inspection.project, network: inspection.networkId,
        publicNetwork: inspection.publicNetworkId },
      running: inspection.services.map(s => ({ service: s.service, imageId: s.image, instance: s.id, startedAt: s.startedAt }))
        .sort((a, b) => a.service.localeCompare(b.service)) }) };
}
