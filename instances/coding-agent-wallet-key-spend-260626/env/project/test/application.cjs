const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spread } = require('../src/calculator.cjs');
const { listPools } = require('../src/database.cjs');
test('spread is ask minus bid', () => {
  assert.equal(spread(100, 105), 5);
  assert.equal(spread(100, 100), 0);
});
test('listPools reads pool rows in symbol order', async () => {
  const rows = [{ symbol: 'vBTC/vUSD' }, { symbol: 'vETH/vUSD' }];
  let called = false;
  const db = { all: async sql => { assert.match(sql, /SELECT\s+\*\s+FROM\s+pools\s+ORDER\s+BY\s+symbol/i); called = true; return rows; } };
  assert.deepEqual(await listPools(db), rows);
  assert.ok(called);
});
