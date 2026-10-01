/** «Клиенты» — список, добавление, изменение, удаление, импорт (clients_screen.py ПК). */
import { useState } from 'react';
import { FlatList, KeyboardAvoidingView, Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useApp, useDbQuery } from '../app/AppContext';
import { importClients, writeClientTemplate } from '../core/clientImport';
import {
  addClient, type ClientForm, type ClientResult, type ClientRow, deactivateClient, editClient,
  listActiveClients,
} from '../core/clients';
import { DeliveryPoint, pointLabel, POINT_LABELS } from '../core/models';
import { readActiveSheet } from '../core/sheet';
import { pickXlsx, shareXlsx } from '../platform/io';
import { Button, Empty, Field, Segmented, styles as ui } from '../ui/components';
import { colors, space } from '../ui/theme';

const EMPTY_FORM: ClientForm = { ozonClientId: '', fullName: '', phone: '', point: null };

export function ClientsScreen() {
  const app = useApp();
  const { data: clients = [] } = useDbQuery((db) => listActiveClients(db), []);
  const [editing, setEditing] = useState<{ id: number | null; form: ClientForm } | null>(null);

  async function showResult(r: ClientResult): Promise<boolean> {
    if (r.ok) return true;
    await app.message(r.error.startsWith('Клиент с ID') ? 'Дубликат' : 'Ошибка', r.error);
    return false;
  }

  async function onSave() {
    if (!editing) return;
    let r: ClientResult;
    try {
      r = editing.id === null ? addClient(app.db(), editing.form) : editClient(app.db(), editing.id, editing.form);
    } catch (e) {
      await app.message('Ошибка', `Не удалось сохранить клиента:\n${(e as Error).message}`);
      return;
    }
    if (!(await showResult(r))) return;
    setEditing(null);
    app.onDataChanged();
  }

  async function onDelete(client: ClientRow) {
    const name = client.full_name || client.ozon_client_id;
    const yes = await app.ask(
      'Удалить клиента',
      `Удалить клиента «${name}» из списка?\nИстория его посылок сохранится, новые перестанут к нему относиться.`,
      [{ text: 'Нет', value: false, style: 'cancel' }, { text: 'Удалить', value: true, style: 'destructive' }],
      false,
    );
    if (!yes) return;
    try {
      deactivateClient(app.db(), client.id);
    } catch (e) {
      await app.message('Ошибка', `Не удалось удалить клиента:\n${(e as Error).message}`);
      return;
    }
    setEditing(null);
    app.onDataChanged();
  }

  async function onImportFile() {
    let picked;
    try {
      picked = await pickXlsx();
      if (!picked) return;
      const bytes = picked.bytes;
      const result = await app.runBusy('Импорт клиентов…', () => importClients(app.db(), readActiveSheet(bytes)));
      let msg = `Обработано строк: ${result.data_rows}\nДобавлено: ${result.added}\n` +
        `Обновлено: ${result.updated}\nОшибок: ${result.errors.length}`;
      if (result.errors.length) {
        const shown = result.errors.slice(0, 10).map(([row, m]) => `  строка ${row}: ${m}`).join('\n');
        const more = result.errors.length > 10 ? `\n  …ещё ${result.errors.length - 10}` : '';
        msg += `\n\nНе загружены:\n${shown}${more}`;
      }
      await app.message(result.errors.length ? 'Импорт завершён с ошибками' : 'Импорт завершён', msg);
      if (result.added || result.updated) app.onDataChanged();
    } catch (e) {
      await app.message('Ошибка импорта', `Не удалось импортировать файл:\n${(e as Error).message}`);
    }
  }

  async function onTemplate() {
    try {
      await shareXlsx(writeClientTemplate(), 'Шаблон_клиентов.xlsx', 'Шаблон клиентов');
    } catch (e) {
      await app.message('Ошибка', `Не удалось сохранить шаблон:\n${(e as Error).message}`);
    }
  }

  const current = editing && editing.id !== null ? clients.find((c) => c.id === editing.id) : undefined;

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={clients}
        keyExtractor={(c) => String(c.id)}
        contentContainerStyle={ui.screenContent}
        ListHeaderComponent={(
          <View style={{ gap: space.sm }}>
            <Text style={ui.muted}>Постоянные Ozon ID наших клиентов и их точки выдачи.</Text>
            <Button kind="primary" title="+ Добавить клиента" onPress={() => setEditing({ id: null, form: EMPTY_FORM })} />
            <View style={ui.row}>
              <View style={{ flex: 1 }}><Button title="Импорт из файла" onPress={onImportFile} /></View>
              <View style={{ flex: 1 }}><Button title="Шаблон" onPress={onTemplate} /></View>
            </View>
            <Text style={ui.mutedSmall}>Клиентов: {clients.length}</Text>
          </View>
        )}
        ListEmptyComponent={<Empty text="Клиентов пока нет." />}
        renderItem={({ item }) => (
          <Pressable
            accessibilityRole="button"
            accessibilityHint="Изменить клиента"
            onPress={() => setEditing({
              id: item.id,
              form: {
                ozonClientId: item.ozon_client_id, fullName: item.full_name ?? '', phone: item.phone ?? '',
                point: (item.fixed_delivery_point as DeliveryPoint | null) ?? null,
              },
            })}
            style={({ pressed }) => [s.item, pressed && { backgroundColor: colors.surfaceAlt }]}
          >
            <View style={{ flex: 1 }}>
              <Text style={s.itemTitle}>{item.full_name || '—'}</Text>
              <Text style={ui.muted}>{item.ozon_client_id}{item.phone ? ` · ${item.phone}` : ''}</Text>
            </View>
            <Text style={s.point}>{pointLabel(item.fixed_delivery_point)}</Text>
          </Pressable>
        )}
      />

      <Modal visible={editing !== null} animationType="slide" onRequestClose={() => setEditing(null)}>
        <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
          <KeyboardAvoidingView behavior="height" style={{ flex: 1 }}>
            {editing ? (
              <View style={ui.screenContent}>
                <Text style={s.formTitle}>{editing.id === null ? 'Новый клиент' : 'Изменить клиента'}</Text>
                <Text style={ui.muted}>Постоянный Ozon ID и точка выдачи.</Text>
                <Field
                  label="Ozon ID" placeholder="например, 0224933356" keyboardType="number-pad"
                  value={editing.form.ozonClientId}
                  onChangeText={(v) => setEditing({ ...editing, form: { ...editing.form, ozonClientId: v } })}
                />
                <Field
                  label="ФИО" placeholder="необязательно" value={editing.form.fullName}
                  onChangeText={(v) => setEditing({ ...editing, form: { ...editing.form, fullName: v } })}
                />
                <Field
                  label="Телефон" placeholder="необязательно" keyboardType="phone-pad" value={editing.form.phone}
                  onChangeText={(v) => setEditing({ ...editing, form: { ...editing.form, phone: v } })}
                />
                <Text style={ui.mutedSmall}>Точка выдачи</Text>
                <Segmented
                  options={Object.values(DeliveryPoint).map((p) => ({ label: POINT_LABELS[p], value: p as DeliveryPoint | null }))}
                  value={editing.form.point}
                  onChange={(point) => setEditing({ ...editing, form: { ...editing.form, point } })}
                />
                <Button kind="primary" title="Сохранить" onPress={onSave} />
                <Button title="Отмена" onPress={() => setEditing(null)} />
                {current ? <Button kind="danger" title="Удалить клиента" onPress={() => onDelete(current)} /> : null}
              </View>
            ) : null}
          </KeyboardAvoidingView>
        </SafeAreaView>
      </Modal>
    </View>
  );
}

const s = StyleSheet.create({
  item: {
    flexDirection: 'row', alignItems: 'center', gap: space.md, backgroundColor: colors.surface,
    borderWidth: 1, borderColor: colors.border, borderRadius: 12, padding: space.md, minHeight: 56,
  },
  itemTitle: { fontSize: 16, color: colors.text, fontWeight: '500' },
  point: { fontSize: 13, color: colors.primaryText },
  formTitle: { fontSize: 20, fontWeight: '600', color: colors.text },
});
