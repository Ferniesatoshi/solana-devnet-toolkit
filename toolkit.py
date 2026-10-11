#!/usr/bin/env python3
"""Read-only Solana Devnet utilities using only Python's standard library."""
import argparse
from datetime import datetime, timezone
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
TOKEN_PROGRAMS = {
    'spl-token': 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
    'token-2022': 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb',
}
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
    return summarize_token_accounts(result, owner, mint)


def summarize_token_accounts(result, owner, mint):
    """Validate parsed accounts and return exact totals for one mint."""
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


def wallet_overview(client, owner, commitment='confirmed', limit=10):
    """Read SOL, both token programs, and recent direct-address activity."""
    validate_base58(owner, 32, 'Owner')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ToolkitError('Transaction limit must be between 1 and 100.')
    native = balance(client, owner, commitment)
    holdings, token_slots = [], {}
    for program_name, program_id in TOKEN_PROGRAMS.items():
        result = client.call('getTokenAccountsByOwner', [owner, {'programId': program_id}, {
            'commitment': commitment, 'encoding': 'jsonParsed'}])
        try:
            entries = result['value']
            slot = result['context']['slot']
            if not isinstance(entries, list) or type(slot) is not int or slot < 0:
                raise ValueError
            groups, seen = {}, set()
            for entry in entries:
                account = entry['account']
                parsed = account['data']['parsed']
                mint = validate_base58(parsed['info']['mint'], 32, 'Mint')
                address = validate_base58(entry['pubkey'], 32, 'Token account')
                if account['owner'] != program_id or parsed['type'] != 'account' or address in seen:
                    raise ValueError
                seen.add(address)
                groups.setdefault(mint, []).append(entry)
            for mint, accounts in sorted(groups.items()):
                summary = summarize_token_accounts({'value': accounts, 'context': {'slot': slot}}, owner, mint)
                summary['program'] = program_name
                holdings.append(summary)
            token_slots[program_name] = slot
        except (KeyError, TypeError, ValueError, ToolkitError):
            raise ToolkitError(f'RPC returned invalid {program_name} accounts.') from None
    recent = client.call('getSignaturesForAddress', [owner, {'commitment': commitment, 'limit': limit}])
    transactions = []
    try:
        if not isinstance(recent, list) or len(recent) > limit:
            raise ValueError
        for item in recent:
            signature = validate_base58(item['signature'], 64, 'Signature')
            slot, timestamp, error = item['slot'], item['blockTime'], item['err']
            if (type(slot) is not int or slot < 0
                    or (timestamp is not None and type(timestamp) is not int)
                    or (error is not None and not isinstance(error, (dict, str)))):
                raise ValueError
            transaction_info = {'signature': signature, 'slot': slot, 'block_time': timestamp,
                                'status': 'success' if error is None else 'failed', 'error': error,
                                'confirmation_status': item.get('confirmationStatus')}
            if client.url == DEVNET_URL:
                transaction_info['explorer'] = f'https://explorer.solana.com/tx/{signature}?cluster=devnet'
            transactions.append(transaction_info)
    except (KeyError, TypeError, ValueError, ToolkitError):
        raise ToolkitError('RPC returned an invalid recent transaction response.') from None
    return {'owner': owner, 'commitment': commitment, 'native_balance': native,
            'token_holdings': holdings, 'token_slots': token_slots,
            'recent_transactions': transactions, 'transaction_limit': limit}


def health(client):
    start = time.monotonic()
    status = client.call('getHealth')
    if status != 'ok':
        raise ToolkitError('RPC node did not report healthy status.')
    return {'status': status, 'latency_ms': round((time.monotonic() - start) * 1000, 2)}


def transaction(client, signature, commitment='confirmed', summary=False):
    validate_base58(signature, 64, 'Signature')
    result = client.call('getTransaction', [signature, {
        'commitment': commitment, 'encoding': 'json', 'maxSupportedTransactionVersion': 0}])
    if result is not None and not isinstance(result, dict):
        raise ToolkitError('RPC returned an invalid transaction response.')
    output = {'signature': signature, 'found': result is not None, 'transaction': result}
    if summary:
        output.pop('transaction')
        output['summary'] = summarize_transaction(result)
    if client.url == DEVNET_URL:
        output['explorer'] = f'https://explorer.solana.com/tx/{signature}?cluster=devnet'
    return output


def summarize_transaction(result):
    """Explain only facts reported by the RPC; missing metadata is unknown."""
    if result is None:
        return {'status': 'not_found', 'message': 'Not found at the requested commitment. '
                'It may be unconfirmed or unavailable in this provider history.'}
    try:
        slot, block_time, meta = result['slot'], result['blockTime'], result['meta']
        if type(slot) is not int or slot < 0:
            raise ValueError
        if block_time is not None and type(block_time) is not int:
            raise ValueError
        timestamp = (datetime.fromtimestamp(block_time, timezone.utc).isoformat()
                     if block_time is not None else None)
        output = {'slot': slot, 'block_time_utc': timestamp, 'fee_lamports': None,
                  'fee_sol': None, 'error': None}
        if meta is None:
            output.update(status='unknown', message='Transaction found, but execution metadata '
                          'is unavailable. Its outcome and fee are unknown.')
            return output
        error, fee = meta['err'], meta['fee']
        if error is not None and not isinstance(error, (dict, str)):
            raise ValueError
        output.update(fee_lamports=fee, fee_sol=sol_string(fee), error=error)
        if error is None:
            output.update(status='success', message='Transaction executed successfully.')
        else:
            message = 'Transaction failed. The reported fee was still charged.'
            if isinstance(error, dict) and 'InstructionError' in error:
                detail = error['InstructionError']
                if (isinstance(detail, list) and len(detail) == 2
                        and type(detail[0]) is int and detail[0] >= 0):
                    output['failed_instruction_index'] = detail[0]
                    message += f' Instruction {detail[0] + 1} reported an error; see the error field.'
            output.update(status='failed', message=message)
        return output
    except (KeyError, TypeError, ValueError, OverflowError, OSError, ToolkitError):
        raise ToolkitError('RPC returned invalid transaction summary data.') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('SOLANA_RPC_URL', DEVNET_URL),
                        help='RPC endpoint (default: SOLANA_RPC_URL or public Devnet)')
    parser.add_argument('--timeout', type=float, default=15, help='Request timeout in seconds')
    parser.add_argument('--commitment', choices=('confirmed', 'finalized'), default='confirmed')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('balance', help='Account balance in lamports and SOL').add_argument('address')
    commands.add_parser('health', help='RPC node health and request latency')
    tx = commands.add_parser('transaction', help='Look up a transaction')
    tx.add_argument('signature')
    tx.add_argument('--summary', action='store_true', help='Explain outcome, fee, and block time')
    tokens = commands.add_parser('tokens', help='Token balance for a public owner and mint')
    tokens.add_argument('owner')
    tokens.add_argument('mint')
    wallet = commands.add_parser('wallet', help='SOL, token holdings, and recent wallet activity')
    wallet.add_argument('owner')
    wallet.add_argument('--limit', type=int, default=10, help='Recent transactions (1-100; default 10)')
    args = parser.parse_args(argv)
    try:
        client = RpcClient(args.url, args.timeout)
        if args.command == 'balance':
            output = balance(client, args.address, args.commitment)
        elif args.command == 'tokens':
            output = token_balance(client, args.owner, args.mint, args.commitment)
        elif args.command == 'wallet':
            output = wallet_overview(client, args.owner, args.commitment, args.limit)
        elif args.command == 'health':
            output = health(client)
        else:
            output = transaction(client, args.signature, args.commitment, args.summary)
        print(json.dumps(output, indent=2))
        return 0
    except ToolkitError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
