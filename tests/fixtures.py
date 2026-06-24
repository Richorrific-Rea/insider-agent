"""
Synthetic XML fixtures for tests — no network access required.
"""

def make_form4_xml(
    ticker="ACME",
    issuer_name="Acme Corp",
    owner_name="Jane Doe",
    is_director="0",
    is_officer="1",
    is_ten_pct="0",
    officer_title="Chief Executive Officer",
    transactions=None,
):
    """
    Build a minimal ownershipDocument XML string.

    `transactions` is a list of dicts with keys:
      code, ad, shares, price, shares_following, date
    """
    if transactions is None:
        transactions = [
            dict(code="P", ad="A", shares="10000", price="15.00",
                 shares_following="50000", date="2024-01-15")
        ]

    txn_blocks = []
    for t in transactions:
        txn_blocks.append(f"""
        <nonDerivativeTransaction>
            <securityTitle><value>Common Stock</value></securityTitle>
            <transactionDate><value>{t['date']}</value></transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>{t['code']}</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares><value>{t['shares']}</value></transactionShares>
                <transactionPricePerShare><value>{t['price']}</value></transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>{t['ad']}</value></transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>{t['shares_following']}</value></sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>""")

    return f"""<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <issuer>
        <issuerCik>0001234567</issuerCik>
        <issuerName>{issuer_name}</issuerName>
        <issuerTradingSymbol>{ticker}</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0009876543</rptOwnerCik>
            <rptOwnerName>{owner_name}</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>{is_director}</isDirector>
            <isOfficer>{is_officer}</isOfficer>
            <isTenPercentOwner>{is_ten_pct}</isTenPercentOwner>
            <officerTitle>{officer_title}</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        {''.join(txn_blocks)}
    </nonDerivativeTable>
</ownershipDocument>"""


# Pre-built fixtures

XML_CEO_PURCHASE = make_form4_xml(
    ticker="AAPL",
    owner_name="Tim Cook",
    officer_title="Chief Executive Officer",
    transactions=[dict(code="P", ad="A", shares="5000", price="180.00",
                       shares_following="55000", date="2024-03-01")],
)

XML_CFO_SMALL_PURCHASE = make_form4_xml(
    ticker="AAPL",
    owner_name="Luca Maestri",
    officer_title="Chief Financial Officer",
    transactions=[dict(code="P", ad="A", shares="100", price="180.00",
                       shares_following="10100", date="2024-03-02")],
)

XML_DIRECTOR_SALE = make_form4_xml(
    ticker="MSFT",
    owner_name="John Director",
    is_director="1",
    is_officer="0",
    officer_title="",
    transactions=[dict(code="S", ad="D", shares="2000", price="400.00",
                       shares_following="8000", date="2024-03-03")],
)

XML_PRES_OPEN_MARKET = make_form4_xml(
    ticker="NVDA",
    owner_name="Jensen Huang",
    officer_title="President",
    transactions=[dict(code="P", ad="A", shares="10000", price="800.00",
                       shares_following="210000", date="2024-04-01")],
)

XML_MULTIPLE_TRANSACTIONS = make_form4_xml(
    ticker="GOOG",
    owner_name="Sundar Pichai",
    officer_title="Chief Executive Officer",
    transactions=[
        dict(code="P", ad="A", shares="1000", price="150.00",
             shares_following="51000", date="2024-05-01"),
        dict(code="S", ad="D", shares="500", price="155.00",
             shares_following="50500", date="2024-05-02"),
        dict(code="P", ad="A", shares="2000", price="148.00",
             shares_following="52500", date="2024-05-03"),
    ],
)

XML_NO_NONDERIVATIVE_TABLE = """<?xml version="1.0"?>
<ownershipDocument>
    <issuer>
        <issuerName>EmptyCorpUSA</issuerName>
        <issuerTradingSymbol>EMPT</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId><rptOwnerName>Nobody</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>0</isDirector><isOfficer>1</isOfficer>
            <isTenPercentOwner>0</isTenPercentOwner>
            <officerTitle>CEO</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <derivativeTable/>
</ownershipDocument>"""

XML_MALFORMED = "<ownershipDocument><unclosed>"

XML_TEN_PCT_OWNER = make_form4_xml(
    ticker="SMLL",
    owner_name="Big Investor",
    is_director="0",
    is_officer="0",
    is_ten_pct="1",
    officer_title="",
    transactions=[dict(code="P", ad="A", shares="50000", price="10.00",
                       shares_following="550000", date="2024-06-01")],
)
