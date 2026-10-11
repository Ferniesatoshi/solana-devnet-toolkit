# Solana Devnet Toolkit

Small, read-only command-line utilities for checking account balances, RPC node health, and transactions on Solana Devnet. Uses Python's standard library: no package installation, wallet connection, private key, or SOL balance required.

## Requirements

Python 3.9 or newer. Network access is needed for live queries. Tests run offline.

## Quick start

From this repository's directory:

```sh
python3 toolkit.py health
python3 toolkit.py balance 11111111111111111111111111111111
python3 toolkit.py transaction YOUR_TRANSACTION_SIGNATURE
```

Replace `YOUR_TRANSACTION_SIGNATURE` with an actual base58 transaction signature. The balance example uses the public System Program address, not your wallet. Supply your public wallet address to inspect your balance.

All successful commands print JSON. Balance output includes integer `lamports`, an exact nine-decimal `sol` string, and the observation slot. Health reports the node's `getHealth` result and request latency; it does not certify every service in the cluster. Transaction output includes the raw RPC transaction and a Devnet explorer link when using the default endpoint. Inspect `transaction.meta.err` to distinguish a failed on-chain transaction from a successful one.

A transaction that is missing or not yet confirmed returns `found: false` and `transaction: null`; this is a successful lookup, not proof that the transaction never existed. Providers may prune old history.

## Token balances

```sh
python3 toolkit.py tokens YOUR_PUBLIC_ADDRESS TOKEN_MINT_ADDRESS
```

Provide a wallet owner and token mint on the selected cluster. The command sums all
matching token accounts, including accounts outside the associated token address.
It reports each account, the exact total `raw_amount` as a string, `decimals`, a
fixed-decimal `amount` string, and the observation slot. It never uses floating-point
UI amounts. The mint filter lets the RPC select the token program.

No matching accounts produces `raw_amount: "0"`, `amount: "0"`, `accounts: []`, and
`decimals: null` (the mint precision cannot be inferred from missing accounts).
An invalid or nonexistent mint can cause an RPC error rather than an empty result.
Amounts describe base token units, not prices or guaranteed spendable balances;
frozen accounts and token extensions can affect transferability or UI display.
No wallet keys or SOL are required. This command does not create accounts or transfer tokens.

## Options

Global options go **before** the command:

```sh
python3 toolkit.py --commitment finalized balance YOUR_PUBLIC_ADDRESS
python3 toolkit.py --timeout 30 health
python3 toolkit.py --url http://localhost:8899 health
```

The default endpoint is `https://api.devnet.solana.com`. Override it with `--url` or the `SOLANA_RPC_URL` environment variable. A custom endpoint can point to another cluster; its results reflect that cluster. Explorer links are omitted for custom endpoints to avoid misleading cluster labels.

Do not commit provider API keys. The program does not read `.env` files automatically. It does not print endpoint URLs or raw transport/server error messages, which can contain credentials. Only use trusted RPC providers, and prefer HTTPS for remote endpoints.

Exit codes: `0` for a successful query (including a missing transaction), `1` for input/RPC/transport errors, `2` for command-line usage errors. Errors go to stderr. Requests have a 15-second socket timeout by default. Rate-limited requests are not automatically retried; wait or choose another provider.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests mock network responses and cover request options, large integer precision, validation, health checks, transaction results, HTTP/RPC errors, and CLI output. Passing offline tests does not establish live endpoint availability.

## Scope

This toolkit never requests airdrops, signs transactions, or reads wallet keypair files. Devnet is for development. GitHub repositories and development activity **do not guarantee faucet eligibility**; faucet operators control their own requirements and rate limits.

Commits should describe actual changes using their real creation dates. Do not fabricate or backdate development history.

## RPC references

- [getBalance](https://solana.com/docs/rpc/http/getbalance)
- [getHealth](https://solana.com/docs/rpc/http/gethealth)
- [getTransaction](https://solana.com/docs/rpc/http/gettransaction)

- [getTokenAccountsByOwner](https://solana.com/docs/rpc/http/gettokenaccountsbyowner)
