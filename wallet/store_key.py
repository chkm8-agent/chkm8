# Move chosen vanity key into macOS Keychain (never printed / never on argv)
import json, sys, keyring
from eth_account import Account
target = sys.argv[1].lower()
key = None
for f in ["wallet/candidates.jsonl", "wallet/candidates_run1.json"]:
    try:
        txt = open(f).read()
        rows = [json.loads(l) for l in txt.splitlines() if l.strip()] if f.endswith("l") else json.loads(txt)
        for h in rows:
            if "0x" + h["address"] == target: key = h["key"]
    except FileNotFoundError: pass
assert key, "not found"
addr = Account.from_key(bytes.fromhex(key)).address
assert addr.lower() == target
keyring.set_password("chkm8-evm-hot-wallet", addr, key)
# verify round-trip
back = keyring.get_password("chkm8-evm-hot-wallet", addr)
assert Account.from_key(bytes.fromhex(back)).address == addr
print("stored + verified in Keychain:", addr)
