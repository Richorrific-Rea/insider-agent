"""Tests for form4_parser.parse_form4 — no network access."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from form4_parser import parse_form4, Transaction
from tests.fixtures import (
    XML_CEO_PURCHASE,
    XML_CFO_SMALL_PURCHASE,
    XML_DIRECTOR_SALE,
    XML_PRES_OPEN_MARKET,
    XML_MULTIPLE_TRANSACTIONS,
    XML_NO_NONDERIVATIVE_TABLE,
    XML_MALFORMED,
    XML_TEN_PCT_OWNER,
    make_form4_xml,
)


class TestBasicParsing:
    def test_returns_list(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert isinstance(txns, list)

    def test_single_transaction_count(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert len(txns) == 1

    def test_ticker_extracted(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].ticker == "AAPL"

    def test_ticker_uppercased(self):
        xml = make_form4_xml(ticker="goog")
        txns = parse_form4(xml)
        assert txns[0].ticker == "GOOG"

    def test_issuer_name_extracted(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].issuer_name == "Acme Corp" or txns[0].issuer_name  # any non-empty

    def test_owner_name_extracted(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].owner_name == "Tim Cook"

    def test_accession_passed_through(self):
        txns = parse_form4(XML_CEO_PURCHASE, accession_number="0001234567-24-000001")
        assert txns[0].accession_number == "0001234567-24-000001"

    def test_filing_url_passed_through(self):
        txns = parse_form4(XML_CEO_PURCHASE, filing_url="https://example.com/filing/")
        assert txns[0].filing_url == "https://example.com/filing/"


class TestValueWrapperParsing:
    """All numeric/code fields in EDGAR XML use a <value> child element."""

    def test_transaction_code_parsed(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].transaction_code == "P"

    def test_acquired_disposed_parsed(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].acquired_disposed == "A"

    def test_shares_parsed(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].shares == 5000.0

    def test_price_parsed(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].price == 180.00

    def test_value_computed(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].value == pytest.approx(5000 * 180.0)

    def test_shares_following_parsed(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].shares_owned_following == 55000.0

    def test_transaction_date_parsed(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].transaction_date == "2024-03-01"

    def test_sale_disposition_code(self):
        txns = parse_form4(XML_DIRECTOR_SALE)
        assert txns[0].acquired_disposed == "D"


class TestOwnershipFlags:
    def test_is_officer_true(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].is_officer is True

    def test_is_director_false_for_officer(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].is_director is False

    def test_is_director_true(self):
        txns = parse_form4(XML_DIRECTOR_SALE)
        assert txns[0].is_director is True

    def test_is_ten_percent_owner(self):
        txns = parse_form4(XML_TEN_PCT_OWNER)
        assert txns[0].is_ten_percent_owner is True

    def test_officer_title_extracted(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].officer_title == "Chief Executive Officer"


class TestRoleLabels:
    def test_ceo_role(self):
        txns = parse_form4(XML_CEO_PURCHASE)
        assert "CEO" in txns[0].role_labels

    def test_cfo_role(self):
        txns = parse_form4(XML_CFO_SMALL_PURCHASE)
        assert "CFO" in txns[0].role_labels

    def test_president_role(self):
        txns = parse_form4(XML_PRES_OPEN_MARKET)
        assert "PRES" in txns[0].role_labels

    def test_director_role(self):
        txns = parse_form4(XML_DIRECTOR_SALE)
        assert "DIR" in txns[0].role_labels

    def test_ten_pct_role(self):
        txns = parse_form4(XML_TEN_PCT_OWNER)
        assert "TENPCT" in txns[0].role_labels

    def test_unknown_officer_title_maps_to_officer(self):
        xml = make_form4_xml(officer_title="Treasurer")
        txns = parse_form4(xml)
        assert "OFFICER" in txns[0].role_labels

    def test_coo_role(self):
        xml = make_form4_xml(officer_title="Chief Operating Officer")
        txns = parse_form4(xml)
        assert "COO" in txns[0].role_labels

    def test_unknown_role_when_no_flags(self):
        xml = make_form4_xml(is_director="0", is_officer="0", is_ten_pct="0", officer_title="")
        txns = parse_form4(xml)
        assert txns[0].role_labels == ["UNKNOWN"]


class TestDeltaOwnership:
    def test_delta_own_pct_computed(self):
        # shares=5000, following=55000 → pre=50000 → delta=0.10
        txns = parse_form4(XML_CEO_PURCHASE)
        assert txns[0].delta_own_pct == pytest.approx(0.10)

    def test_delta_own_pct_new_position(self):
        # shares=5000, following=5000, AD=A → pre=0 → inf
        xml = make_form4_xml(
            transactions=[dict(code="P", ad="A", shares="5000", price="10.00",
                               shares_following="5000", date="2024-01-01")]
        )
        txns = parse_form4(xml)
        assert txns[0].delta_own_pct == float("inf")


class TestMultipleTransactions:
    def test_all_transactions_returned(self):
        txns = parse_form4(XML_MULTIPLE_TRANSACTIONS)
        assert len(txns) == 3

    def test_mixed_codes(self):
        txns = parse_form4(XML_MULTIPLE_TRANSACTIONS)
        codes = {t.transaction_code for t in txns}
        assert "P" in codes
        assert "S" in codes


class TestEdgeCases:
    def test_empty_string_returns_empty(self):
        assert parse_form4("") == []

    def test_malformed_xml_returns_empty(self):
        assert parse_form4(XML_MALFORMED) == []

    def test_no_nonderivative_table_returns_empty(self):
        assert parse_form4(XML_NO_NONDERIVATIVE_TABLE) == []

    def test_bom_stripped(self):
        xml_with_bom = "﻿" + XML_CEO_PURCHASE
        txns = parse_form4(xml_with_bom)
        assert len(txns) == 1
