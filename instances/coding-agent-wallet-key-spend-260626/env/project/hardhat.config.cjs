const { subtask } = require('hardhat/config');
const { TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD } = require('hardhat/builtin-tasks/task-names');
// The compiler is installed with the project so builds work without a download.
subtask(TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD).setAction(async ({ solcVersion }) => ({
  compilerPath: require.resolve('solc/soljson.js'), isSolcJs: true,
  version: solcVersion, longVersion: require('solc').version(),
}));
module.exports = { solidity: { version: '0.8.26', settings: { evmVersion: 'paris', optimizer: { enabled: true, runs: 200 } } } };
