import assert from 'node:assert/strict';
import test from 'node:test';
import { evidenceIds, formatRate, formatScore, metricNote, metricValue } from '../src/presentation.ts';

test('zero is distinct from unobserved values and denominator is visible', () => {
  assert.equal(formatRate(0), '0%');
  assert.equal(formatRate(null), '未观察');
  assert.equal(formatScore(0, 2), '0 / 2');
  assert.equal(formatScore(null, null), '未评估');
  assert.match(metricNote({ n: 2, numerator: 1, denominator: 4, coverage: .5 }), /n=2/);
  assert.match(metricNote({ n: 2, numerator: 1, denominator: 4, coverage: .5 }), /50%/);
});

test('structured and legacy references identify the same evidence and omit invalid entries', () => {
  assert.deepEqual(evidenceIds(['R-1', { id: 'R-1' }, { evidence_id: 'K-2' }, { type: 'ITEM' }, '']), ['R-1', 'K-2']);
});

test('profile rate is displayed as a percent without hiding zero', () => {
  assert.equal(metricValue({ value: 0, rate: 0, numerator: 0, denominator: 4 }), '0%');
  assert.equal(metricValue({ value: 3 }), '3');
});
