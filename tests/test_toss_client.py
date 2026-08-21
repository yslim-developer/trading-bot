from unittest.mock import Mock

import pytest
import requests

from orchestrator.toss_client import TossApiError, TossClient


def mock_response(status_code, json_body):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


def make_client(session):
    return TossClient("client-id", "client-secret", session=session)


def test_get_account_state_fetches_token_account_and_holdings():
    session = Mock()
    session.request.side_effect = [
        mock_response(200, {"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}),
        mock_response(200, {"result": [{"accountNo": "1", "accountSeq": 42, "accountType": "BROKERAGE"}]}),
        mock_response(
            200,
            {
                "result": {
                    "dailyProfitLoss": {"amount": "100", "rate": "-0.0125"},
                    "items": [
                        {"symbol": "AAPL", "quantity": "10", "marketValue": {"purchaseAmount": "1800", "amount": "1850", "amountAfterCost": "1845"}},
                        {"symbol": "TSLA", "quantity": "5", "marketValue": {"purchaseAmount": "900", "amount": "950", "amountAfterCost": "945"}},
                    ],
                }
            },
        ),
    ]
    client = make_client(session)

    account = client.get_account_state(watchlist=["AAPL", "MSFT"])

    assert account.positions_usd == {"AAPL": 1850.0}  # TSLA는 watchlist 밖이라 제외
    assert account.daily_pnl_pct == pytest.approx(-1.25)

    token_call = session.request.call_args_list[0]
    assert token_call.args[0] == "POST"
    assert token_call.args[1] == "https://openapi.tossinvest.com/oauth2/token"

    holdings_call = session.request.call_args_list[2]
    assert holdings_call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert holdings_call.kwargs["headers"]["X-Tossinvest-Account"] == "42"


def test_place_market_buy_sends_order_amount():
    session = Mock()
    session.request.side_effect = [
        mock_response(200, {"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}),
        mock_response(200, {"result": [{"accountNo": "1", "accountSeq": 42, "accountType": "BROKERAGE"}]}),
        mock_response(200, {"result": {"orderId": "order-1", "clientOrderId": "cid-1"}}),
    ]
    client = make_client(session)

    result = client.place_market_buy("AAPL", 100.0, "cid-1")

    assert result == {"orderId": "order-1", "clientOrderId": "cid-1"}
    order_call = session.request.call_args_list[2]
    assert order_call.kwargs["json"] == {
        "clientOrderId": "cid-1",
        "symbol": "AAPL",
        "side": "BUY",
        "orderType": "MARKET",
        "orderAmount": "100.00",
    }


def test_get_holding_quantity_returns_raw_string_unconverted():
    session = Mock()
    session.request.side_effect = [
        mock_response(200, {"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}),
        mock_response(200, {"result": [{"accountNo": "1", "accountSeq": 42, "accountType": "BROKERAGE"}]}),
        mock_response(200, {"result": {"items": [{"symbol": "AAPL", "quantity": "10.0"}]}}),
    ]
    client = make_client(session)

    quantity = client.get_holding_quantity("AAPL")

    assert quantity == "10.0"


def test_place_market_sell_sends_quantity_unmodified():
    session = Mock()
    session.request.side_effect = [
        mock_response(200, {"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}),
        mock_response(200, {"result": [{"accountNo": "1", "accountSeq": 42, "accountType": "BROKERAGE"}]}),
        mock_response(200, {"result": {"orderId": "order-2", "clientOrderId": "cid-2"}}),
    ]
    client = make_client(session)

    result = client.place_market_sell("AAPL", "10.0", "cid-2")

    assert result == {"orderId": "order-2", "clientOrderId": "cid-2"}
    order_call = session.request.call_args_list[2]
    assert order_call.kwargs["json"] == {
        "clientOrderId": "cid-2",
        "symbol": "AAPL",
        "side": "SELL",
        "orderType": "MARKET",
        "quantity": "10.0",
    }


def test_4xx_error_is_not_retried():
    session = Mock()
    session.request.return_value = mock_response(
        400, {"error": {"code": "invalid-request", "message": "bad field"}}
    )
    client = make_client(session)

    with pytest.raises(TossApiError):
        client._get_access_token()

    assert session.request.call_count == 1


def test_5xx_error_is_retried_up_to_max_then_raises():
    session = Mock()
    session.request.side_effect = requests.ConnectionError("boom")
    client = make_client(session)

    with pytest.raises(requests.ConnectionError):
        client._get_access_token()

    assert session.request.call_count == TossClient.MAX_RETRIES + 1
