from forta_agent import FindingSeverity, FindingType, create_transaction_event
from .check_chainalysis_blocklist import provide_handle_transaction

handle_transaction = provide_handle_transaction()

class TestChainalysisBlocklistedAddressBot:
    def test_returns_empty_findings_if_no_blocklisted_address(self):
        tx_event = create_transaction_event({'addresses': {'0x9c1aec4fa72b7c3ff135999b2087868ec85d9ee2': True}})

        findings = handle_transaction(tx_event)

        assert len(findings) == 0

    def test_returns_finding_if_blocklisted_address_in_tx(self):
        blocklisted_address = '0x19aa5fe80d33a56d56c78e82ea5e50e5d80b4dff'
        wallet_tag = ''
        expected_description = f'Transaction involving a blocklisted address: {blocklisted_address}'
        tx_event = create_transaction_event({'addresses': {f'{blocklisted_address}': True}})

        findings = handle_transaction(tx_event)

        assert len(findings) == 1
        finding = findings[0]
        assert finding.name == "Blocklisted Address"
        assert finding.description == expected_description
        assert finding.alert_id == 'FORTA-BLOCKLIST-ADDR-TX'
        assert finding.type == FindingType.Suspicious
        assert finding.severity == FindingSeverity.High
        assert finding.metadata['blocklisted_address'] == blocklisted_address
        assert finding.metadata['wallet_tag'] == wallet_tag
        assert finding.metadata['data_source'] == 'Chainalysis-blocklist'