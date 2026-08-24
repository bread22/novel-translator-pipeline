import { describe, expect, it } from 'vitest';
import { providerRoleReferences } from './settingsUtils';

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
});
