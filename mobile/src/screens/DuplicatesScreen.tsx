/** «Дубли» (duplicates_screen.py ПК): выданные посылки, которые снова пришли в отчёте склада. */
import { FlatList, StyleSheet, View } from 'react-native';

import { useApp, useDbQuery } from '../app/AppContext';
import type { Db } from '../core/db';
import {
  dateLabel, dismissDuplicate, DUPLICATE_DAYS, type DuplicateRow, listDuplicates, shipDuplicate,
} from '../core/duplicates';
import { pointLabel } from '../core/models';
import { Button, Empty, styles as ui, Text } from '../ui/components';
import { colors, radius, shadow, space, type } from '../ui/theme';

const SHIP_QUESTION = 'Это настоящие новые посылки? Они войдут в Excel отгрузки по последнему ' +
  'отчёту — если файл для точки уже сформирован, сформируйте его заново.';
const DISMISS_QUESTION = 'Посылки уже выдали, везти нечего? В отгрузку они не попадут. Если ' +
  `пропадут из отчёта и вернутся в течение ${DUPLICATE_DAYS} дней — снова станут дублями.`;

export function DuplicatesScreen() {
  const app = useApp();
  const { data } = useDbQuery((db) => listDuplicates(db), []);
  const rows = data ?? [];

  async function decide(
    title: string, question: string, action: (db: Db, id: number) => boolean, selected: DuplicateRow[],
  ) {
    let numbers = selected.slice(0, 10).map((r) => r.posting_number).join('\n');
    if (selected.length > 10) numbers += `\n… и ещё ${selected.length - 10}`;
    const yes = await app.ask(
      title, `${numbers}\n\n${question}`,
      [{ text: 'Нет', value: false, style: 'cancel' }, { text: 'Да', value: true }],
      false,
    );
    if (!yes) return;
    try {
      app.db().transaction(() => {
        for (const r of selected) action(app.db(), r.shipment_id);
      });
    } catch (e) {
      await app.message('Ошибка', `Не удалось сохранить решение:\n${(e as Error).message}`);
      return;
    }
    // Последним действием: синхронизация может заменить базу.
    app.onDataChanged();
  }

  const ship = (selected: DuplicateRow[]) => decide('Отгрузить', SHIP_QUESTION, shipDuplicate, selected);
  const dismiss = (selected: DuplicateRow[]) => decide('Ошибка Ozon', DISMISS_QUESTION, dismissDuplicate, selected);

  return (
    <FlatList
      style={{ backgroundColor: colors.bg }}
      contentContainerStyle={[ui.screenContent, { gap: 10 }]}
      data={rows}
      keyExtractor={(r) => String(r.shipment_id)}
      ListHeaderComponent={(
        <View style={{ gap: space.md, marginBottom: 2 }}>
          <Text style={ui.muted}>
            Посылки, которые уже выдали, а в отчёте склада они пришли снова — тот же номер, штрихкод
            и клиент, не позже {DUPLICATE_DAYS} дней после выдачи. В отгрузку они не попадут, пока
            вы не решите. «Отгрузить» — это новая посылка: она войдёт в Excel по последнему отчёту.
            «Ошибка Ozon» — посылку уже выдали, везти нечего.
          </Text>
          {rows.length > 1 ? (
            <View style={s.actions}>
              <View style={s.action}>
                <Button kind="primary" icon="truck" title="Отгрузить все" onPress={() => ship(rows)} />
              </View>
              <View style={s.action}>
                <Button kind="danger" icon="slash" title="Все — ошибка" onPress={() => dismiss(rows)} />
              </View>
            </View>
          ) : null}
        </View>
      )}
      ListEmptyComponent={<Empty text="Дублей в последнем отчёте нет." />}
      renderItem={({ item }) => (
        <View style={s.item}>
          <Text style={s.cell}>Ячейка {item.cell || '—'}</Text>
          <Text style={ui.text}>{item.posting_number}</Text>
          <Text style={s.product} numberOfLines={2}>{item.product_name || item.product_label}</Text>
          <Text style={ui.mutedSmall}>
            {item.client_name || item.ozon_client_id}{item.phone ? ` · ${item.phone}` : ''} · {pointLabel(item.point)}
          </Text>
          <Text style={ui.mutedSmall}>
            Выдана {dateLabel(item.delivered_at)} · снова пришла {dateLabel(item.reappeared_at)}
          </Text>
          <View style={[s.actions, s.itemActions]}>
            <View style={s.action}>
              <Button kind="primary" icon="truck" title="Отгрузить" onPress={() => ship([item])} />
            </View>
            <View style={s.action}>
              <Button kind="danger" icon="slash" title="Ошибка Ozon" onPress={() => dismiss([item])} />
            </View>
          </View>
        </View>
      )}
    />
  );
}

const s = StyleSheet.create({
  item: {
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.lg, padding: space.md, gap: 3, ...shadow,
  },
  cell: { ...type.bodyStrong, color: colors.text },
  product: { ...type.caption, color: colors.textSecondary },
  actions: { flexDirection: 'row', gap: space.sm },
  itemActions: { marginTop: space.sm },
  action: { flex: 1 },
});
