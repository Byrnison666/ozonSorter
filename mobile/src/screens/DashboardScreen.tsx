/**
 * «Главная» — импорт отчёта склада и выгрузка «Отгрузка» по точкам
 * (dashboard_screen.py ПК). Отличие от ПК: показывается последний импорт из
 * базы, а не только сделанный в этом запуске, — Android выгружает приложение
 * из памяти, и повторно загружать отчёт ради выгрузки не нужно.
 */
import { type NavigationProp, type ParamListBase, useNavigation } from '@react-navigation/native';
import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { useApp, useDbQuery } from '../app/AppContext';
import {
  commitExport, defaultExportFileName, prepareExport,
} from '../core/exportService';
import { findDuplicateImport, type ImportSessionRow, processImport } from '../core/importService';
import { DeliveryPoint, POINT_LABELS } from '../core/models';
import { checkReport, parseReport } from '../core/parser';
import { readActiveSheet } from '../core/sheet';
import { ALERT_DAYS, returnAlert, returnAlertText } from '../core/staleService';
import { formatTime } from '../core/sync/flow';
import { sha256 } from '../platform/deps';
import { pickXlsx, shareXlsx } from '../platform/io';
import { Badge, Button, Card, Screen, StatTile, styles, Tiles } from '../ui/components';
import { colors } from '../ui/theme';

function isToday(ts: string): boolean {
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  return ts.slice(0, 10) === today;
}

export function DashboardScreen() {
  const app = useApp();
  const navigation = useNavigation<NavigationProp<ParamListBase>>();
  const [exporting, setExporting] = useState<DeliveryPoint | null>(null);
  const { data: latest } = useDbQuery(
    (db) => db.get<ImportSessionRow>('SELECT * FROM import_sessions ORDER BY id DESC LIMIT 1') ?? null,
    [],
  );
  const { data: alert } = useDbQuery((db) => returnAlert(db), []);

  async function onImport() {
    let picked;
    try {
      picked = await pickXlsx();
    } catch (e) {
      await app.message('Ошибка импорта', `Не удалось открыть файл:\n${(e as Error).message}`);
      return;
    }
    if (!picked) return;
    const { name, bytes } = picked;
    try {
      const sha = await sha256(bytes);
      const dup = findDuplicateImport(app.db(), sha);
      if (dup) {
        const again = await app.ask(
          'Файл уже загружали',
          `Этот файл уже импортировали ${formatTime(dup.started_at)} («${dup.source_file_name}»).\nЗагрузить повторно?`,
          [{ text: 'Нет', value: false, style: 'cancel' }, { text: 'Да', value: true }],
          false,
        );
        if (!again) return;
      }
      // Сначала проверка: пустой или чужой отчёт стал бы «последним» и
      // опустошил бы «Отгрузку» и «Залежавшиеся».
      const rows = await app.runBusy('Проверка отчёта…', () => parseReport(readActiveSheet(bytes)));
      const { errors, warnings } = checkReport(rows);
      if (errors.length) {
        await app.message('Файл не подходит', `${errors.join('\n\n')}\n\nФайл не загружен, база не изменилась.`);
        return;
      }
      if (warnings.length) {
        const load = await app.ask(
          'Проверьте отчёт',
          `${warnings.join('\n\n')}\n\nЗагрузить файл?`,
          [{ text: 'Нет', value: false, style: 'cancel' }, { text: 'Да', value: true }],
          false,
        );
        if (!load) return;
      }
      const session = await app.runBusy('Импорт отчёта…', () => processImport(app.db(), name, sha, rows));
      const summary = `Распознано наших посылок: ${session.matched_rows}.\nНовых к отгрузке: ${session.new_to_ship_rows}.`;
      const [alertCount, overdue] = returnAlert(app.db());
      await app.message(
        'Импорт завершён',
        alertCount ? `${summary}\n\nСкоро возврат. ${returnAlertText(alertCount, overdue)}` : summary,
      );
    } catch (e) {
      await app.message('Ошибка импорта', `Не удалось импортировать файл:\n${(e as Error).message}`);
      return;
    }
    // Последним действием: синхронизация может заменить базу.
    app.onDataChanged();
  }

  async function onExport(point: DeliveryPoint) {
    if (!latest) return;
    setExporting(point);
    try {
      const prepared = prepareExport(app.db(), point, latest.id);
      const fileName = defaultExportFileName(point);
      // Как на ПК: сначала файл, потом отметка посылок в базе.
      await shareXlsx(prepared.bytes, fileName, `Отгрузка — ${POINT_LABELS[point]}`);
      commitExport(app.db(), prepared, fileName);
    } catch (e) {
      await app.message('Ошибка экспорта', `Не удалось сформировать файл:\n${(e as Error).message}`);
      return;
    } finally {
      setExporting(null);
    }
    // Выгрузка помечает посылки как выгруженные — это тоже изменение базы.
    app.onDataChanged();
  }

  return (
    <Screen>
      {app.dbError ? <Badge tone="danger" text={`База не открылась: ${app.dbError}`} /> : null}
      {alert && alert[0] ? (
        <View style={[styles.card, local.alert]}>
          <Text style={[styles.cardTitle, local.alertText]}>Скоро возврат</Text>
          <Text style={[styles.text, local.alertText]}>{returnAlertText(alert[0], alert[1])}</Text>
          <Button
            kind="danger"
            title="Показать посылки"
            // at — чтобы фильтр выставился и при повторном переходе с теми же параметрами.
            onPress={() => navigation.navigate('stale', { minDays: ALERT_DAYS, at: Date.now() })}
          />
        </View>
      ) : null}
      <Card title="Загрузите файл от Казакова 68" subtitle="Отчёт склада Ozon (.xlsx) из Загрузок, Telegram или почты.">
        <Button kind="primary" title="Выбрать отчёт" onPress={onImport} />
      </Card>

      {latest ? (
        <>
          <Card title="Результаты последнего импорта">
            <Text style={styles.text}>{latest.source_file_name}</Text>
            <Text style={styles.muted}>{formatTime(latest.started_at)}</Text>
            {!isToday(latest.started_at) ? <Badge tone="warning" text="отчёт не сегодняшний — загрузите свежий" /> : null}
            <Tiles>
              <StatTile label="Всего строк" value={latest.total_rows} />
              <StatTile label="Наших" value={latest.matched_rows} />
              <StatTile label="Новых к отгрузке" value={latest.new_to_ship_rows} />
              <StatTile label="Уже на точках" value={latest.already_on_point} />
              <StatTile label="Возвраты" value={latest.returned_rows} />
              <StatTile label="Не наши" value={latest.not_ours_rows} />
              <StatTile label="КТЯ" value={latest.kty_rows} />
            </Tiles>
          </Card>
          <Card title="Формирование отгрузки" subtitle="Отдельный Excel для каждой точки — откроется меню «Поделиться».">
            {Object.values(DeliveryPoint).map((point) => (
              <Button
                key={point}
                kind="primary"
                title={`Excel для ${point === 'KOMSOMOLSKAYA_4' ? 'Комсомольской 4' : 'Кольцевой 16'}`}
                busy={exporting === point}
                disabled={exporting !== null}
                onPress={() => onExport(point)}
              />
            ))}
          </Card>
        </>
      ) : null}
    </Screen>
  );
}

const local = StyleSheet.create({
  alert: { backgroundColor: colors.dangerSoft, borderColor: colors.danger },
  alertText: { color: colors.dangerText },
});
