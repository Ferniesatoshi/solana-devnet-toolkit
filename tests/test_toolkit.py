import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from toolkit import RpcClient, ToolkitError, balance, health, main, sol_string, transaction, validate_base58

ADDRESS = '1' * 32
SIGNATURE = '1' * 64

def response(result):
    return io.BytesIO(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result}).encode())

class ToolkitTests(unittest.TestCase):
    def test_balance_precision_and_request(self):
        opener = Mock(return_value=response({'context': {'slot': 12}, 'value': 18446744073709551615}))
        result = balance(RpcClient(opener=opener), ADDRESS, 'finalized')
        self.assertEqual(result['sol'], '18446744073.709551615')
        self.assertEqual(json.loads(opener.call_args.args[0].data)['params'], [ADDRESS, {'commitment': 'finalized'}])

    def test_small_and_invalid_balances(self):
        self.assertEqual(sol_string(100), '0.000000100')
        self.assertEqual(sol_string(0), '0.000000000')
        for value in (-1, True, 1.5, None):
            with self.assertRaises(ToolkitError): sol_string(value)

    def test_base58(self):
        self.assertEqual(validate_base58(ADDRESS, 32, 'Address'), ADDRESS)
        for value in ('0' * 32, '1' * 31, '1' * 33, '', 'z' * 500):
            with self.assertRaises(ToolkitError): validate_base58(value, 32, 'Address')

    def test_invalid_address_never_calls_network(self):
        client = Mock()
        with self.assertRaises(ToolkitError): balance(client, 'invalid')
        client.call.assert_not_called()

    def test_health(self):
        self.assertEqual(health(RpcClient(opener=Mock(return_value=response('ok'))))['status'], 'ok')
        with self.assertRaises(ToolkitError): health(RpcClient(opener=Mock(return_value=response('behind'))))

    def test_missing_transaction_and_version_options(self):
        opener = Mock(return_value=response(None))
        result = transaction(RpcClient(opener=opener), SIGNATURE)
        self.assertFalse(result['found'])
        self.assertIsNone(result['transaction'])
        self.assertIn('cluster=devnet', result['explorer'])
        options = json.loads(opener.call_args.args[0].data)['params'][1]
        self.assertEqual(options['maxSupportedTransactionVersion'], 0)
        self.assertEqual(options['encoding'], 'json')

    def test_failed_transaction_preserved(self):
        tx = {'slot': 2, 'meta': {'err': {'InstructionError': [0, 'InvalidArgument']}}}
        result = transaction(RpcClient('http://localhost:8899', opener=Mock(return_value=response(tx))), SIGNATURE)
        self.assertTrue(result['found'])
        self.assertEqual(result['transaction'], tx)
        self.assertNotIn('explorer', result)

    def test_transport_errors_redacted(self):
        for error in (HTTPError('https://rpc/?key=SECRET', 429, 'SECRET', {}, None), URLError('SECRET'), TimeoutError('SECRET')):
            with self.assertRaises(ToolkitError) as caught: RpcClient(opener=Mock(side_effect=error)).call('getHealth')
            self.assertNotIn('SECRET', str(caught.exception))

    def test_bad_envelopes_and_rpc_errors(self):
        for data in (b'not json', b'[]', b'{}', b'{"jsonrpc":"2.0","id":2,"result":"ok"}', b'{"jsonrpc":"2.0","id":1,"error":{"code":-32005,"message":"SECRET"}}'):
            with self.assertRaises(ToolkitError) as caught: RpcClient(opener=Mock(return_value=io.BytesIO(data))).call('getHealth')
            self.assertNotIn('SECRET', str(caught.exception))

    def test_endpoint_and_timeout_validation(self):
        for url in ('file:///etc/passwd', 'https://', 'https://user:pass@rpc.example', 'https://rpc.example/#key', 'https://rpc.example:bad'):
            with self.assertRaises(ToolkitError): RpcClient(url)
        for timeout in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ToolkitError): RpcClient(timeout=timeout)

    def test_cli_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err): self.assertEqual(main(['balance', 'bad']), 1)
        self.assertIn('Error:', err.getvalue())

    def test_cli_json_output(self):
        out = io.StringIO()
        with patch('toolkit.RpcClient') as client, contextlib.redirect_stdout(out):
            client.return_value.call.return_value = 'ok'
            self.assertEqual(main(['health']), 0)
        self.assertEqual(json.loads(out.getvalue())['status'], 'ok')

if __name__ == '__main__': unittest.main()
