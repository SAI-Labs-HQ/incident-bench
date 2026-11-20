// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

contract Asset {
    string public symbol;
    uint8 public immutable decimals;
    constructor(string memory s, uint8 d) { symbol = s; decimals = d; }
}

contract TradingSuite {
    address public immutable vBTC;
    address public immutable vETH;
    address public immutable vUSD;
    constructor() {
        vBTC = address(new Asset("vBTC", 8));
        vETH = address(new Asset("vETH", 18));
        vUSD = address(new Asset("vUSD", 6));
    }
    function registry() external view returns (address) { return address(this); }
    function router() external view returns (address) { return address(this); }
    function pool() external view returns (address) { return address(this); }
    function quote(uint256 amount) external pure returns (uint256) { return amount; }
}
