#!/usr/bin/env python3
"""One-time: generate a Lighter API key, register it with a signature from the <redacted:wallet-prefix> L1 key,
store it in macOS Keychain. Nothing secret is printed."""
import asyncio, json, sys, time, urllib.request
sys.path.insert(0, __file__.rsplit("/lighter/", 1)[0] + "/wallet")
import keyring, lighter
from signer import load_account, ADDRESS

BASE = "https://mainnet.zklighter.elliot.ai"
API_KEY_INDEX = 4          # 0-3 reserved for web/mobile
SERVICE = "chkm8-lighter-api"

def account_index():
    r = json.load(urllib.request.urlopen(f"{BASE}/api/v1/accountsByL1Address?l1_address={ADDRESS}"))
    assert r.get("code") == 200 and r.get("sub_accounts"), f"no Lighter account yet: {r}"
    return int(r["sub_accounts"][0]["index"])

async def main():
    idx = account_index()
    if keyring.get_password(SERVICE, f"{idx}:{API_KEY_INDEX}"):
        print("API key already stored for", idx, API_KEY_INDEX); return
    priv, pub, err = lighter.create_api_key()
    assert err is None, err
    client = lighter.SignerClient(url=BASE, account_index=idx, api_private_keys={API_KEY_INDEX: priv})
    eth_key = "0x" + load_account().key.hex().removeprefix("0x")
    resp, err = await client.change_api_key(eth_private_key=eth_key, new_pubkey=pub, api_key_index=API_KEY_INDEX)
    del eth_key
    assert err is None, err
    print("change_api_key response code:", getattr(resp, "code", resp))
    time.sleep(10)
    err = client.check_client()
    assert err is None, f"key check failed: {err}"
    keyring.set_password(SERVICE, f"{idx}:{API_KEY_INDEX}", priv)
    json.dump({"account_index": idx, "api_key_index": API_KEY_INDEX, "l1": ADDRESS},
              open(__file__.rsplit("/", 1)[0] + "/account.json", "w"))
    print(f"API key registered + verified + stored in Keychain ({SERVICE}) for account {idx}, key index {API_KEY_INDEX}")
    await client.close()

asyncio.run(main())
