const fs = require('node:fs');
const assert = require('node:assert/strict');
const { Contract, JsonRpcProvider } = require('ethers');

async function main() {
  const provider = new JsonRpcProvider(process.env.SEPOLIA_RPC_URL || 'http://sepolia-rpc:8545');
  assert.equal((await provider.getNetwork()).chainId, 11155111n);
  const m = JSON.parse(fs.readFileSync('contracts/addresses.sepolia.json', 'utf8'));
  for (const a of [m.registry, m.router, m.pool, ...Object.values(m.tokens || {})]) {
    assert.notEqual(await provider.getCode(a), '0x', `No bytecode at ${a}`);
  }
  const suite = new Contract(m.registry, [
    'function registry() view returns(address)', 'function router() view returns(address)',
    'function pool() view returns(address)', 'function quote(uint256) view returns(uint256)',
    'function vBTC() view returns(address)', 'function vETH() view returns(address)', 'function vUSD() view returns(address)',
  ], provider);
  assert.equal((await suite.registry()).toLowerCase(), m.registry.toLowerCase());
  assert.equal((await suite.router()).toLowerCase(), m.router.toLowerCase());
  assert.equal((await suite.pool()).toLowerCase(), m.pool.toLowerCase());
  assert.equal(await suite.quote(1000n), 1000n);
  for (const [symbol, decimals] of [['vBTC', 8n], ['vETH', 18n], ['vUSD', 6n]]) {
    assert.equal((await suite[symbol]()).toLowerCase(), m.tokens[symbol].toLowerCase());
    const token = new Contract(m.tokens[symbol], ['function symbol() view returns(string)', 'function decimals() view returns(uint8)'], provider);
    assert.equal(await token.symbol(), symbol);
    assert.equal(await token.decimals(), decimals);
  }
  const calls = [];
  const { onSpreadClick } = require('../src/SpreadButton.cjs');
  await onSpreadClick({ request: async q => { calls.push(q); return q.method === 'eth_requestAccounts' ? ['0x123'] : null; } });
  assert.deepEqual(calls.map(q => q.method), ['eth_requestAccounts', 'wallet_switchEthereumChain']);
  assert.equal(calls[1].params[0].chainId, '0xaa36a7');
  console.log(JSON.stringify({ integration_ok: true, registry: m.registry, readChecks: 10, button: 'connect-and-switch-only' }));
}
main().catch(e => { console.error(e.message); process.exitCode = 1; });
