/**
 * «Резервные копии» — копии базы на самом телефоне: ежедневные и сделанные
 * перед заменой базы. Их можно отправить («Поделиться») и восстановить из них
 * базу — например, когда Яндекс.Диск недоступен или отдал испорченную базу.
 */
import { useFocusEffect } from '@react-navigation/native';
import { useCallback, useState } from 'react';
import { FlatList, StyleSheet, View } from 'react-native';

import { useApp } from '../app/AppContext';
import {
  inspectBackup, KEEP_DAILY_LOCAL, listBackups, type LocalBackup, restoreQuestion,
} from '../core/localBackup';
import { formatSize } from '../core/sync/flow';
import { DB_PATH, deviceDeps } from '../platform/deps';
import { DATA_DIR } from '../platform/expoDb';
import { expoFiles } from '../platform/files';
import { pickDatabaseFile, shareDatabaseFile } from '../platform/io';
import { Button, Empty, styles as ui, Text } from '../ui/components';
import { colors, radius, shadow, space, type } from '../ui/theme';

const KIND_LABELS: Record<LocalBackup['kind'], string> = {
  daily: 'Копия дня',
  presync: 'Перед заменой базы',
};

export function BackupsScreen() {
  const app = useApp();
  const [rows, setRows] = useState<LocalBackup[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setRows(await listBackups(expoFiles, DB_PATH));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // Перечитать при каждом открытии экрана и после замены базы.
  useFocusEffect(useCallback(() => {
    void reload();
  }, [reload, app.version]));

  async function restore(path: string, source: string) {
    let question: string;
    try {
      const summary = await app.runBusy('Проверка копии…', () => inspectBackup(deviceDeps, path));
      question = restoreQuestion(source, summary);
    } catch (e) {
      await app.message('Восстановить нельзя', (e as Error).message);
      return;
    }
    const yes = await app.ask('Восстановить базу', question, [
      { text: 'Отмена', value: false, style: 'cancel' },
      { text: 'Восстановить', value: true, style: 'destructive' },
    ], false);
    if (!yes) return;
    try {
      await app.restoreBackup(path);
    } catch (e) {
      await app.message('Не удалось восстановить', (e as Error).message);
      return;
    } finally {
      void reload();
    }
    await app.message(
      'База восстановлена',
      app.configured ? 'При следующей синхронизации она будет выложена на Яндекс.Диск.' : 'Можно работать.',
    );
  }

  async function restoreFromFile() {
    let picked: { name: string; path: string } | null;
    try {
      picked = await app.external(() => pickDatabaseFile(DATA_DIR));
    } catch (e) {
      await app.message('Ошибка', `Не удалось открыть файл:\n${(e as Error).message}`);
      return;
    }
    if (!picked) return;
    try {
      await restore(picked.path, `файла «${picked.name}»`);
    } finally {
      await expoFiles.remove(picked.path).catch((e) => console.warn('Picked file was not removed', e));
    }
  }

  async function share(item: LocalBackup) {
    try {
      await app.external(() => shareDatabaseFile(item.path, 'Копия базы OzonSorter'));
    } catch (e) {
      await app.message('Ошибка', `Не удалось поделиться копией:\n${(e as Error).message}`);
    }
  }

  return (
    <FlatList
      style={{ backgroundColor: colors.bg }}
      contentContainerStyle={[ui.screenContent, { gap: 10 }]}
      data={rows}
      keyExtractor={(r) => r.path}
      ListHeaderComponent={(
        <View style={{ gap: space.md, marginBottom: 2 }}>
          <Text style={ui.muted}>
            Копии базы хранятся на этом телефоне и не зависят от Яндекс.Диска: копия дня — первая за
            день (последние {KEEP_DAILY_LOCAL}), «перед заменой» — база, какой она была до загрузки
            с Диска или восстановления. Копию можно отправить на компьютер или в мессенджер.
          </Text>
          <Button icon="folder" title="Восстановить из файла" onPress={() => void restoreFromFile()} />
          {error ? <Text style={ui.muted}>Не удалось прочитать список копий: {error}</Text> : null}
        </View>
      )}
      ListEmptyComponent={<Empty text="Копий пока нет: первая появится, когда в базе будут данные." />}
      renderItem={({ item }) => (
        <View style={s.item}>
          <Text style={s.title}>{item.when}</Text>
          <Text style={ui.mutedSmall}>{KIND_LABELS[item.kind]} · {formatSize(item.size)}</Text>
          <View style={[s.actions, s.itemActions]}>
            <View style={s.action}>
              <Button icon="share-2" title="Поделиться" onPress={() => void share(item)} />
            </View>
            <View style={s.action}>
              <Button
                kind="primary" icon="rotate-ccw" title="Восстановить"
                onPress={() => void restore(item.path, `копии от ${item.when}`)}
              />
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
  title: { ...type.bodyStrong, color: colors.text },
  actions: { flexDirection: 'row', gap: space.sm },
  itemActions: { marginTop: space.sm },
  action: { flex: 1 },
});
