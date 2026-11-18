# vendor/tradingagents/Dockerfile 과 같은 구성 + 봇 스크립트를 이미지에 직접 포함.
# 예전엔 run_analysis.py/decision_mapping.py 를 compose 바인드 마운트로 넣었는데,
# Docker Desktop 이 E: 드라이브를 실제 폴더 대신 빈 스텁 디렉터리로 마운트해
# (/app/run_analysis.py 가 디렉터리로 보임) 모든 종목 분석이 즉시 실패했다.
# 빌드 컨텍스트는 Windows 클라이언트가 직접 보내므로 드라이브 공유와 무관하다.
FROM python:3.13-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY vendor/tradingagents /build
RUN pip install --no-cache-dir /build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents
USER appuser
WORKDIR /home/appuser/app
COPY docker/run_analysis.py orchestrator/decision_mapping.py /app/
