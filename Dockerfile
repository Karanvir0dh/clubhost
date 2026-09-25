FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY clubhost ./clubhost
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-deps . \
    && useradd --create-home --uid 10001 clubhost \
    && mkdir /data && chown clubhost:clubhost /data
USER clubhost
ENV CLUBHOST_DATA=/data
EXPOSE 8000
CMD ["uvicorn", "clubhost.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
