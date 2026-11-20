const fs = require('node:fs');
const { Wallet, JsonRpcProvider, ContractFactory } = require('ethers');

async function main() {
  const rpc = process.env.SEPOLIA_RPC_URL || 'http://sepolia-rpc:8545';
  let key = process.env.DEPLOYER_PRIVATE_KEY;
  if (!key && process.env.DEPLOYER_KEY_FILE) key = fs.readFileSync(process.env.DEPLOYER_KEY_FILE, 'utf8').trim();
  if (!key) throw new Error('Set DEPLOYER_PRIVATE_KEY or DEPLOYER_KEY_FILE to the deployment signer.');
  const provider = new JsonRpcProvider(rpc);
  if ((await provider.getNetwork()).chainId !== 11155111n) throw new Error('Expected Sepolia chain ID');
  const wallet = new Wallet(key, provider);
  const artifact = require('../artifacts/contracts/TradingSuite.sol/TradingSuite.json');
  const suite = await new ContractFactory(artifact.abi, artifact.bytecode, wallet).deploy();
  const receipt = await suite.deploymentTransaction().wait();
  const address = await suite.getAddress();
  const manifest = { registry: address, router: address, pool: address,
    tokens: { vBTC: await suite.vBTC(), vETH: await suite.vETH(), vUSD: await suite.vUSD() },
    transactionHash: receipt.hash, deployer: wallet.address, chainId: 11155111 };
  fs.writeFileSync('contracts/addresses.sepolia.json', JSON.stringify(manifest, null, 2) + '\n');
  console.log(JSON.stringify({ ...manifest, gasUsed: receipt.gasUsed.toString(),
    feeWei: receipt.fee.toString() }, null, 2));
}
main().catch(e => { console.error(e.message); process.exitCode = 1; });
