import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch
from toolkit import DEVNET_URL, TOKEN_PROGRAMS, ToolkitError, main, wallet_overview

OWNER = '1' * 32
MINT = 'So11111111111111111111111111111111111111112'
SIGNATURE = '1' * 64
LEGACY = TOKEN_PROGRAMS['spl-token']
EXTENDED = TOKEN_PROGRAMS['token-2022']


def account(address=MINT, mint=MINT, program=LEGACY, amount='1'):
    return {'pubkey': address, 'account': {'owner': program, 'data': {'parsed': {
        'type': 'account', 'info': {'owner': OWNER, 'mint': mint,
        'tokenAmount': {'amount': amount, 'decimals': 9}}}}}}


def fixture(legacy=None, extended=None, recent=None):
    client = Mock(url=DEVNET_URL)
    client.call.side_effect = [
        {'context': {'slot': 1}, 'value': 100},
        {'context': {'slot': 2}, 'value': legacy or []},
        {'context': {'slot': 3}, 'value': extended or []},
        recent or []]
    return client


def transaction(error=None, timestamp=None):
    return {'signature': SIGNATURE, 'slot': 1, 'blockTime': timestamp,
            'err': error, 'confirmationStatus': 'confirmed'}


class WalletTests(unittest.TestCase):
    def test_both_programs_and_exact_aggregation(self):
        client = fixture([account(amount='18446744073709551615'), account(OWNER)],
                         [account(EXTENDED, mint=EXTENDED, program=EXTENDED)])
        result = wallet_overview(client, OWNER, 'finalized', 5)
        self.assertEqual(result['native_balance']['sol'], '0.000000100')
        self.assertEqual(result['token_holdings'][0]['amount'], '18446744073.709551616')
        self.assertEqual(result['token_holdings'][1]['program'], 'token-2022')
        self.assertEqual(result['token_slots'], {'spl-token': 2, 'token-2022': 3})
        calls = client.call.call_args_list
        self.assertEqual(calls[1].args[1], [OWNER, {'programId': LEGACY},
            {'commitment': 'finalized', 'encoding': 'jsonParsed'}])
        self.assertEqual(calls[2].args[1][1], {'programId': EXTENDED})
        self.assertEqual(calls[3].args, ('getSignaturesForAddress', [OWNER,
            {'commitment': 'finalized', 'limit': 5}]))

    def test_empty_wallet(self):
        result = wallet_overview(fixture(), OWNER)
        self.assertEqual(result['token_holdings'], [])
        self.assertEqual(result['recent_transactions'], [])

    def test_success_failure_and_missing_time(self):
        result = wallet_overview(fixture(recent=[transaction(), transaction('AccountInUse', 123)]), OWNER)
        first, second = result['recent_transactions']
        self.assertEqual(first['status'], 'success')
        self.assertIsNone(first['block_time'])
        self.assertEqual(second['status'], 'failed')
        self.assertEqual(second['error'], 'AccountInUse')
        self.assertIn('cluster=devnet', first['explorer'])

    def test_custom_cluster_omits_explorer(self):
        client = fixture(recent=[transaction()])
        client.url = 'http://localhost:8899'
        self.assertNotIn('explorer', wallet_overview(client, OWNER)['recent_transactions'][0])

    def test_invalid_inputs_do_not_query(self):
        client = Mock()
        for limit in (0, 101, True, 1.5):
            with self.assertRaises(ToolkitError): wallet_overview(client, OWNER, limit=limit)
        with self.assertRaises(ToolkitError): wallet_overview(client, 'bad')
        client.call.assert_not_called()

    def test_invalid_program_or_duplicate_account(self):
        for entries in ([account(program=EXTENDED)], [account(), account()]):
            with self.assertRaises(ToolkitError): wallet_overview(fixture(entries), OWNER)

    def test_bad_token_data(self):
        bad = account()
        bad['account']['data'] = ['encoded-data', 'base64']
        with self.assertRaises(ToolkitError): wallet_overview(fixture([bad]), OWNER)

    def test_invalid_transaction_data(self):
        for field, value in [('signature', 'bad'), ('slot', True), ('blockTime', 'yesterday'), ('err', 1)]:
            item = transaction()
            item[field] = value
            with self.assertRaises(ToolkitError): wallet_overview(fixture(recent=[item]), OWNER)
        with self.assertRaises(ToolkitError):
            wallet_overview(fixture(recent=[transaction(), transaction()]), OWNER, limit=1)

    def test_rpc_failure_does_not_emit_partial_json(self):
        client = fixture()
        client.call.side_effect = [{'context': {'slot': 1}, 'value': 0}, ToolkitError('RPC unavailable')]
        out, err = io.StringIO(), io.StringIO()
        with patch('toolkit.RpcClient', return_value=client), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(['wallet', OWNER]), 1)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('RPC unavailable', err.getvalue())

    def test_cli_limit(self):
        client = fixture()
        out = io.StringIO()
        with patch('toolkit.RpcClient', return_value=client), contextlib.redirect_stdout(out):
            self.assertEqual(main(['wallet', OWNER, '--limit', '2']), 0)
        self.assertEqual(json.loads(out.getvalue())['transaction_limit'], 2)
