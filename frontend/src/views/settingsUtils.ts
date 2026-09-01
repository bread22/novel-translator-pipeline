import { SystemConfig } from '../types/api';

export const providerRoleReferences = (config: SystemConfig, providerId: string): string[] => [
  ...Object.entries(config.roles || {})
    .filter(([, value]) => value === providerId || (Array.isArray(value) && value.includes(providerId)))
    .map(([role]) => role),
  ...(config.knowledge_extractor?.provider === providerId ? ['knowledge_extractor.provider'] : []),
  ...(config.knowledge_extractor?.fallback_providers?.includes(providerId)
    ? ['knowledge_extractor.fallback_providers']
    : []),
];

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
  knowledge_extractor: config.knowledge_extractor ? {
    ...config.knowledge_extractor,
    provider: config.knowledge_extractor.provider === providerId
      ? replacementId
      : config.knowledge_extractor.provider,
    fallback_providers: config.knowledge_extractor.fallback_providers
      ? [...new Set(config.knowledge_extractor.fallback_providers.map((provider) => provider === providerId ? replacementId : provider))]
      : config.knowledge_extractor.fallback_providers,
  } : config.knowledge_extractor,
});
