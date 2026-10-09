/** Дизайн-система «Ink & Porcelain» (макет в Figma) — палитра ПК (src/ui/theme.py), чтобы приложения выглядели одной системой. */
export const colors = {
  bg: '#F3F4F8',
  surface: '#FFFFFF',
  surfaceAlt: '#F7F8FB',
  border: '#E6E8F0',
  borderStrong: '#CED3DF',
  // Тёмные поверхности (карточка импорта, активный чип)
  ink: '#0B1020',
  inkRaised: '#171E38',
  inkText: '#8C94AE',
  text: '#0B1020',
  textSecondary: '#5A6379',
  textMuted: '#9AA1B4',
  primary: '#2F5BFF',
  primaryPressed: '#2449E0',
  primarySoft: '#EDF1FF',
  primaryText: '#1F3BB8',
  onPrimary: '#FFFFFF',
  success: '#0F9F6E',
  successSoft: '#E5F6EE',
  successText: '#0A6A49',
  warning: '#D68A00',
  warningSoft: '#FFF4DA',
  warningText: '#8A5300',
  danger: '#E5484D',
  dangerSoft: '#FDECEC',
  dangerText: '#A5242A',
} as const;

export const radius = { sm: 8, md: 12, lg: 16, xl: 20, pill: 999 } as const;
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 } as const;

/**
 * Onest — XML-семейство Android (плагин expo-font в app.json): начертание
 * выбирается fontWeight 400/500/600/700. Нужен prebuild — в Expo Go шрифта нет.
 */
export const fontFamily = 'Onest';

/** Текстовые стили макета; letterSpacing — проценты Figma в пикселях. */
export const type = {
  title: { fontSize: 22, lineHeight: 28, fontWeight: '600', letterSpacing: -0.2 },
  h2: { fontSize: 17, lineHeight: 24, fontWeight: '600', letterSpacing: -0.1 },
  number: { fontSize: 28, lineHeight: 32, fontWeight: '600', letterSpacing: -0.4 },
  body: { fontSize: 14, lineHeight: 20, fontWeight: '400' },
  bodyStrong: { fontSize: 14, lineHeight: 20, fontWeight: '500' },
  button: { fontSize: 14, lineHeight: 20, fontWeight: '600' },
  caption: { fontSize: 12, lineHeight: 16, fontWeight: '400' },
  captionStrong: { fontSize: 12, lineHeight: 16, fontWeight: '500' },
} as const;

/** Единственная тень системы (Elevation/Card). */
export const shadow = { boxShadow: '0px 4px 16px 0px rgba(11, 16, 32, 0.06)' } as const;
