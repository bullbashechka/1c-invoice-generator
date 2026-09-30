FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
 && python -m pip install --no-cache-dir \
    https://github.com/explosion/spacy-models/releases/download/ru_core_news_md-3.8.0/ru_core_news_md-3.8.0-py3-none-any.whl

COPY onec_mcp_toolkit_proxy/ ./onec_mcp_toolkit_proxy/

ENV PORT=6003
ENV TIMEOUT=180
ENV LOG_LEVEL=INFO

EXPOSE 6003

CMD ["python", "-m", "onec_mcp_toolkit_proxy"]
