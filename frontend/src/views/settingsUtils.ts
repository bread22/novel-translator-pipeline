import { SystemConfig } from '../types/api';

export const providerRoleReferences = (config: SystemConfig, providerId: string): string[] =>
  Object.entries(config.roles || {})
    .filter(([, value]) => value === providerId || (Array.isArray(value) && value.includes(providerId)))
    .map(([role]) => role);
