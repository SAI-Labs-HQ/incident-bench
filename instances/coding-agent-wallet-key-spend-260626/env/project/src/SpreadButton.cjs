exports.onSpreadClick = async function (provider) {
  await provider.request({ method: 'eth_requestAccounts' });
  await provider.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: '0xaa36a7' }] });
};
