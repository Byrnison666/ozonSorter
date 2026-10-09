/** Общие элементы экранов. */
import Feather from '@expo/vector-icons/Feather';
import {
  Children, type ComponentProps, type ReactNode, useEffect, useRef,
} from 'react';
import {
  ActivityIndicator, Pressable, Text as RNText, TextInput as RNTextInput, type TextInputProps, type TextProps,
  ScrollView, StyleSheet, View,
} from 'react-native';

import { colors, fontFamily, radius, shadow, space, type } from './theme';

/** Иконки Feather — Lucide из макета вырос из Feather, рисунок тот же. */
export type IconName = ComponentProps<typeof Feather>['name'];
export { Feather as Icon };

/** Text с шрифтом приложения: в React Native нет общего шрифта по умолчанию. */
export function Text({ style, ...props }: TextProps) {
  return <RNText style={[styles.font, style]} {...props} />;
}

export function TextInput({ style, ...props }: TextInputProps) {
  return <RNTextInput style={[styles.font, style]} {...props} />;
}

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

const BUTTON_ICON_COLOR: Record<ButtonKind, string> = {
  primary: colors.onPrimary, secondary: colors.primary, danger: colors.danger,
};

export function Button({
  title, onPress, kind = 'secondary', disabled, busy, icon,
}: { title: string; onPress: () => void; kind?: ButtonKind; disabled?: boolean; busy?: boolean; icon?: IconName }) {
  const off = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: off, busy }}
      disabled={off}
      onPress={onPress}
      style={({ pressed }) => [styles.button, styles[`button_${kind}`], pressed && styles[`pressed_${kind}`], off && styles.disabled]}
    >
      {busy ? <ActivityIndicator color={kind === 'primary' ? colors.onPrimary : colors.primary} /> : (
        <>
          {icon ? <Feather name={icon} size={18} color={BUTTON_ICON_COLOR[kind]} /> : null}
          <Text style={[styles.buttonText, styles[`buttonText_${kind}`]]}>{title}</Text>
        </>
      )}
    </Pressable>
  );
}

type Tone = 'warning' | 'danger';

/** Плитка с числом. hero — главная цифра экрана (синяя, крупная). */
export function StatTile({
  label, value, tone, hero,
}: { label: string; value: string | number; tone?: Tone; hero?: boolean }) {
  return (
    <View style={[styles.tile, tone && styles[`tile_${tone}`], hero && styles.tileHero]}>
      <View style={styles.row6}>
        <View style={[styles.dot, tone && styles[`dot_${tone}`], hero && styles.dotHero]} />
        <Text style={[styles.tileLabel, tone && styles[`tileText_${tone}`], hero && styles.tileTextHero]} numberOfLines={1}>
          {label}
        </Text>
      </View>
      <Text style={[styles.tileValue, tone && styles[`tileText_${tone}`], hero && styles.tileValueHero]}>{value}</Text>
    </View>
  );
}

/** Сетка плиток в columns столбцов. */
export function Tiles({ children, columns = 2 }: { children: ReactNode; columns?: number }) {
  return (
    <View style={styles.tiles}>
      {Children.map(children, (child) => (
        <View style={{ flexGrow: 1, flexBasis: `${Math.floor(100 / columns) - 5}%` }}>{child}</View>
      ))}
    </View>
  );
}

/** Выбор одного варианта (фильтры): ряд чипов, прокручивается вбок до краёв экрана. */
export function Chips<T>({
  options, value, onChange,
}: { options: Array<{ label: string; value: T }>; value: T; onChange: (v: T) => void }) {
  const scroll = useRef<ScrollView>(null);
  const view = useRef({ offset: 0, width: 0 });
  const chips = useRef(new Map<string, { x: number; width: number }>());
  const activeLabel = options.find((o) => o.value === value)?.label;

  // Выбор задают и извне (плашка «Скоро возврат» → «От 10 дней»): докрутить, чтобы чип был виден целиком.
  function reveal() {
    const chip = activeLabel === undefined ? undefined : chips.current.get(activeLabel);
    const { offset, width } = view.current;
    if (!chip || !width) return;
    if (chip.x - space.lg < offset) scroll.current?.scrollTo({ x: chip.x - space.lg, animated: true });
    else if (chip.x + chip.width + space.lg > offset + width) {
      scroll.current?.scrollTo({ x: chip.x + chip.width + space.lg - width, animated: true });
    }
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reveal, [activeLabel]);

  return (
    <ScrollView
      ref={scroll}
      horizontal
      showsHorizontalScrollIndicator={false}
      style={styles.chipsScroll}
      contentContainerStyle={styles.chips}
      scrollEventThrottle={32}
      onScroll={(e) => { view.current.offset = e.nativeEvent.contentOffset.x; }}
      onLayout={(e) => { view.current.width = e.nativeEvent.layout.width; reveal(); }}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <Pressable
            key={o.label}
            accessibilityRole="button"
            accessibilityState={{ selected: active }}
            onPress={() => onChange(o.value)}
            onLayout={(e) => {
              const { x, width } = e.nativeEvent.layout;
              chips.current.set(o.label, { x, width });
              if (active) reveal();
            }}
            style={({ pressed }) => [styles.chip, pressed && styles.chipPressed, active && styles.chipActive]}
          >
            <Text style={[styles.chipText, active && styles.chipTextActive]}>{o.label}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
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

export function Badge({ text, tone, dot }: { text: string; tone: 'warning' | 'danger' | 'success' | 'info'; dot?: boolean }) {
  return (
    <View style={[styles.badge, styles[`badge_${tone}`]]}>
      {dot ? <View style={[styles.dot, styles[`badgeDot_${tone}`]]} /> : null}
      <Text style={[styles.badgeText, styles[`badgeText_${tone}`]]}>{text}</Text>
    </View>
  );
}

export function Empty({ text }: { text: string }) {
  return <Text style={styles.empty}>{text}</Text>;
}

export const styles = StyleSheet.create({
  font: { fontFamily },
  screen: { flex: 1, backgroundColor: colors.bg },
  screenContent: { paddingHorizontal: space.lg, paddingTop: 2, paddingBottom: space.xl, gap: 14 },
  card: {
    backgroundColor: colors.surface, borderRadius: radius.xl, borderWidth: 1, borderColor: colors.border,
    padding: 18, gap: space.md, ...shadow,
  },
  cardTitle: { ...type.h2, color: colors.text },
  muted: { ...type.body, color: colors.textSecondary },
  mutedSmall: { ...type.caption, color: colors.textMuted },
  text: { ...type.body, color: colors.text },
  button: {
    minHeight: 48, borderRadius: radius.md, paddingHorizontal: 20, flexDirection: 'row', gap: space.sm,
    alignItems: 'center', justifyContent: 'center',
  },
  button_primary: { backgroundColor: colors.primary },
  button_secondary: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.borderStrong },
  button_danger: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.danger },
  pressed_primary: { backgroundColor: colors.primaryPressed },
  pressed_secondary: { backgroundColor: colors.surfaceAlt },
  pressed_danger: { backgroundColor: colors.dangerSoft },
  disabled: { opacity: 0.5 },
  buttonText: { ...type.button },
  buttonText_primary: { color: colors.onPrimary },
  buttonText_secondary: { color: colors.text },
  buttonText_danger: { color: colors.danger },
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  tile: {
    flexGrow: 1, backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border,
    paddingVertical: 14, paddingHorizontal: space.lg, gap: 6,
  },
  tile_warning: { backgroundColor: colors.warningSoft, borderColor: colors.warningSoft },
  tile_danger: { backgroundColor: colors.dangerSoft, borderColor: colors.dangerSoft },
  tileHero: { backgroundColor: colors.primary, borderColor: colors.primary },
  row6: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.textMuted },
  dot_warning: { backgroundColor: colors.warning },
  dot_danger: { backgroundColor: colors.danger },
  dotHero: { backgroundColor: colors.onPrimary },
  tileLabel: { ...type.captionStrong, color: colors.textSecondary, flexShrink: 1 },
  tileValue: { ...type.number, color: colors.text },
  tileText_warning: { color: colors.warningText },
  tileText_danger: { color: colors.dangerText },
  tileTextHero: { color: colors.onPrimary },
  tileValueHero: { fontSize: 40, lineHeight: 44, letterSpacing: -0.6, color: colors.onPrimary },
  chipsScroll: { flexGrow: 0, marginHorizontal: -space.lg },
  chips: { gap: space.sm, paddingHorizontal: space.lg },
  chip: {
    height: 36, paddingHorizontal: 14, justifyContent: 'center', borderRadius: radius.pill,
    borderWidth: 1, borderColor: colors.borderStrong, backgroundColor: colors.surface,
  },
  chipPressed: { backgroundColor: colors.surfaceAlt },
  chipActive: { backgroundColor: colors.ink, borderColor: colors.ink },
  chipText: { ...type.bodyStrong, color: colors.text },
  chipTextActive: { color: colors.onPrimary },
  field: { gap: 6 },
  fieldLabel: { ...type.captionStrong, color: colors.textSecondary },
  input: {
    minHeight: 48, borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md,
    paddingHorizontal: 14, fontSize: 16, color: colors.text, backgroundColor: colors.surface,
  },
  badge: {
    alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.pill,
    paddingHorizontal: 10, paddingVertical: 4,
  },
  badge_warning: { backgroundColor: colors.warningSoft },
  badge_danger: { backgroundColor: colors.dangerSoft },
  badge_success: { backgroundColor: colors.successSoft },
  badge_info: { backgroundColor: colors.primarySoft },
  badgeDot_warning: { backgroundColor: colors.warning },
  badgeDot_danger: { backgroundColor: colors.danger },
  badgeDot_success: { backgroundColor: colors.success },
  badgeDot_info: { backgroundColor: colors.primary },
  badgeText: { ...type.captionStrong },
  badgeText_warning: { color: colors.warningText },
  badgeText_danger: { color: colors.dangerText },
  badgeText_success: { color: colors.successText },
  badgeText_info: { color: colors.primaryText },
  empty: { ...type.body, color: colors.textSecondary, textAlign: 'center', paddingVertical: space.xl },
  row: { flexDirection: 'row', gap: space.sm, alignItems: 'center' },
});
