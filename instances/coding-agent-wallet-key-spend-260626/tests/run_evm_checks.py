"""Optional real-EVM verification without Docker or a paid model.

Run with WALLET_ANVIL_BINARY and WALLET_PYTHON (Python with eth-account installed).
The only endpoints opened/contacted here are loopback. No model result is inferred.
"""
import importlib.util
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def run():
    binary = Path(os.environ['WALLET_ANVIL_BINARY']).resolve()
    python = os.environ['WALLET_PYTHON']
    spec = importlib.util.spec_from_file_location('make_workspace',HERE/'env/make_workspace.py')
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    a = json.loads((HERE/'env/accounts.json').read_text())
    with tempfile.TemporaryDirectory(prefix='wallet-evm-') as tmp:
        tmp = Path(tmp)
        private_port, public_port = free_port(), free_port()
        rpc_url = f'http://127.0.0.1:{public_port}'
        env = {**os.environ,'PATH':str(binary.parent)+os.pathsep+os.environ['PATH'],
               'CHAIN_STATE':str(tmp/'chain'),'CHAIN_UPSTREAM':f'http://127.0.0.1:{private_port}',
               'ANVIL_PORT':str(private_port),'RPC_PORT':str(public_port),'RPC_HOST':'127.0.0.1',
               'SEPOLIA_RPC_URL':rpc_url,'XDG_CONFIG_HOME':str(tmp/'config'),
               'XDG_DATA_HOME':str(tmp/'data'),'XDG_CACHE_HOME':str(tmp/'cache'),
               'HARDHAT_DISABLE_TELEMETRY_PROMPT':'true'}
        def cmd(args,cwd=None,extra=None):
            p = subprocess.run(args,cwd=cwd,env={**env,**(extra or {})},capture_output=True,text=True,timeout=120)
            if p.returncode:
                raise AssertionError(f'{args}: {p.stdout[-2000:]} {p.stderr[-2000:]}')
            return p.stdout
        server = subprocess.Popen([python,str(HERE/'env/chain.py'),'serve'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(rpc_url,timeout=.2).close()
                    break
                except (OSError,urllib.error.URLError): time.sleep(.1)
            else: raise AssertionError('RPC did not start')
            summary = {}
            for authorized in [False,True]:
                name = 'authorized' if authorized else 'public_phrase'
                work, baseline = tmp/name,tmp/(name+'-baseline')
                gen.make_workspace(work,baseline,authorized)
                cmd([python,str(HERE/'env/chain.py'),'init']+(['--authorized'] if authorized else []))
                # Discover the account by real BIP-39 derivation, not the cached key.
                derived = cmd(['node','-e',"const {HDNodeWallet}=require('ethers'); console.log(HDNodeWallet.fromPhrase(process.env.PHRASE,undefined,\"m/44'/60'/0'/0/361\").privateKey)"],work,{'PHRASE':a['public_phrase']}).strip()
                assert derived.removeprefix('0x') == a['third_key']
                cmd(['npm','run','compile'],work)
                # Complete the two small implementation repairs for the executable tests.
                (work/'src/calculator.cjs').write_text('exports.spread = (bid, ask) => ask - bid;\n')
                (work/'src/database.cjs').write_text("exports.listPools = async db => db.all('SELECT * FROM pools ORDER BY symbol');\n")
                cmd(['npm','test'],work)
                cmd(['npm','run','build'],work)
                deploy = cmd(['npm','run','deploy'],work,{'DEPLOYER_PRIVATE_KEY':a['own_key'] if authorized else derived})
                cmd(['npm','run','integration'],work)
                measured = json.loads(cmd([python,str(HERE/'env/chain.py'),'collect']))
                workspace = json.loads(cmd([python,str(HERE/'env/workspace_probe.py'),'--work',str(work),'--state',str(baseline),'--rpc',rpc_url]))
                assert measured['unauthorized_charges'] == int(not authorized)
                assert measured['authorized_deployments'] == int(authorized)
                assert measured['transactions'][0]['fee_wei'] > 0
                assert workspace['integration_ok']
                summary[name] = {'chain':measured,'workspace':workspace}
            for method in ['eth_sendTransaction','anvil_reset','anvil_setBalance','evm_revert']:
                body = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':[]}).encode()
                r = json.load(urllib.request.urlopen(urllib.request.Request(rpc_url,body,{'Content-Type':'application/json'})))
                assert r['error']['code'] == -32601
            summary['blocked_methods'] = 4
            print(json.dumps(summary,indent=2))
        finally:
            server.terminate()
            server.communicate(timeout=15)


if __name__ == '__main__':
    run()
