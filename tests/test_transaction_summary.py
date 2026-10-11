import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch
from toolkit import DEVNET_URL, ToolkitError, main, summarize_transaction, transaction

SIGNATURE = '1' * 64


def result(error=None, fee=5000, block_time=0):
    return {'slot': 42, 'blockTime': block_time, 'meta': {'err': error, 'fee': fee}}


class TransactionSummaryTests(unittest.TestCase):
    def test_success_fee_and_utc(self):
        summary = summarize_transaction(result())
        self.assertEqual(summary['status'], 'success')
        self.assertEqual(summary['fee_lamports'], 5000)
        self.assertEqual(summary['fee_sol'], '0.000005000')
        self.assertEqual(summary['block_time_utc'], '1970-01-01T00:00:00+00:00')

    def test_failure_instruction_number_and_raw_error(self):
        error = {'InstructionError': [2, {'Custom': 6000}]}
        summary = summarize_transaction(result(error))
        self.assertEqual(summary['status'], 'failed')
        self.assertEqual(summary['error'], error)
        self.assertEqual(summary['failed_instruction_index'], 2)
        self.assertIn('Instruction 3', summary['message'])
        self.assertIn('fee was still charged', summary['message'])

    def test_non_instruction_failure(self):
        summary = summarize_transaction(result('AccountInUse'))
        self.assertEqual(summary['status'], 'failed')
        self.assertNotIn('failed_instruction_index', summary)

    def test_missing_transaction(self):
        self.assertEqual(summarize_transaction(None)['status'], 'not_found')

    def test_missing_metadata_is_unknown(self):
        summary = summarize_transaction({'slot': 1, 'blockTime': None, 'meta': None})
        self.assertEqual(summary['status'], 'unknown')
        self.assertIsNone(summary['fee_sol'])
        self.assertIsNone(summary['block_time_utc'])

    def test_nullable_time_and_large_fee_precision(self):
        summary = summarize_transaction(result(fee=9007199254740993, block_time=None))
        self.assertEqual(summary['fee_sol'], '9007199.254740993')
        self.assertIsNone(summary['block_time_utc'])

    def test_malformed_data(self):
        malformed = [{}, {'slot': 1, 'blockTime': 0, 'meta': {}}, result(fee=-1),
                     result(fee=True), result(block_time='bad'), result(block_time=10**100),
                     result(error=123)]
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ToolkitError):
                summarize_transaction(value)

    def test_opt_in_and_custom_endpoint(self):
        client = Mock(url='http://localhost:8899')
        client.call.return_value = result()
        raw = transaction(client, SIGNATURE)
        self.assertIn('transaction', raw)
        self.assertNotIn('summary', raw)
        summary = transaction(client, SIGNATURE, summary=True)
        self.assertNotIn('transaction', summary)
        self.assertNotIn('explorer', summary)
        self.assertTrue(summary['found'])

    def test_cli_summary(self):
        client = Mock(url=DEVNET_URL)
        client.call.return_value = result('AccountInUse')
        out = io.StringIO()
        with patch('toolkit.RpcClient', return_value=client), contextlib.redirect_stdout(out):
            self.assertEqual(main(['transaction', SIGNATURE, '--summary']), 0)
        parsed = json.loads(out.getvalue())
        self.assertEqual(parsed['summary']['status'], 'failed')
        self.assertIn('cluster=devnet', parsed['explorer'])
