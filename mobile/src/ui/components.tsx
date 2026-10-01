/** Общие элементы экранов. */
import type { ReactNode } from 'react';
import {
  ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, type TextInputProps, View,
} from 'react-native';

import { colors, radius, space } from './theme';

export function Screen({ children }: { children: ReactNode }) {
  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.screenContent} keyboardShouldPersistTaps="handled">
      {children}
    </ScrollView>
  );
}

export function Card({ title, subtitle, children }: { title?: string; subtitle?: string; children?: ReactNode }) {
  return (
    <View style={styles.card}>
      {title ? <Text style={styles.cardTitle}>{title}</Text> : null}
      {subtitle ? <Text style={styles.muted}>{subtitle}</Text> : null}
      {children}
    </View>
  );
}

type ButtonKind = 'primary' | 'secondary' | 'danger';

export function Button({
  title, onPress, kind = 'secondary', disabled, busy,
}: { title: string; onPress: () => void; kind?: ButtonKind; disabled?: boolean; busy?: boolean }) {
  const off = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: off, busy }}
      disabled={off}
      onPress={onPress}
      style={({ pressed }) => [styles.button, styles[`button_${kind}`], pressed && styles[`pressed_${kind}`], off && styles.disabled]}
    >
      {busy ? <ActivityIndicator color={kind === 'secondary' ? colors.primary : '#fff'} />
        : <Text style={[styles.buttonText, styles[`buttonText_${kind}`]]}>{title}</Text>}
    </Pressable>
  );
}

export function StatTile({ label, value, tone }: { label: string; value: string | number; tone?: 'warning' | 'danger' }) {
  return (
    <View style={[styles.tile, tone === 'warning' && styles.tileWarning, tone === 'danger' && styles.tileDanger]}>
      <Text style={styles.tileValue}>{value}</Text>
      <Text style={styles.tileLabel}>{label}</Text>
    </View>
  );
}

export function Tiles({ children }: { children: ReactNode }) {
  return <View style={styles.tiles}>{children}</View>;
}

/** Выбор одного варианта из нескольких (фильтры). */
export function Segmented<T>({
  options, value, onChange,
}: { options: Array<{ label: string; value: T }>; value: T; onChange: (v: T) => void }) {
  return (
    <View style={styles.segmented}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <Pressable
            key={o.label}
            accessibilityRole="button"
            accessibilityState={{ selected: active }}
            onPress={() => onChange(o.value)}
            style={[styles.segment, active && styles.segmentActive]}
          >
            <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

export function Field({ label, hint, ...input }: { label: string; hint?: string } & TextInputProps) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput placeholderTextColor={colors.textMuted} style={styles.input} {...input} />
      {hint ? <Text style={styles.mutedSmall}>{hint}</Text> : null}
    </View>
  );
}

export function Badge({ text, tone }: { text: string; tone: 'warning' | 'danger' | 'success' | 'info' }) {
  return (
    <View style={[styles.badge, styles[`badge_${tone}`]]}>
      <Text style={[styles.badgeText, styles[`badgeText_${tone}`]]}>{text}</Text>
    </View>
  );
}

export function Empty({ text }: { text: string }) {
  return <Text style={styles.empty}>{text}</Text>;
}

export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  screenContent: { padding: space.lg, gap: space.lg, paddingBottom: space.xl * 2 },
  card: {
    backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border,
    padding: space.lg, gap: space.md,
  },
  cardTitle: { fontSize: 17, fontWeight: '600', color: colors.text },
  muted: { fontSize: 14, color: colors.textSecondary, lineHeight: 20 },
  mutedSmall: { fontSize: 12, color: colors.textMuted, lineHeight: 16 },
  text: { fontSize: 15, color: colors.text, lineHeight: 21 },
  button: {
    minHeight: 48, borderRadius: radius.md, paddingHorizontal: space.lg, alignItems: 'center', justifyContent: 'center',
  },
  button_primary: { backgroundColor: colors.primary },
  button_secondary: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.borderStrong },
  button_danger: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.danger },
  pressed_primary: { backgroundColor: colors.primaryPressed },
  pressed_secondary: { backgroundColor: colors.surfaceAlt },
  pressed_danger: { backgroundColor: colors.dangerSoft },
  disabled: { opacity: 0.5 },
  buttonText: { fontSize: 15, fontWeight: '600' },
  buttonText_primary: { color: '#fff' },
  buttonText_secondary: { color: colors.text },
  buttonText_danger: { color: colors.danger },
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  tile: {
    flexGrow: 1, flexBasis: '30%', backgroundColor: colors.surfaceAlt, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.border, padding: space.md,
  },
  tileWarning: { backgroundColor: colors.warningSoft, borderColor: colors.warningSoft },
  tileDanger: { backgroundColor: colors.dangerSoft, borderColor: colors.dangerSoft },
  tileValue: { fontSize: 22, fontWeight: '700', color: colors.text, fontVariant: ['tabular-nums'] },
  tileLabel: { fontSize: 12, color: colors.textSecondary, marginTop: 2 },
  segmented: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  segment: {
    paddingHorizontal: space.md, minHeight: 40, justifyContent: 'center', borderRadius: radius.sm,
    borderWidth: 1, borderColor: colors.borderStrong, backgroundColor: colors.surface,
  },
  segmentActive: { backgroundColor: colors.primarySoft, borderColor: colors.primary },
  segmentText: { fontSize: 14, color: colors.text },
  segmentTextActive: { color: colors.primaryText, fontWeight: '600' },
  field: { gap: space.xs },
  fieldLabel: { fontSize: 13, color: colors.textSecondary, fontWeight: '500' },
  input: {
    minHeight: 48, borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md,
    paddingHorizontal: space.md, fontSize: 16, color: colors.text, backgroundColor: colors.surface,
  },
  badge: { alignSelf: 'flex-start', borderRadius: 999, paddingHorizontal: space.sm, paddingVertical: 3 },
  badge_warning: { backgroundColor: colors.warningSoft },
  badge_danger: { backgroundColor: colors.dangerSoft },
  badge_success: { backgroundColor: colors.successSoft },
  badge_info: { backgroundColor: colors.primarySoft },
  badgeText: { fontSize: 12, fontWeight: '600' },
  badgeText_warning: { color: colors.warningText },
  badgeText_danger: { color: colors.dangerText },
  badgeText_success: { color: colors.successText },
  badgeText_info: { color: colors.primaryText },
  empty: { fontSize: 15, color: colors.textSecondary, textAlign: 'center', paddingVertical: space.xl },
  row: { flexDirection: 'row', gap: space.sm, alignItems: 'center' },
});
