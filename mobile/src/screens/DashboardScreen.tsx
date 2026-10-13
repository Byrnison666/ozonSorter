/**
 * «Главная» — импорт отчёта склада и выгрузка «Отгрузка» по точкам
 * (dashboard_screen.py ПК). Отличие от ПК: показывается последний импорт из
 * базы, а не только сделанный в этом запуске, — Android выгружает приложение
 * из памяти, и повторно загружать отчёт ради выгрузки не нужно.
 */
import { type NavigationProp, type ParamListBase, useNavigation } from '@react-navigation/native';
import { useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { useApp, useDbQuery } from '../app/AppContext';
import { duplicatesAlertText, incompleteReportText, listDuplicates } from '../core/duplicates';
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
import {
  Badge, Button, Card, Icon, type IconName, Screen, StatTile, styles, Text, Tiles,
} from '../ui/components';
import { colors, radius, type } from '../ui/theme';

/** Жёлтая плашка-предупреждение с переходом к посылкам (alertBanner ПК). */
function AlertBanner({ icon, title, text, button, buttonIcon = 'arrow-right', onPress }: {
  icon: IconName; title: string; text: string; button: string; buttonIcon?: IconName; onPress: () => void;
}) {
  return (
    <View style={local.alert}>
      <View style={local.row}>
        <View style={[local.bubble, local.alertBubble]}>
          <Icon name={icon} size={18} color={colors.onPrimary} />
        </View>
        <View style={local.textCol}>
          <Text style={local.alertTitle}>{title}</Text>
          <Text style={local.alertText}>{text}</Text>
        </View>
      </View>
      <Button icon={buttonIcon} title={button} onPress={onPress} />
    </View>
  );
}

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
  const { data: duplicates } = useDbQuery((db) => listDuplicates(db).length, []);

  async function onImport() {
    let picked;
    try {
      picked = await app.external(pickXlsx);
    } catch (e) {
      await app.message('Ошибка импорта', `Не удалось открыть файл:\n${(e as Error).message}`);
      return;
    }
    if (!picked) return;
    const { name, bytes } = picked;
    let openDuplicates = false;
    try {
      await app.dbReady();
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
      const { session, skippedDuplicates } = await app.runBusy(
        'Импорт отчёта…', () => processImport(app.db(), name, sha, rows),
      );
      const summary = `Распознано наших посылок: ${session.matched_rows}.\nНовых к отгрузке: ${session.new_to_ship_rows}.`;
      const alerts: string[] = [];
      const duplicateCount = listDuplicates(app.db()).length;
      if (duplicateCount) alerts.push(`Дубли. ${duplicatesAlertText(duplicateCount)}`);
      if (skippedDuplicates) alerts.push(incompleteReportText(skippedDuplicates));
      const [alertCount, overdue] = returnAlert(app.db());
      if (alertCount) alerts.push(`Скоро возврат. ${returnAlertText(alertCount, overdue)}`);
      const text = [summary, ...alerts].join('\n\n');
      if (duplicateCount) {
        openDuplicates = await app.ask('Импорт завершён', text, [
          { text: 'Позже', value: false, style: 'cancel' }, { text: 'Разобрать дубли', value: true },
        ], false);
      } else {
        await app.message('Импорт завершён', text);
      }
    } catch (e) {
      await app.message('Ошибка импорта', `Не удалось импортировать файл:\n${(e as Error).message}`);
      return;
    }
    if (openDuplicates) navigation.navigate('duplicates');
    // Последним действием: синхронизация может заменить базу.
    app.onDataChanged();
  }

  async function onExport(point: DeliveryPoint) {
    if (!latest) return;
    setExporting(point);
    try {
      await app.dbReady();
      const prepared = prepareExport(app.db(), point, latest.id);
      const fileName = defaultExportFileName(point);
      // Как на ПК: сначала файл, потом отметка посылок в базе.
      await app.external(() => shareXlsx(prepared.bytes, fileName, `Отгрузка — ${POINT_LABELS[point]}`));
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
      {app.configured && app.diskUnavailable ? (
        <AlertBanner
          icon="cloud-off"
          title="Яндекс.Диск недоступен"
          text={'Работайте как обычно: база сохранена на телефоне. '
            + (app.dirty === false ? 'Невыложенных изменений нет.' : 'Изменения выложатся, когда Диск заработает.')
            + (app.lastSync ? `\nПоследняя синхронизация: ${app.lastSync}` : '')}
          button="Повторить"
          buttonIcon="refresh-cw"
          onPress={() => app.syncNow({ announce: true })}
        />
      ) : null}
      {duplicates ? (
        <AlertBanner
          icon="copy"
          title="Дубли"
          text={duplicatesAlertText(duplicates)}
          button="Разобрать дубли"
          onPress={() => navigation.navigate('duplicates')}
        />
      ) : null}
      {alert && alert[0] ? (
        <AlertBanner
          icon="alert-triangle"
          title="Скоро возврат"
          text={returnAlertText(alert[0], alert[1])}
          button="Показать посылки"
          // at — чтобы фильтр выставился и при повторном переходе с теми же параметрами.
          onPress={() => navigation.navigate('stale', { minDays: ALERT_DAYS, at: Date.now() })}
        />
      ) : null}
      <View style={local.hero}>
        <View style={local.row}>
          <View style={[local.bubble, local.heroBubble]}>
            <Icon name="upload" size={22} color={colors.onPrimary} />
          </View>
          <View style={local.textCol}>
            <Text style={local.heroTitle}>Загрузите файл от Казакова 68</Text>
            <Text style={local.heroText}>Отчёт склада Ozon (.xlsx) из Загрузок, Telegram или почты.</Text>
          </View>
        </View>
        <Button kind="primary" icon="file-text" title="Выбрать отчёт" onPress={onImport} />
      </View>

      {latest ? (
        <>
          <Card>
            <View style={local.head}>
              <Text style={styles.cardTitle}>Результаты последнего импорта</Text>
              <View style={local.file}>
                <Icon name="file-text" size={14} color={colors.textMuted} />
                <Text style={[styles.mutedSmall, local.textCol]}>
                  {latest.source_file_name} · {formatTime(latest.started_at)}
                </Text>
              </View>
              {!isToday(latest.started_at)
                ? <Badge tone="warning" dot text="отчёт не сегодняшний — загрузите свежий" /> : null}
            </View>
            <StatTile hero label="Новых к отгрузке" value={latest.new_to_ship_rows} />
            <Tiles>
              <StatTile label="Всего строк" value={latest.total_rows} />
              <StatTile label="Наших" value={latest.matched_rows} />
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
                icon="download"
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
  row: { flexDirection: 'row', gap: 12 },
  textCol: { flex: 1 },
  bubble: { alignItems: 'center', justifyContent: 'center' },
  alert: {
    backgroundColor: colors.warningSoft, borderWidth: 1, borderColor: colors.warning, borderRadius: 18,
    padding: 14, gap: 12,
  },
  alertBubble: { width: 36, height: 36, borderRadius: 11, backgroundColor: colors.warning },
  alertTitle: { ...type.bodyStrong, color: colors.warningText, marginBottom: 3 },
  alertText: { ...type.caption, color: colors.warningText },
  hero: { backgroundColor: colors.ink, borderRadius: radius.xl, paddingTop: 20, paddingHorizontal: 18, paddingBottom: 18, gap: 14 },
  heroBubble: { width: 44, height: 44, borderRadius: 14, backgroundColor: colors.inkRaised },
  heroTitle: { ...type.h2, color: colors.onPrimary, marginBottom: 4 },
  heroText: { ...type.caption, color: colors.inkText },
  head: { gap: 6 },
  file: { flexDirection: 'row', alignItems: 'center', gap: 6 },
});
