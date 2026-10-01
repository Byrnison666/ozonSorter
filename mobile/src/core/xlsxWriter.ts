/**
 * Минимальная запись xlsx (один лист) со стилями. SheetJS Community не пишет
 * стили ячеек, а выгрузкам нужны заливки, жирная шапка и закреплённая строка.
 * Разметка повторяет то, что пишет openpyxl на ПК (inlineStr, PatternFill).
 */
import { strToU8, zipSync } from 'fflate';

export interface CellStyle {
  /** Цвет заливки RRGGBB. */
  fill?: string;
  bold?: boolean;
  wrap?: boolean;
  vertical?: 'top';
}

export interface WriteCell {
  /** null/undefined/'' — ячейка без значения (но со стилем, если задан). */
  value?: string | number | null;
  style?: CellStyle;
}

export interface SheetSpec {
  name: string;
  /** rows[i][j] — ячейка (i+1, j+1); undefined — ячейки нет. */
  rows: Array<Array<WriteCell | undefined>>;
  /** Ширины колонок с A. */
  widths?: number[];
  /** Сколько верхних строк закрепить. */
  freezeRows?: number;
}

const NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
const NS_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const NS_PKG_REL = 'http://schemas.openxmlformats.org/package/2006/relationships';
const XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';

function escapeAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Текст ячейки. Символы, запрещённые в XML 1.0, выбрасываются: openpyxl на ПК
 * на них падает (IllegalCharacterError), а файл должен открываться. Буквальный
 * «_xHHHH_» пишется как есть — так же делает ПК.
 */
function escapeText(s: string): string {
  return escapeAttr(s.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F￾￿]/g, ''));
}

export function columnLetter(index: number): string {
  let n = index + 1;
  let out = '';
  while (n > 0) {
    const rem = (n - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

class StyleTable {
  private readonly fills: string[] = [];
  private readonly xfs: string[] = ['']; // 0 — без стиля
  private readonly xfIndex = new Map<string, number>();

  id(style: CellStyle | undefined): number {
    if (!style || (!style.fill && !style.bold && !style.wrap && !style.vertical)) return 0;
    const key = JSON.stringify([style.fill ?? '', !!style.bold, !!style.wrap, style.vertical ?? '']);
    const known = this.xfIndex.get(key);
    if (known !== undefined) return known;
    let fillId = 0;
    if (style.fill) {
      const rgb = `00${style.fill.toUpperCase()}`;
      let i = this.fills.indexOf(rgb);
      if (i < 0) i = this.fills.push(rgb) - 1;
      fillId = i + 2; // 0 и 1 — обязательные none и gray125
    }
    const fontId = style.bold ? 1 : 0;
    const align = style.wrap || style.vertical
      ? `<alignment${style.vertical ? ` vertical="${style.vertical}"` : ''}${style.wrap ? ' wrapText="1"' : ''}/>`
      : '';
    const xf = `<xf numFmtId="0" fontId="${fontId}" fillId="${fillId}" borderId="0" xfId="0"` +
      `${fontId ? ' applyFont="1"' : ''}${fillId ? ' applyFill="1"' : ''}` +
      `${align ? ' applyAlignment="1">' + align + '</xf>' : '/>'}`;
    const id = this.xfs.push(xf) - 1;
    this.xfIndex.set(key, id);
    return id;
  }

  xml(): string {
    const fills = this.fills.map((rgb) =>
      `<fill><patternFill patternType="solid"><fgColor rgb="${rgb}"/><bgColor rgb="${rgb}"/></patternFill></fill>`);
    const xfs = ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>', ...this.xfs.slice(1)];
    return XML_HEAD +
      `<styleSheet xmlns="${NS_MAIN}">` +
      '<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>' +
      '<font><b val="1"/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>' +
      `<fills count="${2 + fills.length}"><fill><patternFill patternType="none"/></fill>` +
      `<fill><patternFill patternType="gray125"/></fill>${fills.join('')}</fills>` +
      '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>' +
      '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' +
      `<cellXfs count="${xfs.length}">${xfs.join('')}</cellXfs>` +
      '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>' +
      '</styleSheet>';
  }
}

function sheetXml(spec: SheetSpec, styles: StyleTable): string {
  const view = spec.freezeRows
    ? `<sheetView workbookViewId="0"><pane ySplit="${spec.freezeRows}" ` +
      `topLeftCell="A${spec.freezeRows + 1}" activePane="bottomLeft" state="frozen"/>` +
      '<selection pane="bottomLeft"/></sheetView>'
    : '<sheetView workbookViewId="0"/>';
  const cols = spec.widths?.length
    ? `<cols>${spec.widths.map((w, i) =>
      `<col min="${i + 1}" max="${i + 1}" width="${w}" customWidth="1"/>`).join('')}</cols>`
    : '';
  const rows = spec.rows.map((row, r) => {
    const cells = row.map((cell, c) => {
      if (!cell) return '';
      const ref = `${columnLetter(c)}${r + 1}`;
      const s = styles.id(cell.style);
      const sAttr = s ? ` s="${s}"` : '';
      const v = cell.value;
      if (typeof v === 'number') return `<c r="${ref}"${sAttr}><v>${v}</v></c>`;
      if (v === null || v === undefined) return `<c r="${ref}"${sAttr}/>`;
      if (v === '') return `<c r="${ref}"${sAttr} t="inlineStr"/>`;
      return `<c r="${ref}"${sAttr} t="inlineStr"><is><t xml:space="preserve">${escapeText(v)}</t></is></c>`;
    }).join('');
    return `<row r="${r + 1}">${cells}</row>`;
  }).join('');
  return XML_HEAD +
    `<worksheet xmlns="${NS_MAIN}" xmlns:r="${NS_REL}">` +
    `<sheetViews>${view}</sheetViews><sheetFormatPr defaultRowHeight="15"/>${cols}` +
    `<sheetData>${rows}</sheetData></worksheet>`;
}

export function writeXlsx(spec: SheetSpec): Uint8Array {
  const styles = new StyleTable();
  const sheet = sheetXml(spec, styles); // заполняет таблицу стилей
  const files: Record<string, string> = {
    '[Content_Types].xml': XML_HEAD +
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
      '<Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
      '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
      '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' +
      '</Types>',
    '_rels/.rels': XML_HEAD +
      `<Relationships xmlns="${NS_PKG_REL}">` +
      `<Relationship Id="rId1" Type="${NS_REL}/officeDocument" Target="xl/workbook.xml"/>` +
      '</Relationships>',
    'xl/workbook.xml': XML_HEAD +
      `<workbook xmlns="${NS_MAIN}" xmlns:r="${NS_REL}">` +
      '<bookViews><workbookView activeTab="0"/></bookViews>' +
      `<sheets><sheet name="${escapeAttr(spec.name)}" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    'xl/_rels/workbook.xml.rels': XML_HEAD +
      `<Relationships xmlns="${NS_PKG_REL}">` +
      `<Relationship Id="rId1" Type="${NS_REL}/worksheet" Target="worksheets/sheet1.xml"/>` +
      `<Relationship Id="rId2" Type="${NS_REL}/styles" Target="styles.xml"/>` +
      '</Relationships>',
    'xl/worksheets/sheet1.xml': sheet,
    'xl/styles.xml': styles.xml(),
  };
  const zipped: Record<string, Uint8Array> = {};
  for (const [name, content] of Object.entries(files)) zipped[name] = strToU8(content);
  return zipSync(zipped, { level: 6 });
}
