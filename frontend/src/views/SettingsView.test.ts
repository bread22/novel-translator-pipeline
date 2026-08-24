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
});
