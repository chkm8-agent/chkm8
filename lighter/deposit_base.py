#!/usr/bin/env python3
"""Deposit native USDC from the chkm8 wallet on Base into Lighter perps (via CCTP intent address).
Usage:  deposit_base.py <amount_usdc>            # dry run: checks + prints plan
        deposit_base.py <amount_usdc> --send     # actually sends
"""
import sys, time, json, urllib.request, urllib.parse
sys.path.insert(0, __file__.rsplit("/lighter/", 1)[0] + "/wallet")
from web3 import Web3
from signer import load_account, ADDRESS

RPC = "https://mainnet.base.org"
CHAIN_ID = 8453
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"   # native USDC on Base
LIGHTER = "https://mainnet.zklighter.elliot.ai/api/v1"
MIN_USDC = 5.0
ABI = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
        "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]},
       {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
        "inputs": [{"name": "to", "type": "address"}, {"name": "v", "type": "uint256"}], "outputs": [{"type": "bool"}]}]

def intent_address():
    data = urllib.parse.urlencode({"chain_id": CHAIN_ID, "from_addr": ADDRESS, "amount": 0,
                                   "is_external_deposit": "true"}).encode()
    got = set()
    for _ in range(2):  # must be deterministic
        r = json.load(urllib.request.urlopen(urllib.request.Request(f"{LIGHTER}/createIntentAddress", data=data)))
        assert r.get("code") == 200, r
        got.add(Web3.to_checksum_address(r["intent_address"]))
    assert len(got) == 1, f"intent address not stable: {got}"
    return got.pop()

def main():
    amount = float(sys.argv[1]); send = "--send" in sys.argv
    assert amount >= MIN_USDC, f"Lighter minimum is {MIN_USDC} USDC"
    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 20}))
    assert w3.eth.chain_id == CHAIN_ID
    usdc = w3.eth.contract(address=USDC, abi=ABI)
    bal = usdc.functions.balanceOf(ADDRESS).call(); eth = w3.eth.get_balance(ADDRESS)
    raw = int(round(amount * 1e6))
    to = intent_address()
    tx = usdc.functions.transfer(to, raw).build_transaction({
        "from": ADDRESS, "chainId": CHAIN_ID, "nonce": w3.eth.get_transaction_count(ADDRESS)})
    fee = tx["gas"] * tx["maxFeePerGas"]
    print(f"wallet {ADDRESS}\n  USDC {bal/1e6}  ETH {w3.from_wei(eth,'ether')}\n"
          f"deposit {amount} USDC -> intent {to} (Base)\n  est. max gas {w3.from_wei(fee,'ether')} ETH")
    assert bal >= raw, "insufficient USDC"
    assert eth >= fee, "insufficient ETH for gas on Base"
    if not send:
        print("dry run OK — rerun with --send"); return
    signed = load_account().sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    print("sent:", "https://basescan.org/tx/0x" + h.hex().removeprefix("0x"))
    rc = w3.eth.wait_for_transaction_receipt(h, timeout=180)
    print("status:", "success" if rc.status == 1 else "FAILED")
    for i in range(40):  # watch Lighter for account creation (~minutes)
        try:
            r = json.load(urllib.request.urlopen(f"{LIGHTER}/accountsByL1Address?l1_address={ADDRESS}"))
        except urllib.error.HTTPError as e:   # Lighter returns 400 + code 21100 until account exists
            r = json.loads(e.read() or b"{}")
        if r.get("code") == 200 and r.get("sub_accounts"):
            print("Lighter account live:", json.dumps(r["sub_accounts"][0])[:400]); return
        time.sleep(30)
    print("not credited yet — check again later")

if __name__ == "__main__":
    main()
