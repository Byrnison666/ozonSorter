/** «Журнал импортов» (import_log_screen.py ПК): все загрузки со статистикой и пометкой повтора. */
import { FlatList, StyleSheet, Text, View } from 'react-native';

import { useDbQuery } from '../app/AppContext';
import type { ImportSessionRow } from '../core/importService';
import { formatTime } from '../core/sync/flow';
import { Badge, Empty, styles as ui } from '../ui/components';
import { colors, space } from '../ui/theme';

export function ImportLogScreen() {
  const { data } = useDbQuery((db) => {
    const sessions = db.all<ImportSessionRow>('SELECT * FROM import_sessions ORDER BY started_at DESC');
    // Повтор файла: тот же sha256 уже грузили в более раннюю сессию.
    const seen = new Set<string>();
    const repeats = new Set<number>();
    for (const s of [...sessions].sort((a, b) => (a.started_at < b.started_at ? -1 : a.started_at > b.started_at ? 1 : a.id - b.id))) {
      if (seen.has(s.source_file_sha256)) repeats.add(s.id);
      seen.add(s.source_file_sha256);
    }
    return { sessions, repeats };
  }, []);

  return (
    <FlatList
      style={{ backgroundColor: colors.bg }}
      contentContainerStyle={ui.screenContent}
      data={data?.sessions ?? []}
      keyExtractor={(s) => String(s.id)}
      ListHeaderComponent={<Text style={ui.muted}>Все загрузки отчётов Казакова со статистикой — чтобы ничего не пропустить.</Text>}
      ListEmptyComponent={<Empty text="Импортов ещё не было." />}
      renderItem={({ item }) => (
        <View style={s.item}>
          <View style={ui.row}>
            <Text style={s.date}>{formatTime(item.started_at)}</Text>
            {data?.repeats.has(item.id) ? <Badge tone="warning" text="повтор файла" /> : null}
          </View>
          <Text style={ui.muted} numberOfLines={1}>{item.source_file_name || '—'}</Text>
          <Text style={ui.mutedSmall}>
            Всего {item.total_rows} · наших {item.matched_rows} · новых к отгрузке {item.new_to_ship_rows} ·
            возвраты {item.returned_rows} · уже на точках {item.already_on_point} · не наши {item.not_ours_rows} ·
            КТЯ {item.kty_rows}
          </Text>
        </View>
      )}
    />
  );
}

const s = StyleSheet.create({
  item: {
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: 12,
    padding: space.md, gap: 4,
  },
  date: { fontSize: 15, fontWeight: '600', color: colors.text },
});
