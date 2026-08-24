import { SystemConfig } from '../types/api';

export const providerRoleReferences = (config: SystemConfig, providerId: string): string[] =>
  Object.entries(config.roles || {})
    .filter(([, value]) => value === providerId || (Array.isArray(value) && value.includes(providerId)))
    .map(([role]) => role);

export const migrateProviderRoleReferences = (
  config: SystemConfig,
  providerId: string,
  replacementId: string,
): SystemConfig => ({
  ...config,
  roles: Object.fromEntries(Object.entries(config.roles || {}).map(([role, value]) => [
    role,
    Array.isArray(value)
      ? [...new Set(value.map((provider) => provider === providerId ? replacementId : provider))]
      : value === providerId ? replacementId : value,
  ])) as SystemConfig['roles'],
});
