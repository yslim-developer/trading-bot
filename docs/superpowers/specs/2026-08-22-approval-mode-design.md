# 반자동 승인 모드 — 설계

## 목표

완전자동 매매 대신, 리스크 엔진이 승인한 주문을 실행하기 전에 텔레그램으로 사용자 승인을 받는 옵션을 추가한다.
기본값은 꺼짐(기존 완전자동 동작 유지)이며, `config.yaml`에서 켤 수 있다.

## 설계

- `config.yaml`에 `approval: {enabled: false, timeout_minutes: 20}` 추가. 기존 config.yaml에 이 키가 없어도
  `load_config()`는 실패하지 않고 `enabled=false, timeout_minutes=20`으로 기본 동작한다.
- `orchestrator/notifier.py`에 `wait_for_telegram_reply(config, timeout_seconds)` 추가:
  - 시작 시 `getUpdates`를 한 번 호출해 현재 최신 `update_id`를 기록(이후 이 값보다 큰 업데이트만 처리 — 과거
    쌓인 메시지를 승인으로 오인하지 않도록).
  - `poll_interval_seconds` 간격으로 `getUpdates(offset=...)`를 반복 호출하며, `config.telegram_chat_id`에서 온
    새 텍스트 메시지를 확인한다.
  - 텍스트를 strip/lower한 뒤 `{"승인","approve","yes","y"}`에 속하면 `"approved"`,
    `{"거부","reject","no","n"}`에 속하면 `"rejected"`를 즉시 반환. 그 외 텍스트는 무시하고 계속 대기.
  - `timeout_seconds`가 지나도 응답이 없으면 `"timeout"`을 반환한다.
- `orchestrator/config.py`의 `AppConfig`에 `approval_enabled: bool`, `approval_timeout_minutes: int` 필드
  추가. `raw.get("approval", {})`로 읽어 섹션 자체가 없어도 기본값(`False`, `20`)으로 채운다.
- `orchestrator/main.py`: `evaluate_decisions()` 직후, `not dry_run and config.approval_enabled and
  any(r.approved for r in risk_results)`일 때만 승인 대기 플로우를 탄다:
  1. 제안 주문 목록(`summary_lines`에 이미 쌓인 내용)에 "20분 안에 승인/거부로 답장해주세요. 무응답 시 자동
     거부됩니다." 안내를 붙여 텔레그램 전송.
  2. `wait_for_telegram_reply(config, timeout_seconds=config.approval_timeout_minutes * 60)` 호출.
  3. `"approved"`면 기존 주문 실행 루프를 그대로 진행.
  4. `"rejected"`/`"timeout"`이면 주문 실행 루프를 건너뛴다(리스크 판단은 `record_order`로 이미 기록됨, 단
     `placed`는 남지 않음). 결과를 알리는 후속 텔레그램 메시지를 보낸다.
- 드라이런 모드이거나 승인 대상 주문이 하나도 없으면(전부 HOLD/거부) 승인 대기 없이 기존과 동일하게 동작한다.

## 테스트

`tests/test_notifier.py`에 `wait_for_telegram_reply`의 승인/거부/타임아웃/무관한메시지무시 케이스를
`requests.get`을 모킹해 테스트. `tests/test_main.py`에 승인모드 켰을 때 승인/거부 각각의 주문 실행 여부,
드라이런/모드꺼짐일 때 대기 플로우를 안 타는지 테스트.

## 범위 밖

종목별 개별 승인, 별도 스케줄러를 통한 지연 승인은 다루지 않는다(사용자가 명시적으로 범위 밖으로 결정).
