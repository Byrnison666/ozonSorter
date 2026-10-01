/**
 * Локальные файлы на телефоне (expo-file-system). Пути — обычные абсолютные
 * пути; для expo-file-system переводятся в file://.
 *
 * Замена файла — moveAsync из legacy-API: это File.renameTo, то есть rename(2)
 * с атомарной перезаписью. Новый API (File.move/rename) поверх существующего
 * файла не пишет. fsync Expo не даёт: при внезапном отключении питания сразу
 * после замены файл теоретически может оказаться пустым — остаётся копия в
 * backups/presync и версия на Диске.
 */
import { Directory, File } from 'expo-file-system';
import { moveAsync } from 'expo-file-system/legacy';

import type { LocalFiles } from '../core/sync/syncService';

const uri = (p: string) => (p.startsWith('file://') ? p : `file://${p}`);

export const expoFiles: LocalFiles = {
  exists: async (p) => new File(uri(p)).exists,
  read: (p) => new File(uri(p)).bytes(),
  readText: (p) => new File(uri(p)).text(),
  async write(p, data) {
    new File(uri(p)).write(data);
  },
  async replace(src, dst) {
    await moveAsync({ from: uri(src), to: uri(dst) });
  },
  async copy(src, dst) {
    const target = new File(uri(dst));
    if (target.exists) target.delete();
    new File(uri(src)).copy(target);
  },
  async remove(p) {
    const f = new File(uri(p));
    if (f.exists) f.delete();
  },
  async list(dir) {
    return new Directory(uri(dir)).list()
      .map((entry) => decodeURIComponent(entry.uri.replace(/\/+$/, '').split('/').pop() ?? ''));
  },
  async mkdirs(dir) {
    new Directory(uri(dir)).create({ intermediates: true, idempotent: true });
  },
  join: (dir, name) => `${dir.replace(/\/+$/, '')}/${name}`,
  dirname: (p) => p.slice(0, p.lastIndexOf('/')) || '/',
};
