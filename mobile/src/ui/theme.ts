/** Цвета — палитра ПК (src/ui/theme.py), чтобы приложения выглядели одной системой. */
export const colors = {
  bg: '#F6F7FB',
  surface: '#FFFFFF',
  surfaceAlt: '#F8FAFC',
  border: '#E4E7EE',
  borderStrong: '#CBD5E1',
  text: '#0F172A',
  textSecondary: '#64748B',
  textMuted: '#94A3B8',
  primary: '#3B82F6',
  primaryPressed: '#2563EB',
  primarySoft: '#DBEAFE',
  primaryText: '#1E40AF',
  success: '#10B981',
  successSoft: '#D1FAE5',
  successText: '#065F46',
  warningSoft: '#FEF3C7',
  warningText: '#92400E',
  danger: '#DC2626',
  dangerSoft: '#FEE2E2',
  dangerText: '#991B1B',
} as const;

export const radius = { sm: 8, md: 12, lg: 16 } as const;
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 } as const;
