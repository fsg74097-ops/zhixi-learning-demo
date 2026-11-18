import assert from 'node:assert/strict';
import test from 'node:test';
import { findGraphDropTarget } from '../src/knowledgeGraph.ts';

test('a graph drop only selects the nearest other node within the node hit area', () => {
  const targets = [
    {id: 'first', position: {x: 100, y: 100}, radius: 40},
    {id: 'second', position: {x: 240, y: 140}, radius: 35},
    {id: 'third', position: {x: 285, y: 140}, radius: 35},
  ];
  assert.equal(findGraphDropTarget({x: 250, y: 140}, 'first', targets), 'second');
  assert.equal(findGraphDropTarget({x: 280, y: 140}, 'first', targets), 'third');
  assert.equal(findGraphDropTarget({x: 100, y: 100}, 'first', targets), undefined);
  assert.equal(findGraphDropTarget({x: 150, y: 170}, 'first', targets), undefined);
});
