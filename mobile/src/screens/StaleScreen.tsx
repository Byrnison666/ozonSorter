/** «Залежавшиеся посылки» (stale_screen.py ПК): фильтры, подсветка по дням, выгрузка. */
import { type ParamListBase, type RouteProp, useRoute } from '@react-navigation/native';
import { useEffect, useState } from 'react';
import { FlatList, StyleSheet, View } from 'react-native';

import { useApp, useDbQuery } from '../app/AppContext';
import { type DeliveryPoint, pointLabel, POINT_LABELS } from '../core/models';
import {
  ALERT_DAYS, DANGER_DAYS, exportStaleXlsx, listStale, RETURN_DAYS, WARN_DAYS,
} from '../core/staleService';
import { formatTime } from '../core/sync/flow';
import { shareXlsx } from '../platform/io';
import {
  Badge, Button, Chips, Empty, StatTile, styles as ui, Text, Tiles,
} from '../ui/components';
import { colors, radius, shadow, space, type } from '../ui/theme';

const MIN_DAYS_PRESETS = [0, WARN_DAYS, DANGER_DAYS, ALERT_DAYS, RETURN_DAYS];

/** Цвета блока дней — как пилюля дней на ПК (_days_colors в stale_screen.py). */
function daysColors(days: number): [string, string] {
  if (days >= DANGER_DAYS) return [colors.dangerSoft, colors.dangerText];
  if (days >= WARN_DAYS) return [colors.warningSoft, colors.warningText];
  return [colors.surfaceAlt, colors.textSecondary];
}

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function StaleScreen() {
  const app = useApp();
  const [point, setPoint] = useState<DeliveryPoint | null>(null);
  const [minDays, setMinDays] = useState(0);
  // Переход с плашки «Скоро возврат» на «Главной» выставляет фильтр срока.
  const params = useRoute<RouteProp<ParamListBase>>().params as { minDays?: number; at?: number } | undefined;
  useEffect(() => {
    if (params?.minDays !== undefined) setMinDays(params.minDays);
  }, [params?.minDays, params?.at]);
  const { data } = useDbQuery((db) => {
    const latest = db.get<{ id: number; started_at: string; source_file_name: string }>(
      'SELECT id, started_at, source_file_name FROM import_sessions ORDER BY id DESC LIMIT 1',
    );
    return { latest, rows: listStale(db, point, minDays) };
  }, [point, minDays]);
  const rows = data?.rows ?? [];
  const latest = data?.latest;

  async function onExport() {
    const d = new Date();
    const name = `Залежавшиеся_${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}.xlsx`;
    try {
      await shareXlsx(exportStaleXlsx(rows), name, 'Залежавшиеся посылки');
    } catch (e) {
      await app.message('Ошибка', `Не удалось сохранить файл:\n${(e as Error).message}`);
    }
  }

  const emptyText = !latest
    ? 'Отчёт ещё не загружен. Импортируйте его на «Главной».'
    : point !== null || minDays ? 'Нет посылок по выбранным фильтрам.' : 'Залежавшихся посылок нет.';

  return (
    <FlatList
      style={{ backgroundColor: colors.bg }}
      // Между карточками посылок 10, от шапки до первой — 12 (marginBottom шапки).
      contentContainerStyle={[ui.screenContent, { gap: 10 }]}
      data={rows}
      keyExtractor={(r, i) => `${r.posting_number}|${r.product_label}|${i}`}
      ListHeaderComponent={(
        <View style={{ gap: space.md, marginBottom: 2 }}>
          {latest ? (
            <Text style={ui.mutedSmall}>По отчёту от {formatTime(latest.started_at)} — {latest.source_file_name || '—'}</Text>
          ) : null}
          {latest && latest.started_at.slice(0, 10) < todayIso()
            ? <Badge tone="warning" dot text="отчёт не сегодняшний — загрузите свежий" /> : null}
          <Chips
            options={[{ label: 'Все точки', value: null as DeliveryPoint | null },
              ...(Object.keys(POINT_LABELS) as DeliveryPoint[]).map((p) => ({ label: POINT_LABELS[p], value: p }))]}
            value={point}
            onChange={setPoint}
          />
          <Chips
            options={MIN_DAYS_PRESETS.map((d) => ({ label: d ? `От ${d} дней` : 'Любой срок', value: d }))}
            value={minDays}
            onChange={setMinDays}
          />
          <Tiles columns={3}>
            <StatTile label="Показано" value={rows.length} />
            <StatTile label={`${WARN_DAYS}–${DANGER_DAYS - 1} дней`} tone="warning"
              value={rows.filter((r) => r.days >= WARN_DAYS && r.days < DANGER_DAYS).length} />
            <StatTile label={`От ${DANGER_DAYS} дней`} tone="danger" value={rows.filter((r) => r.days >= DANGER_DAYS).length} />
          </Tiles>
          <Button kind="primary" icon="download" title="Выгрузить в Excel" onPress={onExport} disabled={!rows.length} />
        </View>
      )}
      ListEmptyComponent={<Empty text={emptyText} />}
      renderItem={({ item }) => {
        const [daysBg, daysFg] = daysColors(item.days);
        return (
          <View style={s.item}>
            <View style={[s.days, { backgroundColor: daysBg }]}>
              <Text style={[s.daysValue, { color: daysFg }]}>{item.days}</Text>
              <Text style={[s.daysUnit, { color: daysFg }]}>дн.</Text>
            </View>
            <View style={s.body}>
              <View style={s.top}>
                <Text style={s.cell}>Ячейка {item.cell || '—'}</Text>
                {item.is_returned ? <Badge tone="danger" dot text="Возврат" /> : null}
              </View>
              <Text style={ui.text}>{item.posting_number}</Text>
              <Text style={s.product} numberOfLines={2}>{item.product_name || item.product_label}</Text>
              <Text style={ui.mutedSmall}>
                {item.client_name || item.ozon_client_id}{item.phone ? ` · ${item.phone}` : ''} · {pointLabel(item.point)}
              </Text>
            </View>
          </View>
        );
      }}
    />
  );
}

const s = StyleSheet.create({
  item: {
    flexDirection: 'row', gap: space.md, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.lg, padding: space.md, ...shadow,
  },
  days: { width: 52, height: 58, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  daysValue: { fontSize: 22, lineHeight: 26, fontWeight: '600' },
  daysUnit: { ...type.caption },
  body: { flex: 1, gap: 3 },
  top: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: space.sm },
  cell: { ...type.bodyStrong, color: colors.text },
  product: { ...type.caption, color: colors.textSecondary },
});
