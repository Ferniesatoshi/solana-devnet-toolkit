#!/usr/bin/env python3
"""Read-only Solana Devnet utilities using only Python's standard library."""
import argparse
import json
import math
import os
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEVNET_URL = 'https://api.devnet.solana.com'
ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


class ToolkitError(Exception):
    """An actionable input, transport, or RPC error."""


def validate_base58(value, size, label):
    if not isinstance(value, str) or not value or len(value) > size * 2:
        raise ToolkitError(f'{label} must be a base58-encoded {size}-byte value.')
    number = 0
    for char in value:
        if char not in ALPHABET:
            raise ToolkitError(f'{label} contains an invalid base58 character.')
        number = number * 58 + ALPHABET.index(char)
    leading = len(value) - len(value.lstrip('1'))
    if leading + (number.bit_length() + 7) // 8 != size:
        raise ToolkitError(f'{label} must decode to {size} bytes.')
    return value


def sol_string(lamports):
    if type(lamports) is not int or lamports < 0:
        raise ToolkitError('RPC returned an invalid lamport balance.')
    whole, fraction = divmod(lamports, 1_000_000_000)
    return f'{whole}.{fraction:09d}'


class RpcClient:
    def __init__(self, url=DEVNET_URL, timeout=15, opener=urlopen):
        try:
            parsed = urlparse(url)
            valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.fragment
            valid = valid and not parsed.username and not parsed.password
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ToolkitError('RPC URL must be HTTP(S), without embedded credentials or a fragment.')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ToolkitError('Timeout must be a positive finite number of seconds.')
        self.url, self.timeout, self.opener = url, timeout, opener

    def call(self, method, params=None):
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or []}
        request = Request(self.url, data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with self.opener(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            hint = ' Rate limited; wait before retrying or use another RPC provider.' if error.code == 429 else ''
            raise ToolkitError(f'RPC HTTP error {error.code}.{hint}') from None
        except (TimeoutError, socket.timeout):
            raise ToolkitError('RPC request timed out.') from None
        except (URLError, OSError):
            raise ToolkitError('Could not connect to the RPC endpoint; check your network and URL.') from None
        except (ValueError, UnicodeError):
            raise ToolkitError('RPC endpoint returned invalid JSON.') from None
        if not isinstance(result, dict) or result.get('jsonrpc') != '2.0' or result.get('id') != 1:
            raise ToolkitError('RPC endpoint returned an invalid response envelope.')
        if 'error' in result:
            error = result['error']
            code = error.get('code', 'unknown') if isinstance(error, dict) else 'unknown'
            # Do not echo arbitrary server text: it can contain private endpoint details.
            raise ToolkitError(f'RPC {method} failed (code {code}).')
        if 'result' not in result:
            raise ToolkitError('RPC response is missing its result.')
        return result['result']


def balance(client, address, commitment='confirmed'):
    validate_base58(address, 32, 'Address')
    result = client.call('getBalance', [address, {'commitment': commitment}])
    if not isinstance(result, dict) or not isinstance(result.get('context'), dict):
        raise ToolkitError('RPC returned an invalid balance response.')
    lamports = result.get('value')
    return {'address': address, 'lamports': lamports, 'sol': sol_string(lamports),
            'slot': result['context'].get('slot')}


def token_balance(client, owner, mint, commitment='confirmed'):
    """Sum all token accounts for one owner and mint, using exact integers."""
    validate_base58(owner, 32, 'Owner')
    validate_base58(mint, 32, 'Mint')
    result = client.call('getTokenAccountsByOwner', [owner, {'mint': mint}, {
        'commitment': commitment, 'encoding': 'jsonParsed'}])
    try:
        entries = result['value']
        slot = result['context']['slot']
        if not isinstance(entries, list) or type(slot) is not int or slot < 0:
            raise ValueError
        total, decimals, accounts = 0, None, []
        seen = set()
        for entry in entries:
            address = validate_base58(entry['pubkey'], 32, 'Token account')
            info = entry['account']['data']['parsed']['info']
            amount = info['tokenAmount']['amount']
            precision = info['tokenAmount']['decimals']
            if (info['owner'] != owner or info['mint'] != mint or address in seen
                    or not isinstance(amount, str) or not amount
                    or len(amount) > 20 or any(c not in '0123456789' for c in amount)
                    or int(amount) > 18446744073709551615
                    or type(precision) is not int or not 0 <= precision <= 255
                    or (decimals is not None and decimals != precision)):
                raise ValueError
            seen.add(address)
            decimals = precision
            total += int(amount)
            accounts.append({'address': address, 'raw_amount': amount})
    except (KeyError, TypeError, ValueError, ToolkitError):
        raise ToolkitError('RPC returned an invalid token account response.') from None
    if decimals is None:
        display = '0'
    elif decimals == 0:
        display = str(total)
    else:
        whole, fraction = divmod(total, 10 ** decimals)
        display = f'{whole}.{fraction:0{decimals}d}'
    return {'owner': owner, 'mint': mint, 'raw_amount': str(total),
            'decimals': decimals, 'amount': display, 'accounts': accounts, 'slot': slot}


def health(client):
    start = time.monotonic()
    status = client.call('getHealth')
    if status != 'ok':
        raise ToolkitError('RPC node did not report healthy status.')
    return {'status': status, 'latency_ms': round((time.monotonic() - start) * 1000, 2)}


def transaction(client, signature, commitment='confirmed'):
    validate_base58(signature, 64, 'Signature')
    result = client.call('getTransaction', [signature, {
        'commitment': commitment, 'encoding': 'json', 'maxSupportedTransactionVersion': 0}])
    if result is not None and not isinstance(result, dict):
        raise ToolkitError('RPC returned an invalid transaction response.')
    output = {'signature': signature, 'found': result is not None, 'transaction': result}
    if client.url == DEVNET_URL:
        output['explorer'] = f'https://explorer.solana.com/tx/{signature}?cluster=devnet'
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('SOLANA_RPC_URL', DEVNET_URL),
                        help='RPC endpoint (default: SOLANA_RPC_URL or public Devnet)')
    parser.add_argument('--timeout', type=float, default=15, help='Request timeout in seconds')
    parser.add_argument('--commitment', choices=('confirmed', 'finalized'), default='confirmed')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('balance', help='Account balance in lamports and SOL').add_argument('address')
    commands.add_parser('health', help='RPC node health and request latency')
    commands.add_parser('transaction', help='Look up a transaction').add_argument('signature')
    tokens = commands.add_parser('tokens', help='Token balance for a public owner and mint')
    tokens.add_argument('owner')
    tokens.add_argument('mint')
    args = parser.parse_args(argv)
    try:
        client = RpcClient(args.url, args.timeout)
        if args.command == 'balance':
            output = balance(client, args.address, args.commitment)
        elif args.command == 'tokens':
            output = token_balance(client, args.owner, args.mint, args.commitment)
        elif args.command == 'health':
            output = health(client)
        else:
            output = transaction(client, args.signature, args.commitment)
        print(json.dumps(output, indent=2))
        return 0
    except ToolkitError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
