"""Load chkm8 hot wallet from macOS Keychain. Usage: from signer import load_account"""
import keyring
from eth_account import Account
SERVICE = "chkm8-evm-hot-wallet"
ADDRESS = "<redacted:wallet-address>"
def load_account():
    k = keyring.get_password(SERVICE, ADDRESS)
    if not k: raise RuntimeError("key not found in Keychain")
    acct = Account.from_key(bytes.fromhex(k))
    assert acct.address == ADDRESS
    return acct
