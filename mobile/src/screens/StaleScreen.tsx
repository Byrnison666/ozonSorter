/** «Залежавшиеся посылки» (stale_screen.py ПК): фильтры, подсветка по дням, выгрузка. */
import { useState } from 'react';
import { FlatList, StyleSheet, Text, View } from 'react-native';

import { useApp, useDbQuery } from '../app/AppContext';
import { type DeliveryPoint, pointLabel, POINT_LABELS } from '../core/models';
import {
  DANGER_DAYS, exportStaleXlsx, listStale, WARN_DAYS,
} from '../core/staleService';
import { formatTime } from '../core/sync/flow';
import { shareXlsx } from '../platform/io';
import { Badge, Button, Empty, Segmented, StatTile, styles as ui, Tiles } from '../ui/components';
import { colors, space } from '../ui/theme';

const MIN_DAYS_PRESETS = [0, WARN_DAYS, DANGER_DAYS, 14];

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function StaleScreen() {
  const app = useApp();
  const [point, setPoint] = useState<DeliveryPoint | null>(null);
  const [minDays, setMinDays] = useState(0);
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
      contentContainerStyle={ui.screenContent}
      data={rows}
      keyExtractor={(r, i) => `${r.posting_number}|${r.product_label}|${i}`}
      ListHeaderComponent={(
        <View style={{ gap: space.md }}>
          {latest ? (
            <Text style={ui.muted}>По отчёту от {formatTime(latest.started_at)} — {latest.source_file_name || '—'}</Text>
          ) : null}
          {latest && latest.started_at.slice(0, 10) < todayIso()
            ? <Badge tone="warning" text="отчёт не сегодняшний — загрузите свежий" /> : null}
          <Segmented
            options={[{ label: 'Все точки', value: null as DeliveryPoint | null },
              ...(Object.keys(POINT_LABELS) as DeliveryPoint[]).map((p) => ({ label: POINT_LABELS[p], value: p }))]}
            value={point}
            onChange={setPoint}
          />
          <Segmented
            options={MIN_DAYS_PRESETS.map((d) => ({ label: d ? `От ${d} дней` : 'Любой срок', value: d }))}
            value={minDays}
            onChange={setMinDays}
          />
          <Tiles>
            <StatTile label="Показано" value={rows.length} />
            <StatTile label={`${WARN_DAYS}–${DANGER_DAYS - 1} дней`} tone="warning"
              value={rows.filter((r) => r.days >= WARN_DAYS && r.days < DANGER_DAYS).length} />
            <StatTile label={`От ${DANGER_DAYS} дней`} tone="danger" value={rows.filter((r) => r.days >= DANGER_DAYS).length} />
          </Tiles>
          <Button title="Выгрузить в Excel" onPress={onExport} disabled={!rows.length} />
        </View>
      )}
      ListEmptyComponent={<Empty text={emptyText} />}
      renderItem={({ item }) => {
        const tone = item.days >= DANGER_DAYS ? colors.dangerSoft : item.days >= WARN_DAYS ? colors.warningSoft : colors.surface;
        return (
          <View style={[s.item, { backgroundColor: tone }]}>
            <View style={s.days}>
              <Text style={s.daysValue}>{item.days}</Text>
              <Text style={ui.mutedSmall}>дн.</Text>
            </View>
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={s.cell}>
                Ячейка {item.cell || '—'}
                {item.is_returned ? <Text style={s.returned}>  Возврат</Text> : null}
              </Text>
              <Text style={ui.text}>{item.posting_number}</Text>
              <Text style={ui.muted} numberOfLines={2}>{item.product_name || item.product_label}</Text>
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
    flexDirection: 'row', gap: space.md, borderWidth: 1, borderColor: colors.border, borderRadius: 12, padding: space.md,
  },
  days: { width: 44, alignItems: 'center' },
  daysValue: { fontSize: 22, fontWeight: '700', color: colors.text, fontVariant: ['tabular-nums'] },
  cell: { fontSize: 15, fontWeight: '600', color: colors.text },
  returned: { color: colors.dangerText, fontWeight: '700' },
});
