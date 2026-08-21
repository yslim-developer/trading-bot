import time

import requests

from orchestrator.risk_engine import AccountState


class TossApiError(Exception):
    def __init__(self, status_code, code, message):
        self.status_code = status_code
        self.code = code
        super().__init__(f"Toss API error {status_code} {code}: {message}")


class TossClient:
    BASE_URL = "https://openapi.tossinvest.com"
    MAX_RETRIES = 2
    TIMEOUT_SECONDS = 10

    def __init__(self, client_id, client_secret, session=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self._access_token = None
        self._token_expires_at = 0.0
        self._account_seq = None

    def _request(self, method, path, headers=None, params=None, json=None, data=None):
        last_exc = None
        for _ in range(self.MAX_RETRIES + 1):
            try:
                resp = self.session.request(
                    method,
                    f"{self.BASE_URL}{path}",
                    headers=headers,
                    params=params,
                    json=json,
                    data=data,
                    timeout=self.TIMEOUT_SECONDS,
                )
                self._raise_for_status(resp)
                return resp.json()
            except TossApiError as exc:
                if exc.status_code < 500:
                    raise
                last_exc = exc
            except requests.RequestException as exc:
                last_exc = exc
        raise last_exc

    @staticmethod
    def _raise_for_status(resp):
        if resp.status_code < 400:
            return
        try:
            body = resp.json()
        except ValueError:
            body = {}
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code", "unknown")
            message = error.get("message", resp.text)
        else:
            code = str(error) if error else "unknown"
            message = body.get("error_description", resp.text)
        raise TossApiError(resp.status_code, code, message)

    def _get_access_token(self):
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        body = self._request(
            "POST",
            "/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        self._access_token = body["access_token"]
        self._token_expires_at = time.time() + body["expires_in"] - 60
        return self._access_token

    def _get_account_seq(self):
        if self._account_seq is None:
            body = self._request(
                "GET",
                "/api/v1/accounts",
                headers={"Authorization": f"Bearer {self._get_access_token()}"},
            )
            accounts = body["result"]
            if not accounts:
                raise TossApiError(200, "no-account", "조회된 계좌가 없습니다")
            self._account_seq = accounts[0]["accountSeq"]
        return self._account_seq

    def _auth_headers(self):
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "X-Tossinvest-Account": str(self._get_account_seq()),
        }

    def get_account_state(self, watchlist):
        body = self._request("GET", "/api/v1/holdings", headers=self._auth_headers())
        result = body["result"]
        positions_usd = {
            item["symbol"]: float(item["marketValue"]["amount"])
            for item in result["items"]
            if item["symbol"] in watchlist
        }
        daily_pnl_pct = float(result["dailyProfitLoss"]["rate"]) * 100
        return AccountState(positions_usd=positions_usd, daily_pnl_pct=daily_pnl_pct)

    def get_holding_quantity(self, ticker):
        body = self._request(
            "GET", "/api/v1/holdings", headers=self._auth_headers(), params={"symbol": ticker}
        )
        items = body["result"]["items"]
        return float(items[0]["quantity"]) if items else 0.0

    def place_market_buy(self, ticker, amount_usd, client_order_id):
        return self._place_order(
            {
                "clientOrderId": client_order_id,
                "symbol": ticker,
                "side": "BUY",
                "orderType": "MARKET",
                "orderAmount": f"{amount_usd:.2f}",
            }
        )

    def place_market_sell(self, ticker, quantity, client_order_id):
        return self._place_order(
            {
                "clientOrderId": client_order_id,
                "symbol": ticker,
                "side": "SELL",
                "orderType": "MARKET",
                "quantity": str(quantity),
            }
        )

    def _place_order(self, body):
        result = self._request(
            "POST", "/api/v1/orders", headers=self._auth_headers(), json=body
        )
        return result["result"]
