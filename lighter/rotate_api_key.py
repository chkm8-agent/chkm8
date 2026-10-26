#!/usr/bin/env python3
"""Rotate chkm8's Lighter API keys so trading happens only from the EU cluster.

The wallet key (<redacted:wallet-prefix>, Keychain chkm8-evm-hot-wallet) signs ChangePubKey transactions HERE on the Mac.
Nothing is sent from the Mac: the signed tx JSON is written to stdout-free files in $TMPDIR and then
submitted from the cluster (EU IP) by `submit` mode, which runs inside a pod.

  rotate_api_key.py sign-new  --index 5   -> new API key -> Keychain chkm8-lighter-api-cluster/<acct>:5,
                                               signed tx -> $TMPDIR/chkm8-cpk-5.json
  rotate_api_key.py sign-revoke --index 4 -> random throwaway key (never stored) for index 4,
                                               signed tx -> $TMPDIR/chkm8-cpk-4.json
  rotate_api_key.py submit FILE           -> (run in the cluster) POST /api/v1/sendTx
  rotate_api_key.py status                -> registered keys (public keys + nonces) for the account

Signed tx files contain the NEW PUBLIC key + signatures only: no private key, no wallet key.
"""
import argparse, asyncio, json, os, sys, urllib.request, urllib.parse

BASE = "https://mainnet.zklighter.elliot.ai"
ACCOUNT = <redacted:lighter-account>
TMP = os.environ.get("TMPDIR", "/tmp")


def get(path):
    with urllib.request.urlopen(f"{BASE}/api/v1/{path}", timeout=20) as r:
        return json.load(r)


def status():
    d = get(f"apikeys?account_index={ACCOUNT}&api_key_index=255")
    for k in d.get("api_keys", []):
        print(f"index {k['api_key_index']}: pub {k['public_key'][:16]}…  nonce {k['nonce']}")
    return {k["api_key_index"]: k["public_key"] for k in d.get("api_keys", [])}


async def sign(index, store):
    sys.path.insert(0, os.path.expanduser("~/chkm8/wallet"))
    import keyring, lighter
    from signer import load_account
    priv, pub, err = lighter.create_api_key()
    assert err is None, err
    # The SignerClient needs *a* key for `index` to build the tx; the new key works and sets the nonce lookup.
    client = lighter.SignerClient(url=BASE, account_index=ACCOUNT, api_private_keys={index: priv})
    nonce = int(get(f"nextNonce?account_index={ACCOUNT}&api_key_index={index}")["nonce"])
    eth = "0x" + load_account().key.hex().removeprefix("0x")
    tx_type, tx_info, tx_hash, err = client.sign_change_api_key(eth, pub, nonce=nonce, api_key_index=index)
    del eth
    await client.close()
    assert err is None, err
    out = f"{TMP}/chkm8-cpk-{index}.json"
    with open(out, "w") as f:
        json.dump({"tx_type": tx_type, "tx_info": tx_info, "tx_hash": tx_hash, "index": index, "pub": pub}, f)
    os.chmod(out, 0o600)
    if store:
        keyring.set_password("chkm8-lighter-api-cluster", f"{ACCOUNT}:{index}", priv)
        print(f"new API key for index {index} stored in Keychain chkm8-lighter-api-cluster")
    else:
        print(f"throwaway key for index {index} discarded (never stored)")
    del priv
    print(f"signed ChangePubKey: index {index}, nonce {nonce}, new pub {pub[:16]}…, tx {tx_hash[:16]}… -> {out}")


def submit(path):
    tx = json.load(open(path))
    body = urllib.parse.urlencode({"tx_type": tx["tx_type"], "tx_info": tx["tx_info"]}).encode()
    req = urllib.request.Request(f"{BASE}/api/v1/sendTx", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("sendTx:", r.read().decode()[:300])
    except urllib.error.HTTPError as e:
        print("sendTx HTTP", e.code, e.read().decode()[:300])
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sign-new", "sign-revoke", "submit", "status"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--index", type=int)
    a = ap.parse_args()
    if a.mode == "status": status()
    elif a.mode == "submit": submit(a.arg)
    else:
        assert a.index is not None and 2 < a.index < 255, "--index required (not 0-2, reserved for web/mobile)"
        asyncio.run(sign(a.index, store=(a.mode == "sign-new")))
