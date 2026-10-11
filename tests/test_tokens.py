import contextlib
import copy
import io
import json
import unittest
from unittest.mock import Mock, patch
from toolkit import ToolkitError, main, token_balance

OWNER = '1' * 32
MINT = 'So11111111111111111111111111111111111111112'
ACCOUNT = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'


def entry(address=ACCOUNT, amount='100', decimals=9):
    return {'pubkey': address, 'account': {'data': {'parsed': {'info': {
        'owner': OWNER, 'mint': MINT,
        'tokenAmount': {'amount': amount, 'decimals': decimals, 'uiAmount': None}}}}}}


def client_for(entries):
    client = Mock()
    client.call.return_value = {'context': {'slot': 10}, 'value': entries}
    return client


class TokenTests(unittest.TestCase):
    def test_sum_exact_large_amounts_and_request(self):
        client = client_for([entry(amount='18446744073709551615'), entry(MINT, '1')])
        result = token_balance(client, OWNER, MINT, 'finalized')
        self.assertEqual(result['raw_amount'], '18446744073709551616')
        self.assertEqual(result['amount'], '18446744073.709551616')
        self.assertEqual(len(result['accounts']), 2)
        client.call.assert_called_once_with('getTokenAccountsByOwner', [OWNER, {'mint': MINT},
            {'commitment': 'finalized', 'encoding': 'jsonParsed'}])

    def test_empty_accounts(self):
        result = token_balance(client_for([]), OWNER, MINT)
        self.assertEqual(result['amount'], '0')
        self.assertIsNone(result['decimals'])
        self.assertEqual(result['accounts'], [])

    def test_zero_precision_and_tiny_balance(self):
        self.assertEqual(token_balance(client_for([entry(decimals=0)]), OWNER, MINT)['amount'], '100')
        self.assertEqual(token_balance(client_for([entry(amount='1')]), OWNER, MINT)['amount'], '0.000000001')

    def test_invalid_inputs_never_call_rpc(self):
        client = Mock()
        for owner, mint in [('bad', MINT), (OWNER, 'bad')]:
            with self.assertRaises(ToolkitError): token_balance(client, owner, mint)
        client.call.assert_not_called()

    def test_invalid_amounts_and_decimals(self):
        for amount in ('-1', '1.1', '1e2', '', '１２', None, 1, '18446744073709551616'):
            with self.assertRaises(ToolkitError):
                token_balance(client_for([entry(amount=amount)]), OWNER, MINT)
        for decimals in (-1, 256, True, '9'):
            with self.assertRaises(ToolkitError):
                token_balance(client_for([entry(decimals=decimals)]), OWNER, MINT)

    def test_reject_mismatched_or_duplicate_accounts(self):
        variants = [[entry(), entry()], [entry(), entry(MINT, decimals=6)]]
        for field in ('owner', 'mint'):
            bad = copy.deepcopy(entry())
            bad['account']['data']['parsed']['info'][field] = ACCOUNT
            variants.append([bad])
        for entries in variants:
            with self.assertRaises(ToolkitError): token_balance(client_for(entries), OWNER, MINT)

    def test_malformed_response(self):
        for result in (None, {}, {'value': None, 'context': {'slot': 1}},
                       {'value': [None], 'context': {'slot': 1}}):
            client = Mock()
            client.call.return_value = result
            with self.assertRaises(ToolkitError): token_balance(client, OWNER, MINT)

    def test_cli(self):
        out = io.StringIO()
        with patch('toolkit.RpcClient', return_value=client_for([])), contextlib.redirect_stdout(out):
            self.assertEqual(main(['tokens', OWNER, MINT]), 0)
        self.assertEqual(json.loads(out.getvalue())['raw_amount'], '0')
