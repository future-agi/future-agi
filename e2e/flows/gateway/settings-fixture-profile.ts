// W12 Settings journey fixtures only. Never widen the product profile API.
import { walkGatewayProfile, type WalkGatewayProfile } from '../../lib/profile';

export type SettingsFixtureProfile = Extract<WalkGatewayProfile,
  'stock' | 'r7alt' | 'mcp' | 'mcp-catalog' | 'mcp-signin'>;

export function settingsFixtureProfile(value?: string): SettingsFixtureProfile {
  const profile = walkGatewayProfile(value);
  switch (profile) {
    case 'stock':
    case 'r7alt':
    case 'mcp':
    case 'mcp-catalog':
    case 'mcp-signin':
      return profile;
    default:
      throw new Error(`Settings fixture profile ${profile} is outside this five-fixture journey`);
  }
}
