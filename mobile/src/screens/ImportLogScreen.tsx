/** «Журнал импортов» (import_log_screen.py ПК): все загрузки со статистикой и пометкой повтора. */
import { FlatList, StyleSheet, View } from 'react-native';

import { useDbQuery } from '../app/AppContext';
import type { ImportSessionRow } from '../core/importService';
import { formatTime } from '../core/sync/flow';
import { Badge, Empty, Icon, styles as ui, Text } from '../ui/components';
import { colors, radius, shadow, space, type } from '../ui/theme';

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
      contentContainerStyle={[ui.screenContent, { gap: 10 }]}
      data={data?.sessions ?? []}
      keyExtractor={(s) => String(s.id)}
      ListHeaderComponent={<Text style={ui.muted}>Все загрузки отчётов Казакова со статистикой — чтобы ничего не пропустить.</Text>}
      ListEmptyComponent={<Empty text="Импортов ещё не было." />}
      renderItem={({ item }) => (
        <View style={s.item}>
          <View style={ui.row}>
            <Text style={s.date}>{formatTime(item.started_at)}</Text>
            {data?.repeats.has(item.id) ? <Badge tone="warning" dot text="повтор файла" /> : null}
          </View>
          <View style={s.file}>
            <Icon name="file-text" size={14} color={colors.textMuted} />
            {/* У имени файла важен и конец — дата, поэтому сокращение посередине (как на ПК). */}
            <Text style={[ui.muted, { flex: 1 }]} numberOfLines={1} ellipsizeMode="middle">{item.source_file_name || '—'}</Text>
          </View>
          <Text style={s.stats}>
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
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: radius.lg,
    padding: 14, gap: 6, ...shadow,
  },
  date: { ...type.bodyStrong, color: colors.text },
  file: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  stats: { ...type.caption, color: colors.textSecondary },
});
