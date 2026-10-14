/** Файлы пользователя: выбор отчёта, отправка выгрузки, проверка интернета. */
import * as DocumentPicker from 'expo-document-picker';
import { File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';

export const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

export interface PickedFile {
  name: string;
  bytes: Uint8Array;
}

/** Выбрать xlsx (Загрузки, Telegram, Диск…). null — пользователь отказался. */
export async function pickXlsx(): Promise<PickedFile | null> {
  const result = await DocumentPicker.getDocumentAsync({
    // Часть файловых менеджеров не знает MIME xlsx — допускаем и «любой файл».
    type: [XLSX_MIME, 'application/vnd.ms-excel', 'application/octet-stream', '*/*'],
    copyToCacheDirectory: true,
    multiple: false,
  });
  if (result.canceled || !result.assets?.length) return null;
  const asset = result.assets[0];
  const file = new File(asset.uri);
  try {
    return { name: asset.name, bytes: await file.bytes() };
  } finally {
    if (file.exists) file.delete(); // копия во временной папке больше не нужна
  }
}

/** Безопасное имя файла: без разделителей пути и управляющих символов. */
function safeName(name: string): string {
  return name.replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').slice(0, 120) || 'file.xlsx';
}

/** Сохранить во временную папку и открыть «Поделиться». Возвращает имя файла. */
export async function shareXlsx(bytes: Uint8Array, fileName: string, title: string): Promise<string> {
  const name = safeName(fileName);
  const file = new File(Paths.cache, name);
  if (file.exists) file.delete();
  file.write(bytes);
  if (!(await Sharing.isAvailableAsync())) {
    throw new Error('На этом устройстве нельзя поделиться файлом.');
  }
  await Sharing.shareAsync(file.uri, { mimeType: XLSX_MIME, dialogTitle: title });
  return name;
}

/**
 * Выбрать файл базы для восстановления. Он копируется в папку данных под
 * постоянным именем (обычный путь, как у самой базы); вызывающий удаляет его
 * по окончании. null — пользователь отказался.
 */
export async function pickDatabaseFile(dataDir: string): Promise<{ name: string; path: string } | null> {
  const result = await DocumentPicker.getDocumentAsync({
    // У файлов SQLite MIME обычно не определён: «любой файл», проверка — по содержимому.
    type: '*/*',
    copyToCacheDirectory: true,
    multiple: false,
  });
  if (result.canceled || !result.assets?.length) return null;
  const asset = result.assets[0];
  const picked = new File(asset.uri);
  try {
    const path = `${dataDir}/restore-picked.db`;
    const target = new File(`file://${path}`);
    if (target.exists) target.delete();
    picked.copy(target);
    return { name: asset.name, path };
  } finally {
    if (picked.exists) picked.delete(); // копия во временной папке больше не нужна
  }
}

/** Отправить копию базы («Поделиться»): Telegram, почта, компьютер. */
export async function shareDatabaseFile(path: string, title: string): Promise<void> {
  if (!(await Sharing.isAvailableAsync())) {
    throw new Error('На этом устройстве нельзя поделиться файлом.');
  }
  await Sharing.shareAsync(`file://${path}`, { mimeType: 'application/octet-stream', dialogTitle: title });
}

/** Отвечает ли интернет вообще (адрес, не связанный с Яндексом). */
export async function probeInternet(timeoutMs = 3000): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    await fetch('https://1.1.1.1/', { method: 'HEAD', signal: controller.signal });
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}
