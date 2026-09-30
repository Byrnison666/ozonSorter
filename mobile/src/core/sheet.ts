import * as XLSX from 'xlsx';

import { type CellValue, fromExcel } from './py';

/**
 * Активный лист книги как таблица значений — так же, как их видит openpyxl
 * (load_workbook(data_only=True), iter_rows(values_only=True)): строки с первой,
 * колонки с A, короткие строки добиты None, ячейки с форматом даты — datetime.
 */
export function readActiveSheet(data: Uint8Array): CellValue[][] {
  const wb = XLSX.read(data, { type: 'array', cellNF: true, cellDates: false });
  // openpyxl: wb.active — вкладка, активная при сохранении, а не обязательно первая.
  // SheetJS кладёт <workbookView> в недокументированное поле WBView (не Views).
  const views = (wb.Workbook as { WBView?: Array<{ activeTab?: number }> } | undefined)?.WBView;
  const active = views?.[0]?.activeTab ?? 0;
  const name = wb.SheetNames[active] ?? wb.SheetNames[0];
  const ws = name === undefined ? undefined : wb.Sheets[name];
  if (!ws || !ws['!ref']) return [];

  const date1904 = Boolean(wb.Workbook?.WBProps?.date1904);
  const range = XLSX.utils.decode_range(ws['!ref']);
  const grid: CellValue[][] = [];
  for (let r = 0; r <= range.e.r; r++) {
    const row: CellValue[] = [];
    for (let c = 0; c <= range.e.c; c++) {
      row.push(cellValue(ws[XLSX.utils.encode_cell({ r, c })], date1904));
    }
    grid.push(row);
  }
  return grid;
}

function cellValue(cell: XLSX.CellObject | undefined, date1904: boolean): CellValue {
  if (!cell || cell.v === undefined) return null;
  switch (cell.t) {
    case 's':
      // Пустую ячейку-строку (<c t="inlineStr"/>) openpyxl читает как None, SheetJS —
      // как "". Пустую shared string openpyxl отдал бы как '', но по данным SheetJS
      // их не различить; выбран частый случай.
      return cell.v === '' ? null : String(cell.v);
    case 'b':
      return Boolean(cell.v);
    case 'e':
      // openpyxl отдаёт текст ошибки («#N/A»), SheetJS — код в v и текст в w.
      return cell.w ?? null;
    case 'n': {
      const v = cell.v as number;
      if (typeof cell.z === 'string' && XLSX.SSF.is_date(cell.z)) {
        try {
          return fromExcel(v, date1904);
        } catch {
          return '#VALUE!'; // openpyxl помечает так дату вне диапазона
        }
      }
      return v;
    }
    default:
      return null;
  }
}
