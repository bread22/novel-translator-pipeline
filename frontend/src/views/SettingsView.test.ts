import { describe, expect, it } from 'vitest';
import { migrateProviderRoleReferences, providerRoleReferences } from './settingsUtils';

describe('provider deletion guard', () => {
  it('reports scalar and list role references that must be migrated', () => {
    expect(providerRoleReferences({
      roles: {
        primary_translator: 'alpha',
        reviewer: 'beta',
        fallback_translators: ['beta', 'alpha'],
      },
    }, 'alpha')).toEqual(['primary_translator', 'fallback_translators']);
  });

  it('migrates scalar and list role references without duplicate fallbacks', () => {
    const migrated = migrateProviderRoleReferences({
      roles: {
        primary_translator: 'alpha',
        reviewer: 'beta',
        fallback_translators: ['alpha', 'beta'],
      },
    }, 'alpha', 'beta');
    expect(migrated.roles).toEqual({
      primary_translator: 'beta',
      reviewer: 'beta',
      fallback_translators: ['beta'],
    });
  });

  it('guards and migrates knowledge extractor fallback references', () => {
    const config = {
      roles: {},
      knowledge_extractor: {
        provider: 'alpha',
        fallback_providers: ['alpha', 'beta'],
      },
    };
    expect(providerRoleReferences(config, 'alpha')).toEqual([
      'knowledge_extractor.provider',
      'knowledge_extractor.fallback_providers',
    ]);
    expect(migrateProviderRoleReferences(config, 'alpha', 'beta').knowledge_extractor).toEqual({
      provider: 'beta',
      fallback_providers: ['beta'],
    });
  });
});
