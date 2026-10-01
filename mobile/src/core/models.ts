/** Значения перечислений в базе — как имена Enum в src/models.py. */
import { PyDateTime } from './py';

export const DeliveryPoint = {
  KOMSOMOLSKAYA_4: 'KOMSOMOLSKAYA_4',
  KOLTSEVAYA_16: 'KOLTSEVAYA_16',
} as const;
export type DeliveryPoint = (typeof DeliveryPoint)[keyof typeof DeliveryPoint];

export const POINT_LABELS: Record<DeliveryPoint, string> = {
  KOMSOMOLSKAYA_4: 'Комсомольская 4',
  KOLTSEVAYA_16: 'Кольцевая 16',
};

export function pointLabel(point: string | null | undefined): string {
  return point && point in POINT_LABELS ? POINT_LABELS[point as DeliveryPoint] : '—';
}

export const AssignmentStatus = {
  TO_ASSIGN: 'TO_ASSIGN',
  TO_SHIP: 'TO_SHIP',
  ON_POINT: 'ON_POINT',
  DELIVERED: 'DELIVERED',
  // Наша посылка, но в отчёте склада — возврат: в отгрузку не идёт.
  RETURNED: 'RETURNED',
  EXCLUDED_NOT_OURS: 'EXCLUDED_NOT_OURS',
  EXCLUDED_KTY: 'EXCLUDED_KTY',
} as const;
export type AssignmentStatus = (typeof AssignmentStatus)[keyof typeof AssignmentStatus];

/** Источник текущего времени; в тестах подменяется. */
export interface Clock {
  now(): PyDateTime;
}

export const systemClock: Clock = { now: () => PyDateTime.now() };
