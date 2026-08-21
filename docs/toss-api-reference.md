# 토스증권 오픈API 레퍼런스 (Phase 1에서 실제 사용하는 부분만)

출처: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json (직접 다운로드해 확인)

Base URL: `https://openapi.tossinvest.com`

## 인증

`POST /oauth2/token` (application/x-www-form-urlencoded, 인증 헤더 없음)

요청 바디:
- `grant_type=client_credentials`
- `client_id={TOSS_CLIENT_ID}`
- `client_secret={TOSS_CLIENT_SECRET}`

응답 (200):
```json
{"access_token": "...", "token_type": "Bearer", "expires_in": 86400}
```

이후 모든 호출에 `Authorization: Bearer {access_token}` 필요.
계좌 관련 호출(`accounts` 제외)은 추가로 `X-Tossinvest-Account: {accountSeq}` 헤더 필요.

에러 응답은 두 형식 중 하나:
- 토큰 엔드포인트: `{"error": "invalid_client", "error_description": "..."}`
- 그 외 엔드포인트: `{"error": {"code": "...", "message": "..."}}`

## 계좌 조회

`GET /api/v1/accounts` (Bearer만 필요)

응답 `result`: `[{"accountNo": "...", "accountSeq": 1, "accountType": "BROKERAGE"}]`

`accountSeq`를 이후 모든 계좌 관련 호출의 `X-Tossinvest-Account` 헤더값으로 사용.

## 보유 종목 / 당일 손익 조회

`GET /api/v1/holdings` (Bearer + X-Tossinvest-Account, 선택적 `symbol` 쿼리 파라미터)

응답 `result`:
```json
{
  "dailyProfitLoss": {"amount": "100000", "rate": "0.0141"},
  "items": [
    {
      "symbol": "AAPL",
      "quantity": "10",
      "marketValue": {"purchaseAmount": "1800", "amount": "1850", "amountAfterCost": "1845"}
    }
  ]
}
```

- `dailyProfitLoss.rate`: 계좌 전체 당일 손익률 (소수비율, 0.0141 = 1.41%, 음수면 손실)
- `items[].marketValue.amount`: 해당 종목의 시장 평가금액 (US 종목은 USD 단일 값)
- `items[].quantity`: 보유 수량 (decimal 문자열)

## 주문 생성

`POST /api/v1/orders` (Bearer + X-Tossinvest-Account)

시장가 매수(달러 금액 지정, US MARKET 전용):
```json
{"clientOrderId": "2026-08-21-AAPL-BUY", "symbol": "AAPL", "side": "BUY", "orderType": "MARKET", "orderAmount": "100.00"}
```

시장가 매도(수량 지정):
```json
{"clientOrderId": "2026-08-21-AAPL-SELL", "symbol": "AAPL", "side": "SELL", "orderType": "MARKET", "quantity": "10"}
```

응답 `result`: `{"orderId": "...", "clientOrderId": "..."}`

`clientOrderId`는 10분간 유효한 멱등성 키 — 동일 값으로 재요청하면 같은 주문 결과를 반환한다(중복 주문 방지에 사용).
